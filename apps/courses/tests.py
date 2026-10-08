import base64
import hashlib

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.test import TestCase, TransactionTestCase, override_settings
from rest_framework_simplejwt.tokens import RefreshToken

from apps.users.models import User

from . import services
from .models import Answer, Course, Enrollment, Lesson, LessonProgress, Question, Quiz
from .permissions import can_access_lesson
from .video import parse_drive_id, signed_hls_url


def make_user(phone: str, role: str = User.Role.STUDENT, **extra) -> User:
    return User.objects.create(username=phone, phone=phone, role=role, **extra)


def make_course(title: str = "Python asoslari", **extra) -> Course:
    extra.setdefault("price", 50_000)
    extra.setdefault("is_published", True)
    return Course.objects.create(title=title, **extra)


def auth(user: User) -> dict:
    return {"HTTP_AUTHORIZATION": f"Bearer {RefreshToken.for_user(user).access_token}"}


class CourseModelTests(TestCase):
    def test_slug_auto_and_unique(self):
        a = make_course("Python asoslari")
        b = make_course("Python asoslari")
        self.assertEqual(a.slug, "python-asoslari")
        self.assertTrue(b.slug.startswith("python-asoslari-"))

    def test_price_limit(self):
        course = Course(title="X", price=100_001)
        with self.assertRaises(ValidationError):
            course.full_clean()


class LessonOrderConstraintTests(TransactionTestCase):
    """Deferred constraint commit paytida tekshiriladi — haqiqiy tranzaksiyalar kerak."""

    def test_unique_but_swappable_in_transaction(self):
        course = make_course()
        first = Lesson.objects.create(course=course, order=1, title="A")
        second = Lesson.objects.create(course=course, order=2, title="B")
        with transaction.atomic():
            Lesson.objects.filter(pk=first.pk).update(order=2)
            Lesson.objects.filter(pk=second.pk).update(order=1)
        self.assertEqual(Lesson.objects.get(pk=first.pk).order, 2)
        with self.assertRaises(IntegrityError), transaction.atomic():
            Lesson.objects.create(course=course, order=1, title="C")


class LessonAccessTests(TestCase):
    def setUp(self):
        self.admin = make_user("+998900000001", role=User.Role.ADMIN)
        self.student = make_user("+998900000002")
        self.course = make_course()
        self.paid = Lesson.objects.create(course=self.course, order=1, title="Pullik")
        self.free = Lesson.objects.create(
            course=self.course, order=2, title="Bepul", is_free_preview=True
        )

    def test_rules(self):
        from django.contrib.auth.models import AnonymousUser

        self.assertTrue(can_access_lesson(AnonymousUser(), self.free))
        self.assertFalse(can_access_lesson(AnonymousUser(), self.paid))
        self.assertFalse(can_access_lesson(self.student, self.paid))
        self.assertTrue(can_access_lesson(self.admin, self.paid))  # oldindan ko'rish
        Enrollment.objects.create(student=self.student, course=self.course)
        self.assertTrue(can_access_lesson(self.student, self.paid))


class CatalogApiTests(TestCase):
    def setUp(self):
        self.admin = make_user("+998900000001", role=User.Role.ADMIN)
        self.student = make_user("+998900000002")
        self.course = make_course("Python asoslari", instructor_name="Aziz")
        for i in range(1, 4):
            lesson = Lesson.objects.create(
                course=self.course,
                order=i,
                title=f"Dars {i}",
                duration_min=10,
                video_id="secret-video",
            )
        Quiz.objects.create(lesson=lesson)
        make_course("Yashirin", is_published=False)

    def test_list_only_published_without_n_plus_one(self):
        for i in range(5):
            make_course(f"Kurs {i}")
        with self.assertNumQueries(2):  # count + sahifa
            res = self.client.get("/api/v1/courses/")
        body = res.json()
        self.assertEqual(body["count"], 6)
        item = next(c for c in body["results"] if c["slug"] == "python-asoslari")
        self.assertEqual((item["lessons_count"], item["duration_min"]), (3, 30))
        self.assertEqual(item["instructor_name"], "Aziz")

    def test_search(self):
        res = self.client.get("/api/v1/courses/?search=pyth")
        self.assertEqual(res.json()["count"], 1)

    def test_detail(self):
        with self.assertNumQueries(2):  # kurs + darslar
            res = self.client.get("/api/v1/courses/python-asoslari/")
        body = res.json()
        self.assertFalse(body["is_enrolled"])
        self.assertEqual([lesson["order"] for lesson in body["lessons"]], [1, 2, 3])
        self.assertEqual([lesson["has_quiz"] for lesson in body["lessons"]], [False, False, True])
        self.assertNotIn("secret-video", res.content.decode())

    def test_detail_enrolled(self):
        Enrollment.objects.create(student=self.student, course=self.course)
        res = self.client.get("/api/v1/courses/python-asoslari/", **auth(self.student))
        self.assertTrue(res.json()["is_enrolled"])

    def test_unpublished_hidden_but_admin_can_preview(self):
        hidden = Course.objects.get(title="Yashirin")
        url = f"/api/v1/courses/{hidden.slug}/"
        self.assertEqual(self.client.get(url).status_code, 404)
        self.assertEqual(self.client.get(url, **auth(self.student)).status_code, 404)
        self.assertEqual(self.client.get(url, **auth(self.admin)).status_code, 200)
        # katalog ro'yxatida admin uchun ham faqat nashr qilinganlar
        slugs = [
            c["slug"]
            for c in self.client.get("/api/v1/courses/", **auth(self.admin)).json()["results"]
        ]
        self.assertNotIn(hidden.slug, slugs)


class AdminAccessTests(TestCase):
    """Django Admin: kontentni faqat admin boshqaradi."""

    def setUp(self):
        self.admin = make_user("+998900000001", role=User.Role.ADMIN)
        self.student = make_user("+998900000002")
        self.course = make_course("Python asoslari")

    def test_admin_creates_course_with_instructor_name(self):
        self.client.force_login(self.admin)
        res = self.client.post(
            "/admin/courses/course/add/",
            {
                "title": "Yangi",
                "slug": "yangi",
                "price": 10_000,
                "instructor_name": "Aziz Karimov",
                "review_status": "draft",
                "lessons-TOTAL_FORMS": 0,
                "lessons-INITIAL_FORMS": 0,
            },
        )
        self.assertEqual(res.status_code, 302, res.content[:2000])
        self.assertEqual(Course.objects.get(slug="yangi").instructor_name, "Aziz Karimov")
        self.assertContains(self.client.get("/admin/courses/course/"), "Python asoslari")

    def test_admin_cannot_grant_superuser(self):
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get("/admin/users/user/").status_code, 200)
        page = self.client.get(f"/admin/users/user/{self.student.pk}/change/")
        self.assertNotContains(page, 'name="is_superuser"')

    def test_non_staff_redirected_to_login(self):
        self.client.force_login(self.student)
        res = self.client.get("/admin/courses/course/")
        self.assertEqual(res.status_code, 302)


class VideoUrlTests(TestCase):
    @override_settings(VIDEO_SIGNING_KEY="", VIDEO_CDN_HOSTNAME="cdn.example")
    def test_not_configured(self):
        self.assertIsNone(signed_hls_url("abc"))

    @override_settings(
        VIDEO_SIGNING_KEY="key", VIDEO_CDN_HOSTNAME="cdn.example", VIDEO_URL_TTL=3600
    )
    def test_signed_and_stable_within_window(self):
        now = 1_700_000_000
        url = signed_hls_url("vid-1", now=now)
        expires = int(url.split("expires=")[1].split("/")[0])
        self.assertGreaterEqual(expires, now + 3600)
        self.assertLess(expires, now + 3600 + 1800)
        token_path = "/vid-1/"
        digest = hashlib.sha256(f"key{token_path}{expires}token_path={token_path}".encode())
        token = base64.urlsafe_b64encode(digest.digest()).decode().rstrip("=")
        self.assertEqual(
            url,
            f"https://cdn.example/bcdn_token={token}&token_path=%2Fvid-1%2F"
            f"&expires={expires}/vid-1/playlist.m3u8",
        )
        self.assertEqual(signed_hls_url("vid-1", now=now + 1), url)


class LearningFlowTests(TestCase):
    def setUp(self):
        self.admin = make_user("+998900000001", role=User.Role.ADMIN)
        self.student = make_user("+998900000002")
        self.course = make_course()
        self.l1 = Lesson.objects.create(course=self.course, order=1, title="Kirish", video_id="v1")
        self.l2 = Lesson.objects.create(course=self.course, order=2, title="Test darsi")
        quiz = Quiz.objects.create(lesson=self.l2)
        self.right, self.wrong = [], []
        for i in range(5):
            q = Question.objects.create(quiz=quiz, order=i, text=f"Savol {i}")
            self.right.append(Answer.objects.create(question=q, text="ha", is_correct=True).pk)
            self.wrong.append(Answer.objects.create(question=q, text="yo'q").pk)
        self.enrollment = Enrollment.objects.create(student=self.student, course=self.course)

    def complete(self, lesson, answers=None, user=None):
        return self.client.post(
            f"/api/v1/lessons/{lesson.pk}/complete/",
            {"answers": answers or []},
            content_type="application/json",
            **auth(user or self.student),
        )

    def test_lesson_detail_hides_correct_answers(self):
        with override_settings(VIDEO_SIGNING_KEY="k", VIDEO_CDN_HOSTNAME="cdn.example"):
            res = self.client.get(f"/api/v1/lessons/{self.l2.pk}/", **auth(self.student))
        body = res.json()
        self.assertEqual(res.status_code, 200)
        self.assertEqual(len(body["quiz"]["questions"]), 5)
        self.assertNotIn("is_correct", res.content.decode())
        self.assertIsNone(body["progress"])

        services.complete_lesson(self.enrollment, self.l1)
        res = self.client.get(f"/api/v1/lessons/{self.l1.pk}/", **auth(self.student))
        self.assertIsNotNone(res.json()["progress"]["completed_at"])

        res = self.client.get(f"/api/v1/lessons/{self.l1.pk}/", **auth(self.student))
        self.assertIsNone(res.json()["quiz"])

    def test_lesson_detail_query_count(self):
        # user, dars+kurs+quiz+yozilish+progress, savollar, javoblar
        with self.assertNumQueries(4):
            self.client.get(f"/api/v1/lessons/{self.l2.pk}/", **auth(self.student))

    def test_not_enrolled_and_free_preview(self):
        other = make_user("+998900000003")
        self.assertEqual(
            self.client.get(f"/api/v1/lessons/{self.l1.pk}/", **auth(other)).status_code, 403
        )
        self.assertEqual(self.complete(self.l1, user=other).status_code, 403)
        Lesson.objects.filter(pk=self.l1.pk).update(is_free_preview=True)
        res = self.client.get(f"/api/v1/lessons/{self.l1.pk}/")
        self.assertEqual(res.status_code, 200)

    def test_unpublished_course_lessons_hidden(self):
        Course.objects.filter(pk=self.course.pk).update(is_published=False)
        url = f"/api/v1/lessons/{self.l1.pk}/"
        self.assertEqual(self.client.get(url, **auth(self.student)).status_code, 404)
        self.assertEqual(self.client.get(url, **auth(self.admin)).status_code, 200)

    def test_full_flow_to_course_completion(self):
        body = self.complete(self.l1).json()
        self.assertEqual((body["lesson_completed"], body["course_completed"]), (True, False))

        self.assertEqual(self.complete(self.l2).json()["code"], "answers_required")

        # 2/5 = 40% — o'tmadi
        body = self.complete(self.l2, self.right[:2] + self.wrong[2:]).json()
        self.assertEqual((body["quiz_score"], body["passed"]), (40, False))
        self.assertFalse(body["lesson_completed"])

        # 3/5 = 60% — o'tdi, kurs tugadi
        body = self.complete(self.l2, self.right[:3] + self.wrong[3:]).json()
        self.assertEqual((body["quiz_score"], body["passed"]), (60, True))
        self.assertTrue(body["course_completed"])
        self.enrollment.refresh_from_db()
        self.assertIsNotNone(self.enrollment.completed_at)

        # Qayta topshirish: eng yaxshi ball saqlanadi
        self.complete(self.l2, self.wrong)
        self.assertEqual(LessonProgress.objects.get(lesson=self.l2).quiz_score, 60)

    def test_invalid_answers(self):
        other_quiz = Quiz.objects.create(lesson=self.l1)
        foreign = Answer.objects.create(
            question=Question.objects.create(quiz=other_quiz, text="?"), text="x"
        )
        self.assertEqual(self.complete(self.l2, [foreign.pk]).status_code, 400)
        two_for_one = [self.right[0], self.wrong[0]]
        self.assertEqual(self.complete(self.l2, two_for_one).json()["code"], "invalid_answers")

    def test_complete_course_if_done_only_once(self):
        services.complete_lesson(self.enrollment, self.l1)
        services.complete_lesson(self.enrollment, self.l2, self.right)
        self.assertFalse(services.complete_course_if_done(self.enrollment))

    def test_my_courses(self):
        second = make_course("Ikkinchi")
        Enrollment.objects.create(student=self.student, course=second)
        services.complete_lesson(self.enrollment, self.l1)
        with self.assertNumQueries(3):  # user, count, sahifa (barcha hisoblar subquery'da)
            res = self.client.get("/api/v1/me/courses/", **auth(self.student))
        items = {item["course"]["slug"]: item for item in res.json()["results"]}
        first = items[self.course.slug]
        self.assertEqual(
            (first["lessons_total"], first["lessons_done"], first["progress_percent"]), (2, 1, 50)
        )
        self.assertEqual(first["next_lesson_id"], self.l2.pk)
        empty = items[second.slug]
        self.assertEqual((empty["lessons_total"], empty["next_lesson_id"]), (0, None))

    def test_my_courses_student_only(self):
        res = self.client.get("/api/v1/me/courses/", **auth(self.admin))
        self.assertEqual(res.status_code, 403)


class FreeEnrollTests(TestCase):
    def setUp(self):
        self.student = make_user("+998900000002")
        self.free = make_course("Bepul kurs", price=0)
        self.paid = make_course("Pullik kurs", price=10_000)

    def enroll(self, course, user=None):
        return self.client.post(
            f"/api/v1/courses/{course.slug}/enroll/", **auth(user or self.student)
        )

    def test_free_course_enrolls_immediately(self):
        res = self.enroll(self.free)
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()["course"], self.free.slug)
        self.assertTrue(Enrollment.objects.filter(student=self.student, course=self.free).exists())
        detail = self.client.get(f"/api/v1/courses/{self.free.slug}/", **auth(self.student))
        self.assertTrue(detail.json()["is_enrolled"])
        self.assertEqual(self.enroll(self.free).json()["code"], "already_enrolled")

    def test_paid_course_requires_payment(self):
        self.assertEqual(self.enroll(self.paid).json()["code"], "payment_required")
        self.assertFalse(Enrollment.objects.exists())

    def test_only_students_and_published(self):
        parent = make_user("+998900000003", role=User.Role.PARENT)
        self.assertEqual(self.enroll(self.free, user=parent).status_code, 403)
        self.assertEqual(
            self.client.post(f"/api/v1/courses/{self.free.slug}/enroll/").status_code, 401
        )
        hidden = make_course("Yashirin bepul", price=0, is_published=False)
        self.assertEqual(self.enroll(hidden).status_code, 404)


class DriveVideoTests(TestCase):
    FILE_ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz012345"

    def test_parse_drive_id_variants(self):
        for value in (
            f"https://drive.google.com/file/d/{self.FILE_ID}/view?usp=sharing",
            f"https://drive.google.com/file/d/{self.FILE_ID}/preview",
            f"https://drive.google.com/open?id={self.FILE_ID}",
            f"https://drive.google.com/uc?id={self.FILE_ID}&export=download",
            f"  {self.FILE_ID}  ",
        ):
            self.assertEqual(parse_drive_id(value), self.FILE_ID, value)
        for bad in ("https://drive.google.com/drive/folders/", "salom dunyo", "https://youtu.be/x"):
            self.assertIsNone(parse_drive_id(bad), bad)

    @override_settings(VIDEO_PROVIDER="drive")
    def test_enrolled_student_gets_embed_url_others_do_not(self):
        student = make_user("+998900000002")
        course = make_course()
        lesson = Lesson.objects.create(course=course, order=1, title="Dars", video_id=self.FILE_ID)
        detail = self.client.get(f"/api/v1/courses/{course.slug}/", **auth(student))
        self.assertNotIn(self.FILE_ID, detail.content.decode())  # katalog/kurs sahifasida yo'q
        self.assertEqual(
            self.client.get(f"/api/v1/lessons/{lesson.pk}/", **auth(student)).status_code, 403
        )
        Enrollment.objects.create(student=student, course=course)
        body = self.client.get(f"/api/v1/lessons/{lesson.pk}/", **auth(student)).json()
        self.assertEqual(
            (body["video_url"], body["video_type"]),
            (f"https://drive.google.com/file/d/{self.FILE_ID}/preview", "iframe"),
        )

    @override_settings(VIDEO_PROVIDER="drive")
    def test_admin_pastes_full_link(self):
        admin = make_user("+998900000001", role=User.Role.ADMIN)
        lesson = Lesson.objects.create(course=make_course(), order=1, title="Dars")
        link = f"https://drive.google.com/file/d/{self.FILE_ID}/view?usp=sharing&resourcekey=0-abc"
        res = self.client.patch(
            f"/api/v1/admin/lessons/{lesson.pk}/",
            {"video_url": link},
            content_type="application/json",
            **auth(admin),
        )
        self.assertEqual(res.status_code, 200, res.content)
        lesson.refresh_from_db()
        self.assertEqual(lesson.video_id, self.FILE_ID)
        res = self.client.patch(
            f"/api/v1/admin/lessons/{lesson.pk}/",
            {"video_url": "https://youtu.be/abc"},
            content_type="application/json",
            **auth(admin),
        )
        self.assertEqual(res.status_code, 400)

    @override_settings(VIDEO_PROVIDER="drive")
    def test_django_admin_form_normalizes_link(self):
        lesson = Lesson(course=make_course(), order=1, title="Dars")
        lesson.video_id = f"https://drive.google.com/open?id={self.FILE_ID}"
        lesson.full_clean()
        self.assertEqual(lesson.video_id, self.FILE_ID)
        lesson.video_id = "noto'g'ri havola"
        with self.assertRaises(ValidationError):
            lesson.full_clean()

    @override_settings(
        VIDEO_PROVIDER="bunny", VIDEO_SIGNING_KEY="k", VIDEO_CDN_HOSTNAME="cdn.example"
    )
    def test_bunny_mode_still_works(self):
        student = make_user("+998900000002")
        course = make_course()
        lesson = Lesson.objects.create(course=course, order=1, title="Dars", video_id="vid-1")
        Enrollment.objects.create(student=student, course=course)
        body = self.client.get(f"/api/v1/lessons/{lesson.pk}/", **auth(student)).json()
        self.assertEqual(body["video_type"], "hls")
        self.assertTrue(body["video_url"].startswith("https://cdn.example/bcdn_token="))
