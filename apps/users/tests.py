from datetime import timedelta
from io import StringIO
from types import SimpleNamespace

from django.core.cache import cache
from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone
from rest_framework_simplejwt.tokens import RefreshToken

from apps.errors import ServiceError

from . import services
from .models import OneTimeCode, ParentLink, User
from .permissions import IsAdminRole, IsParent, IsStudent


def make_user(phone: str, role: str = User.Role.STUDENT, **extra) -> User:
    return User.objects.create(username=phone, phone=phone, role=role, **extra)


class NormalizePhoneTests(TestCase):
    def test_formats(self):
        for raw in ["+998 90 123-45-67", "998901234567", "901234567", "(90) 123 45 67"]:
            self.assertEqual(services.normalize_phone(raw), "+998901234567")

    def test_invalid(self):
        for raw in ["", "12345", "abc"]:
            with self.assertRaises(ServiceError):
                services.normalize_phone(raw)


class UserModelTests(TestCase):
    def test_staff_follows_role(self):
        user = make_user("+998900000001")
        self.assertFalse(user.is_staff)
        user.role = User.Role.ADMIN
        user.save(update_fields=["role"])
        user.refresh_from_db()
        self.assertTrue(user.is_staff)
        user.role = User.Role.STUDENT
        user.save()
        self.assertFalse(user.is_staff)

    def test_superuser_stays_staff(self):
        admin = User.objects.create_superuser("root", password="x")
        self.assertTrue(admin.is_staff)
        self.assertTrue(admin.is_admin_role)

    def test_empty_phone_not_unique(self):
        User.objects.create(username="a")
        User.objects.create(username="b")  # ikkalasida phone="" — xato bo'lmasligi kerak


class BotUserTests(TestCase):
    def test_creates_student(self):
        user, created = services.get_or_create_bot_user(
            telegram_id=111, phone="901234567", first_name="Ali"
        )
        self.assertTrue(created)
        self.assertEqual(
            (user.role, user.phone, user.first_name), ("student", "+998901234567", "Ali")
        )
        self.assertFalse(user.has_usable_password())

    def test_returns_existing_by_telegram_id(self):
        first, _ = services.get_or_create_bot_user(telegram_id=111, phone="901234567")
        again, created = services.get_or_create_bot_user(telegram_id=111, phone="901234567")
        self.assertFalse(created)
        self.assertEqual(first.pk, again.pk)

    def test_attaches_preregistered_admin(self):
        admin = make_user("+998901234567", role=User.Role.ADMIN)
        user, created = services.get_or_create_bot_user(telegram_id=222, phone="901234567")
        self.assertFalse(created)
        self.assertEqual(user.pk, admin.pk)
        self.assertEqual((user.telegram_id, user.role), (222, "admin"))

    def test_phone_of_other_telegram_account(self):
        services.get_or_create_bot_user(telegram_id=111, phone="901234567")
        with self.assertRaises(ServiceError):
            services.get_or_create_bot_user(telegram_id=999, phone="901234567")


class LoginCodeTests(TestCase):
    def setUp(self):
        self.user = make_user("+998901234567")

    def test_code_format_and_ttl(self):
        otc = services.issue_code(self.user, OneTimeCode.Purpose.LOGIN)
        self.assertRegex(otc.code, r"^\d{6}$")
        self.assertAlmostEqual((otc.expires_at - timezone.now()).total_seconds(), 600, delta=5)

    def test_verify_success_is_single_use(self):
        otc = services.issue_code(self.user, OneTimeCode.Purpose.LOGIN)
        self.assertEqual(services.verify_login_code("901234567", otc.code), self.user)
        with self.assertRaises(ServiceError):
            services.verify_login_code("901234567", otc.code)

    def test_new_code_expires_old(self):
        old = services.issue_code(self.user, OneTimeCode.Purpose.LOGIN)
        new = services.issue_code(self.user, OneTimeCode.Purpose.LOGIN)
        if old.code != new.code:
            with self.assertRaises(ServiceError):
                services.verify_login_code(self.user.phone, old.code)
        self.assertEqual(services.verify_login_code(self.user.phone, new.code), self.user)

    def test_five_wrong_attempts_kill_code(self):
        otc = services.issue_code(self.user, OneTimeCode.Purpose.LOGIN)
        wrong = "000000" if otc.code != "000000" else "111111"
        for _ in range(OneTimeCode.MAX_ATTEMPTS):
            with self.assertRaises(ServiceError):
                services.verify_login_code(self.user.phone, wrong)
        otc.refresh_from_db()
        self.assertEqual(otc.attempts, OneTimeCode.MAX_ATTEMPTS)
        with self.assertRaises(ServiceError):
            services.verify_login_code(self.user.phone, otc.code)

    def test_expired_code(self):
        otc = services.issue_code(self.user, OneTimeCode.Purpose.LOGIN)
        OneTimeCode.objects.filter(pk=otc.pk).update(expires_at=timezone.now())
        with self.assertRaises(ServiceError):
            services.verify_login_code(self.user.phone, otc.code)

    def test_inactive_user(self):
        otc = services.issue_code(self.user, OneTimeCode.Purpose.LOGIN)
        User.objects.filter(pk=self.user.pk).update(is_active=False)
        with self.assertRaises(ServiceError):
            services.verify_login_code(self.user.phone, otc.code)

    def test_bad_phone_gives_generic_error(self):
        with self.assertRaises(ServiceError) as ctx:
            services.verify_login_code("x", "123456")
        self.assertEqual(ctx.exception.code, "invalid_code")


class ParentInviteTests(TestCase):
    def setUp(self):
        self.student = make_user("+998900000001")
        self.parent = make_user("+998900000002", role=User.Role.PARENT)

    def test_invite_and_accept(self):
        invite = services.create_parent_invite(self.student)
        self.assertRegex(invite["code"], r"^[A-Z2-9]{8}$")
        self.assertIn(f"start=p_{invite['code']}", invite["link"])
        link = services.accept_parent_invite(self.parent, invite["code"].lower())
        self.assertEqual((link.parent, link.student), (self.parent, self.student))

    def test_code_single_use(self):
        invite = services.create_parent_invite(self.student)
        services.accept_parent_invite(self.parent, invite["code"])
        other = make_user("+998900000003", role=User.Role.PARENT)
        with self.assertRaises(ServiceError):
            services.accept_parent_invite(other, invite["code"])

    def test_expired_code(self):
        invite = services.create_parent_invite(self.student)
        OneTimeCode.objects.update(expires_at=timezone.now())
        with self.assertRaises(ServiceError):
            services.accept_parent_invite(self.parent, invite["code"])

    def test_roles_enforced(self):
        with self.assertRaises(ServiceError):
            services.create_parent_invite(self.parent)
        invite = services.create_parent_invite(self.student)
        with self.assertRaises(ServiceError):
            services.accept_parent_invite(self.student, invite["code"])

    def test_several_children(self):
        second = make_user("+998900000004")
        for child in (self.student, second):
            services.accept_parent_invite(self.parent, services.create_parent_invite(child)["code"])
        self.assertEqual(self.parent.children_links.count(), 2)


class CleanupCodesTests(TestCase):
    def test_removes_only_stale(self):
        user = make_user("+998900000001")
        fresh = services.issue_code(user, OneTimeCode.Purpose.PARENT_LINK)
        stale = services.issue_code(user, OneTimeCode.Purpose.LOGIN)
        OneTimeCode.objects.filter(pk=stale.pk).update(
            expires_at=timezone.now() - timedelta(days=2)
        )
        out = StringIO()
        call_command("cleanup_codes", stdout=out)
        self.assertIn("1", out.getvalue())
        self.assertEqual(list(OneTimeCode.objects.values_list("pk", flat=True)), [fresh.pk])


class PermissionTests(TestCase):
    @staticmethod
    def request(user):
        return SimpleNamespace(user=user)

    def test_roles(self):
        student = make_user("+998900000001")
        parent = make_user("+998900000002", role=User.Role.PARENT)
        admin = make_user("+998900000003", role=User.Role.ADMIN)
        self.assertTrue(IsStudent().has_permission(self.request(student), None))
        self.assertFalse(IsStudent().has_permission(self.request(parent), None))
        self.assertTrue(IsParent().has_permission(self.request(parent), None))
        self.assertTrue(IsAdminRole().has_permission(self.request(admin), None))
        self.assertFalse(IsAdminRole().has_permission(self.request(parent), None))


class BotLoginTests(TestCase):
    def test_new_contact_registers_student_and_issues_code(self):
        user, otc, student = services.register_from_contact(
            telegram_id=111, phone="+998901234567", first_name="Ali"
        )
        self.assertEqual(user.role, User.Role.STUDENT)
        self.assertEqual(otc.purpose, OneTimeCode.Purpose.LOGIN)
        self.assertIsNone(student)
        self.assertEqual(services.verify_login_code("901234567", otc.code), user)

    def test_contact_with_invite_registers_parent(self):
        child = make_user("+998900000001")
        invite = services.create_parent_invite(child)["code"]
        parent, _, student = services.register_from_contact(
            telegram_id=222, phone="+998900000002", invite_code=invite
        )
        self.assertEqual(parent.role, User.Role.PARENT)
        self.assertEqual(student, child)
        self.assertTrue(ParentLink.objects.filter(parent=parent, student=child).exists())

    def test_bad_invite_rolls_back_new_parent(self):
        with self.assertRaises(ServiceError):
            services.register_from_contact(
                telegram_id=222, phone="+998900000002", invite_code="WRONG123"
            )
        self.assertFalse(User.objects.filter(telegram_id=222).exists())

    def test_known_student_cannot_use_parent_invite(self):
        child = make_user("+998900000001")
        other = make_user("+998900000003", telegram_id=333)
        invite = services.create_parent_invite(child)["code"]
        with self.assertRaises(ServiceError):
            services.issue_bot_login(other, invite)

    def test_blocked_user_gets_no_code(self):
        user = make_user("+998900000001", telegram_id=111, is_active=False)
        with self.assertRaises(ServiceError):
            services.issue_bot_login(user)
        self.assertFalse(OneTimeCode.objects.exists())


class AuthApiTests(TestCase):
    def setUp(self):
        cache.clear()
        self.user = make_user("+998901234567", first_name="Ali")

    def verify(self, code: str, phone: str = "901234567"):
        return self.client.post(
            "/api/v1/auth/verify/", {"phone": phone, "code": code}, content_type="application/json"
        )

    def test_verify_returns_tokens_and_profile(self):
        otc = services.issue_code(self.user, OneTimeCode.Purpose.LOGIN)
        res = self.verify(otc.code)
        self.assertEqual(res.status_code, 200)
        body = res.json()
        self.assertEqual(body["user"]["role"], "student")

        me = self.client.get("/api/v1/me/", HTTP_AUTHORIZATION=f"Bearer {body['access']}")
        self.assertEqual(me.json()["phone"], "+998901234567")

        refreshed = self.client.post(
            "/api/v1/auth/refresh/", {"refresh": body["refresh"]}, content_type="application/json"
        )
        self.assertIn("access", refreshed.json())

    def test_wrong_code(self):
        services.issue_code(self.user, OneTimeCode.Purpose.LOGIN)
        res = self.verify("12345")  # format xato
        self.assertEqual(res.status_code, 400)
        res = self.verify("000000" if OneTimeCode.objects.get().code != "000000" else "111111")
        self.assertEqual((res.status_code, res.json()["code"]), (400, "invalid_code"))

    def test_throttled_after_five_requests(self):
        statuses = [self.verify("000000").status_code for _ in range(6)]
        self.assertEqual(statuses[-1], 429)

    def test_me_requires_auth(self):
        self.assertEqual(self.client.get("/api/v1/me/").status_code, 401)

    def test_me_patch_cannot_change_role(self):
        token = RefreshToken.for_user(self.user).access_token
        res = self.client.patch(
            "/api/v1/me/",
            {"first_name": "Vali", "language": "ru", "role": "admin", "phone": "+1"},
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )
        self.assertEqual(res.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(
            (self.user.first_name, self.user.language, self.user.role, self.user.phone),
            ("Vali", "ru", "student", "+998901234567"),
        )

    def test_blocked_user_token_rejected(self):
        token = RefreshToken.for_user(self.user).access_token
        User.objects.filter(pk=self.user.pk).update(is_active=False)
        res = self.client.get("/api/v1/me/", HTTP_AUTHORIZATION=f"Bearer {token}")
        self.assertEqual(res.status_code, 401)


class ParentApiTests(TestCase):
    def setUp(self):
        from apps.courses.models import Course, Enrollment, Lesson

        self.student = make_user("+998900000001", first_name="Ali")
        self.parent = make_user("+998900000002", role=User.Role.PARENT)
        course = Course.objects.create(title="Kurs", price=1000)
        self.lesson = Lesson.objects.create(course=course, order=1, title="Dars")
        self.enrollment = Enrollment.objects.create(student=self.student, course=course)

    def api(self, method: str, url: str, user: User, data=None):
        token = RefreshToken.for_user(user).access_token
        return getattr(self.client, method)(
            url, data, content_type="application/json", HTTP_AUTHORIZATION=f"Bearer {token}"
        )

    def test_invite_and_link_via_api(self):
        res = self.api("post", "/api/v1/me/parent-invites/", self.student)
        self.assertEqual(res.status_code, 201)
        code = res.json()["code"]
        self.assertIn(f"?start=p_{code}", res.json()["link"])

        res = self.api("post", "/api/v1/me/children/", self.parent, {"code": code})
        self.assertEqual(res.status_code, 201)
        self.assertEqual((res.json()["id"], res.json()["full_name"]), (self.student.pk, "Ali"))

        res = self.api("post", "/api/v1/me/children/", self.parent, {"code": code})
        self.assertEqual(res.json()["code"], "invalid_code")

    def test_roles(self):
        self.assertEqual(
            self.api("post", "/api/v1/me/parent-invites/", self.parent).status_code, 403
        )
        self.assertEqual(self.api("get", "/api/v1/me/children/", self.student).status_code, 403)

    def test_children_list_and_progress(self):
        from apps.courses.services import complete_lesson

        second_parent = make_user("+998900000004", role=User.Role.PARENT)
        ParentLink.objects.create(parent=self.parent, student=self.student)
        ParentLink.objects.create(parent=second_parent, student=self.student)
        complete_lesson(self.enrollment, self.lesson)

        with self.assertNumQueries(3):  # user, count, sahifa
            res = self.api("get", "/api/v1/me/children/", self.parent)
        children = res.json()["results"]
        self.assertEqual(len(children), 1)  # ikkinchi ota-ona qatorni ko'paytirmaydi
        self.assertEqual(
            (children[0]["courses_count"], children[0]["completed_courses_count"]), (1, 1)
        )

        url = f"/api/v1/me/children/{self.student.pk}/progress/"
        with self.assertNumQueries(4):  # user, farzand, count, sahifa
            res = self.api("get", url, self.parent)
        item = res.json()["results"][0]
        self.assertEqual((item["progress_percent"], item["completed_at"] is not None), (100, True))

    def test_foreign_child_is_404(self):
        stranger = make_user("+998900000005")
        ParentLink.objects.create(parent=self.parent, student=self.student)
        url = f"/api/v1/me/children/{stranger.pk}/progress/"
        self.assertEqual(self.api("get", url, self.parent).status_code, 404)
