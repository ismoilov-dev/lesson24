"""Admin panel API (`/api/v1/admin/`) across apps."""

import io
import shutil
import tempfile
from unittest.mock import MagicMock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.test.client import BOUNDARY, MULTIPART_CONTENT, encode_multipart
from PIL import Image
from rest_framework_simplejwt.tokens import RefreshToken

from apps.certificates.models import Certificate
from apps.courses import services as course_services
from apps.courses.models import Answer, Course, Enrollment, Lesson, Question, Quiz
from apps.payments import services as payment_services

from .models import ParentLink, User

MEDIA = tempfile.mkdtemp()


def make_user(phone: str, role: str = User.Role.STUDENT, **extra) -> User:
    return User.objects.create(username=phone, phone=phone, role=role, **extra)


def image_file() -> SimpleUploadedFile:
    buffer = io.BytesIO()
    Image.new("RGB", (10, 10)).save(buffer, "PNG")
    return SimpleUploadedFile("chek.png", buffer.getvalue(), content_type="image/png")


@override_settings(MEDIA_ROOT=MEDIA, USE_X_ACCEL_REDIRECT=False)
class AdminApiTestCase(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        self.admin = make_user("+998900000001", role=User.Role.ADMIN)
        self.student = make_user("+998900000003", first_name="Ali")
        self.parent = make_user("+998900000004", role=User.Role.PARENT)
        self.course = Course.objects.create(title="Python", price=30_000, is_published=True)

    def api(self, method: str, url: str, data=None, user: User | None = None):
        token = RefreshToken.for_user(user or self.admin).access_token
        return getattr(self.client, method)(
            f"/api/v1/admin/{url}",
            data,
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )


class AccessTests(AdminApiTestCase):
    def test_non_admins_forbidden_everywhere(self):
        urls = [
            "stats/",
            "users/",
            "orders/",
            "courses/",
            "lessons/",
            "enrollments/",
            "parent-links/",
            "certificates/",
        ]
        for user in (self.student, self.parent):
            for url in urls:
                self.assertEqual(self.api("get", url, user=user).status_code, 403, (user.role, url))

    def test_superuser_allowed(self):
        root = User.objects.create_superuser("root", password="x")
        self.assertEqual(self.api("get", "stats/", user=root).status_code, 200)


class StatsTests(AdminApiTestCase):
    def test_dashboard(self):
        order = payment_services.create_order(
            payer=self.student, course=self.course, screenshot=image_file()
        )
        payment_services.approve_order(order, self.admin)
        with self.assertNumQueries(5):  # user + 4 aggregate
            data = self.api("get", "stats/").json()
        self.assertEqual(data["users"]["students"], 1)
        self.assertEqual(data["users"]["teachers"], 0)
        self.assertEqual(data["courses"], {"total": 1, "published": 1})
        self.assertEqual(data["orders"]["revenue_total"], 30_000)
        self.assertEqual(data["orders"]["revenue_month"], 30_000)
        self.assertEqual(data["enrollments"]["total"], 1)


class UserTests(AdminApiTestCase):
    def test_preregister_admin_then_bot_login_links(self):
        from . import services

        res = self.api(
            "post", "users/", {"phone": "90 111 22 33", "first_name": "Olim", "role": "admin"}
        )
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()["phone"], "+998901112233")
        user, created = services.get_or_create_bot_user(telegram_id=55, phone="+998901112233")
        self.assertFalse(created)
        self.assertEqual((user.role, user.is_staff), ("admin", True))

        dup = self.api("post", "users/", {"phone": "+998901112233"})
        self.assertEqual(dup.status_code, 400)
        teacher = self.api("post", "users/", {"phone": "+998905556677", "role": "teacher"})
        self.assertEqual(teacher.status_code, 201)
        # teacher Django Admin'ga kirmaydi — faqat Teacher API
        self.assertFalse(User.objects.get(pk=teacher.json()["id"]).is_staff)

    def test_filters_and_search(self):
        self.assertEqual(self.api("get", "users/?role=parent").json()["count"], 1)
        self.assertEqual(self.api("get", "users/?search=Ali").json()["count"], 1)

    def test_block_and_role_change(self):
        res = self.api("patch", f"users/{self.student.pk}/", {"is_active": False})
        self.assertFalse(res.json()["is_active"])
        res = self.api("patch", f"users/{self.parent.pk}/", {"role": "admin"})
        self.parent.refresh_from_db()
        self.assertTrue(self.parent.is_staff)

    def test_guards(self):
        res = self.api("patch", f"users/{self.admin.pk}/", {"is_active": False})
        self.assertEqual(res.status_code, 400)
        res = self.api("patch", f"users/{self.admin.pk}/", {"role": "student"})
        self.assertEqual(res.status_code, 400)
        root = User.objects.create_superuser("root", password="x")
        self.assertEqual(
            self.api("patch", f"users/{root.pk}/", {"is_active": False}).status_code, 403
        )
        res = self.api("patch", f"users/{self.student.pk}/", {"is_superuser": True})
        self.student.refresh_from_db()
        self.assertFalse(self.student.is_superuser)
        self.assertEqual(self.api("delete", f"users/{self.student.pk}/").status_code, 405)


class OrderTests(AdminApiTestCase):
    def setUp(self):
        super().setUp()
        self.order = payment_services.create_order(
            payer=self.student, course=self.course, screenshot=image_file()
        )

    def test_list_pending_first_and_screenshot(self):
        other = make_user("+998900000005")
        old = payment_services.create_order(
            payer=other, course=self.course, screenshot=image_file()
        )
        payment_services.reject_order(old, self.admin, "x")
        newer = make_user("+998900000006")
        payment_services.create_order(payer=newer, course=self.course, screenshot=image_file())
        with self.assertNumQueries(3):  # user, count, sahifa
            rows = self.api("get", "orders/").json()["results"]
        self.assertEqual([r["status"] for r in rows], ["pending", "pending", "rejected"])
        self.assertEqual(self.api("get", "orders/?status=rejected").json()["count"], 1)

        res = self.api("get", rows[0]["screenshot_url"].removeprefix("/api/v1/admin/"))
        self.assertEqual(res.status_code, 200)
        self.assertEqual(
            self.api("get", f"orders/{self.order.pk}/screenshot/", user=self.student).status_code,
            403,
        )

    def test_approve_and_reject(self):
        res = self.api("post", f"orders/{self.order.pk}/approve/")
        self.assertEqual(
            (res.json()["status"], res.json()["reviewed_by"]["id"]), ("approved", self.admin.pk)
        )
        self.assertTrue(
            Enrollment.objects.filter(student=self.student, course=self.course).exists()
        )
        self.assertEqual(
            self.api("post", f"orders/{self.order.pk}/reject/", {"reason": "x"}).json()["code"],
            "already_reviewed",
        )

    def test_reject_requires_reason(self):
        self.assertEqual(self.api("post", f"orders/{self.order.pk}/reject/", {}).status_code, 400)
        res = self.api("post", f"orders/{self.order.pk}/reject/", {"reason": "Summa xato"})
        self.assertEqual(
            (res.json()["status"], res.json()["reject_reason"]), ("rejected", "Summa xato")
        )


QUIZ = {
    "questions": [
        {
            "text": "2+2?",
            "answers": [{"text": "4", "is_correct": True}, {"text": "5", "is_correct": False}],
        },
        {
            "text": "Python — bu?",
            "answers": [
                {"text": "Til", "is_correct": True},
                {"text": "Ilon", "is_correct": False},
                {"text": "Kofe", "is_correct": False},
            ],
        },
    ]
}


class CourseContentTests(AdminApiTestCase):
    def test_create_list_update(self):
        res = self.api(
            "post",
            "courses/",
            {"title": "Yangi kurs", "price": 20_000, "instructor_name": "Aziz Karimov"},
        )
        self.assertEqual(res.status_code, 201, res.content)
        course = Course.objects.get(pk=res.json()["id"])
        self.assertEqual(
            (course.slug, course.instructor_name, course.is_published),
            ("yangi-kurs", "Aziz Karimov", False),
        )
        Enrollment.objects.create(student=self.student, course=self.course)
        with self.assertNumQueries(3):  # user, count, sahifa
            rows = self.api("get", "courses/").json()["results"]
        python = next(r for r in rows if r["id"] == self.course.pk)
        self.assertEqual((python["lessons_count"], python["students_count"]), (0, 1))
        res = self.api("patch", f"courses/{course.pk}/", {"is_published": True})
        self.assertTrue(res.json()["is_published"])
        self.assertEqual(
            self.api("post", "courses/", {"title": "x", "price": 100_001}).status_code, 400
        )

    def test_delete_protected_when_enrolled(self):
        Enrollment.objects.create(student=self.student, course=self.course)
        self.assertEqual(
            self.api("delete", f"courses/{self.course.pk}/").json()["code"], "course_in_use"
        )
        empty = Course.objects.create(title="Bo'sh", price=0)
        self.assertEqual(self.api("delete", f"courses/{empty.pk}/").status_code, 204)

    def test_course_students_progress(self):
        lessons = [
            Lesson.objects.create(course=self.course, order=i, title=f"Dars {i}") for i in (1, 2, 3)
        ]
        enrollment = Enrollment.objects.create(student=self.student, course=self.course)
        course_services.complete_lesson(enrollment, lessons[0])
        [row] = self.api("get", f"courses/{self.course.pk}/students/").json()["results"]
        self.assertEqual(
            (row["student_id"], row["lessons_done"], row["progress_percent"]),
            (self.student.pk, 1, 33),
        )
        self.assertNotIn("phone", row)


class LessonContentTests(AdminApiTestCase):
    def setUp(self):
        super().setUp()
        self.lessons = [
            Lesson.objects.create(course=self.course, order=i, title=f"Dars {i}") for i in (1, 2, 3)
        ]

    def test_list_and_create_appends(self):
        res = self.api("get", f"courses/{self.course.pk}/lessons/")
        self.assertEqual([lesson["order"] for lesson in res.json()], [1, 2, 3])
        res = self.api(
            "post",
            f"courses/{self.course.pk}/lessons/",
            {"title": "To'rtinchi", "materials": [{"title": "Slayd", "url": "https://x.uz/a.pdf"}]},
        )
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()["order"], 4)

    def test_order_clash_and_bad_materials(self):
        url = f"courses/{self.course.pk}/lessons/"
        self.assertEqual(self.api("post", url, {"title": "x", "order": 2}).status_code, 400)
        res = self.api("post", url, {"title": "x", "materials": [{"title": "a", "url": "nope"}]})
        self.assertEqual(res.status_code, 400)

    def test_reorder(self):
        ids = [lesson.pk for lesson in reversed(self.lessons)]
        url = f"courses/{self.course.pk}/lessons/reorder/"
        res = self.api("post", url, {"lesson_ids": ids})
        self.assertEqual([lesson["id"] for lesson in res.json()], ids)
        self.assertEqual(
            self.api("post", url, {"lesson_ids": ids[:2]}).json()["code"], "invalid_order"
        )

    def test_list_filter_update_delete(self):
        other = Course.objects.create(title="B", price=0)
        Lesson.objects.create(course=other, order=1, title="X")
        self.assertEqual(self.api("get", f"lessons/?course={self.course.pk}").json()["count"], 3)
        self.assertEqual(self.api("get", "lessons/").json()["count"], 4)
        lesson = self.lessons[0]
        res = self.api(
            "patch", f"lessons/{lesson.pk}/", {"video_url": "1AbCdEfGhIjKlMnOpQrStUvWxYz012345"}
        )
        self.assertEqual((res.status_code, res.json()["course"]), (200, self.course.pk))
        self.assertEqual(self.api("delete", f"lessons/{lesson.pk}/").status_code, 204)

    def test_quiz_editor(self):
        url = f"lessons/{self.lessons[0].pk}/quiz/"
        self.assertEqual(self.api("get", url).json()["questions"], [])
        res = self.api("put", url, QUIZ)
        self.assertEqual(res.status_code, 200, res.content)
        self.assertTrue(res.json()["questions"][0]["answers"][0]["is_correct"])

        self.api("put", url, {"questions": QUIZ["questions"][:1]})
        self.assertEqual((Question.objects.count(), Answer.objects.count()), (1, 2))

        self.assertEqual(self.api("delete", url).status_code, 204)
        self.assertFalse(Quiz.objects.exists())

    def test_quiz_validation(self):
        url = f"lessons/{self.lessons[0].pk}/quiz/"
        both = [{"text": "a", "is_correct": True}, {"text": "b", "is_correct": True}]
        two_correct = {"questions": [{"text": "?", "answers": both}]}
        one_answer = {"questions": [{"text": "?", "answers": [{"text": "a", "is_correct": True}]}]}
        for bad in (two_correct, one_answer, {"questions": []}):
            self.assertEqual(self.api("put", url, bad).status_code, 400)

    def test_admin_previews_unpublished_lesson_as_student_view(self):
        Course.objects.filter(pk=self.course.pk).update(is_published=False)
        self.api("put", f"lessons/{self.lessons[0].pk}/quiz/", QUIZ)
        token = {"HTTP_AUTHORIZATION": f"Bearer {RefreshToken.for_user(self.admin).access_token}"}
        res = self.client.get(f"/api/v1/lessons/{self.lessons[0].pk}/", **token)
        self.assertEqual(res.status_code, 200)
        self.assertNotIn("is_correct", res.content.decode())  # student ko'rinishi
        self.assertEqual(
            self.client.get(f"/api/v1/courses/{self.course.slug}/", **token).status_code, 200
        )


class EnrollmentAndLinkTests(AdminApiTestCase):
    def test_manual_enroll_and_unenroll(self):
        res = self.api(
            "post", "enrollments/", {"student": self.student.pk, "course": self.course.pk}
        )
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()["lessons_total"], 0)
        again = self.api(
            "post", "enrollments/", {"student": self.student.pk, "course": self.course.pk}
        )
        self.assertEqual(again.json()["code"], "exists")
        self.assertEqual(
            self.api("get", f"enrollments/?course={self.course.pk}").json()["count"], 1
        )
        self.assertEqual(self.api("delete", f"enrollments/{res.json()['id']}/").status_code, 204)

    def test_cannot_unenroll_with_certificate(self):
        lesson = Lesson.objects.create(course=self.course, order=1, title="Dars")
        enrollment = Enrollment.objects.create(student=self.student, course=self.course)
        course_services.complete_lesson(enrollment, lesson)
        self.assertTrue(Certificate.objects.exists())
        res = self.api("delete", f"enrollments/{enrollment.pk}/")
        self.assertEqual(res.json()["code"], "has_certificate")
        self.assertEqual(self.api("get", "certificates/").json()["count"], 1)

    def test_parent_links(self):
        res = self.api(
            "post", "parent-links/", {"parent": self.parent.pk, "student": self.student.pk}
        )
        self.assertEqual(res.status_code, 201, res.content)
        dup = self.api(
            "post", "parent-links/", {"parent": self.parent.pk, "student": self.student.pk}
        )
        self.assertEqual(dup.json()["code"], "exists")
        wrong = self.api(
            "post", "parent-links/", {"parent": self.student.pk, "student": self.parent.pk}
        )
        self.assertEqual(wrong.status_code, 400)
        self.assertEqual(
            self.api("get", f"parent-links/?student={self.student.pk}").json()["count"], 1
        )
        self.assertEqual(self.api("delete", f"parent-links/{res.json()['id']}/").status_code, 204)
        self.assertFalse(ParentLink.objects.exists())


IMGBB_OK = {"success": True, "data": {"url": "https://i.ibb.co/abc/python.png"}}


@override_settings(IMGBB_API_KEY="test-key")
class CourseFormTests(AdminApiTestCase):
    """Kurs bitta formada (title, description, cover rasm → ImgBB); dars — JSON, video Drive URL."""

    def setUp(self):
        super().setUp()
        patcher = patch("apps.imgbb.httpx.post")
        self.imgbb = patcher.start()
        self.addCleanup(patcher.stop)
        self.imgbb.return_value = MagicMock(json=MagicMock(return_value=IMGBB_OK))

    def auth_header(self) -> dict:
        return {"HTTP_AUTHORIZATION": f"Bearer {RefreshToken.for_user(self.admin).access_token}"}

    def create(self, data: dict):
        # test client POST'da multipart'ni o'zi kodlaydi
        return self.client.post("/api/v1/admin/courses/", data, **self.auth_header())

    def test_create_course_with_cover_in_one_form(self):
        res = self.create(
            {
                "title": "Python asoslari",
                "description": "Noldan",
                "price": 50000,
                "cover": image_file(),
            }
        )
        self.assertEqual(res.status_code, 201, res.content)
        body = res.json()
        self.assertEqual(body["cover_url"], "https://i.ibb.co/abc/python.png")
        self.assertNotIn("cover", body)  # fayl maydoni faqat yozish uchun
        self.assertEqual(Course.objects.get(pk=body["id"]).cover_url, body["cover_url"])
        kwargs = self.imgbb.call_args.kwargs
        self.assertEqual(kwargs["params"], {"key": "test-key"})
        self.assertIn("image", kwargs["files"])

        catalog = self.client.get(f"/api/v1/courses/{body['slug']}/", **self.auth_header())
        self.assertEqual(catalog.json()["cover_url"], body["cover_url"])

    def test_cover_is_optional_and_json_works(self):
        res = self.api("post", "courses/", {"title": "JSON kurs", "price": 0})
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()["cover_url"], "")
        self.imgbb.assert_not_called()

    def test_replace_cover_and_read_only_fields(self):
        url = f"/api/v1/admin/courses/{self.course.pk}/"
        res = self.client.patch(
            url,
            encode_multipart(BOUNDARY, {"cover": image_file()}),
            content_type=MULTIPART_CONTENT,
            **self.auth_header(),
        )
        self.assertEqual(res.json()["cover_url"], "https://i.ibb.co/abc/python.png")
        # cover_url va slug faqat o'qish uchun — yuborilsa e'tiborsiz qoladi
        res = self.api(
            "patch", f"courses/{self.course.pk}/", {"cover_url": "https://x.uz/a.png", "slug": "x"}
        )
        self.assertEqual(
            (res.json()["cover_url"], res.json()["slug"]),
            ("https://i.ibb.co/abc/python.png", self.course.slug),
        )

    def test_invalid_cover_not_sent_to_imgbb(self):
        fake_video = SimpleUploadedFile("dars.mp4", b"\x00" * 100, content_type="video/mp4")
        res = self.create({"title": "X", "price": 0, "cover": fake_video})
        self.assertEqual(res.json()["code"], "invalid_image")
        with override_settings(COURSE_COVER_MAX_BYTES=10):
            res = self.create({"title": "X", "price": 0, "cover": image_file()})
        self.assertEqual(res.json()["code"], "file_too_large")
        self.imgbb.assert_not_called()
        self.assertFalse(Course.objects.filter(title="X").exists())

    def test_imgbb_failure_does_not_create_course(self):
        self.imgbb.return_value = MagicMock(
            json=MagicMock(return_value={"success": False, "error": {"message": "bad key"}})
        )
        res = self.create({"title": "Y", "price": 0, "cover": image_file()})
        self.assertEqual((res.status_code, res.json()["code"]), (502, "image_upload_failed"))
        self.assertFalse(Course.objects.filter(title="Y").exists())

    def test_lessons_accept_json_only(self):
        lesson = Lesson.objects.create(course=self.course, order=1, title="Dars")
        res = self.client.patch(
            f"/api/v1/admin/lessons/{lesson.pk}/",
            encode_multipart(BOUNDARY, {"title": "x"}),
            content_type=MULTIPART_CONTENT,
            **self.auth_header(),
        )
        self.assertEqual(res.status_code, 415)

    @override_settings(VIDEO_PROVIDER="drive")
    def test_lesson_with_video_url(self):
        file_id = "1AbCdEfGhIjKlMnOpQrStUvWxYz012345"
        res = self.api(
            "post",
            f"courses/{self.course.pk}/lessons/",
            {
                "title": "1-dars",
                "video_url": f"https://drive.google.com/file/d/{file_id}/view?usp=sharing",
                "description": "Kirish",
                "duration_min": 12,
            },
        )
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(
            res.json()["video_url"], f"https://drive.google.com/file/d/{file_id}/preview"
        )
        self.assertEqual(Lesson.objects.get(pk=res.json()["id"]).video_id, file_id)
        bad = self.api(
            "patch", f"lessons/{res.json()['id']}/", {"video_url": "https://youtu.be/abc"}
        )
        self.assertEqual(bad.status_code, 400)
