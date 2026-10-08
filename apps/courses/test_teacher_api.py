"""Teacher paneli: o'z kurslari, tekshiruv oqimi (submit → approve/reject), profil."""

from io import BytesIO
from unittest.mock import MagicMock, patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.test.client import BOUNDARY, MULTIPART_CONTENT, encode_multipart
from PIL import Image
from rest_framework_simplejwt.tokens import RefreshToken

from apps.users.models import TeacherProfile, User

from . import services
from .models import Course, Enrollment, Lesson

FILE_ID = "1AbCdEfGhIjKlMnOpQrStUvWxYz012345"


def make_user(phone: str, role: str = User.Role.STUDENT, **extra) -> User:
    return User.objects.create(username=phone, phone=phone, role=role, **extra)


class TeacherApiTestCase(TestCase):
    def setUp(self):
        self.teacher = make_user(
            "+998900000001", role=User.Role.TEACHER, first_name="Aziz", last_name="Karimov"
        )
        self.other = make_user("+998900000002", role=User.Role.TEACHER)
        self.admin = make_user("+998900000003", role=User.Role.ADMIN)
        self.student = make_user("+998900000004")
        self.foreign = Course.objects.create(owner=self.other, title="Begona", price=1000)

    def call(self, method: str, url: str, data=None, user: User | None = None):
        token = RefreshToken.for_user(user or self.teacher).access_token
        return getattr(self.client, method)(
            f"/api/v1/{url}",
            data,
            content_type="application/json",
            HTTP_AUTHORIZATION=f"Bearer {token}",
        )

    def teacher_course(self, **extra) -> Course:
        extra.setdefault("price", 20_000)
        return Course.objects.create(owner=self.teacher, title="Mening kursim", **extra)


class AccessTests(TeacherApiTestCase):
    def test_only_teachers(self):
        for user in (self.student, self.admin):
            self.assertEqual(self.call("get", "teacher/courses/", user=user).status_code, 403)

    def test_foreign_course_and_lesson_are_404(self):
        lesson = Lesson.objects.create(course=self.foreign, order=1, title="X")
        self.assertEqual(self.call("get", f"teacher/courses/{self.foreign.pk}/").status_code, 404)
        self.assertEqual(self.call("get", f"teacher/lessons/{lesson.pk}/").status_code, 404)
        self.assertEqual(self.call("get", "teacher/courses/").json()["count"], 0)

    def test_payments_not_visible(self):
        self.assertEqual(self.call("get", "admin/orders/").status_code, 403)


class CourseFlowTests(TeacherApiTestCase):
    def test_create_is_draft_owned_and_cannot_self_publish(self):
        res = self.call(
            "post", "teacher/courses/", {"title": "Python", "price": 30_000, "is_published": True}
        )
        self.assertEqual(res.status_code, 201, res.content)
        course = Course.objects.get(pk=res.json()["id"])
        self.assertEqual(
            (course.owner, course.is_published, course.review_status),
            (self.teacher, False, "draft"),
        )
        res = self.call("patch", f"teacher/courses/{course.pk}/", {"is_published": True})
        self.assertFalse(res.json()["is_published"])

    @override_settings(VIDEO_PROVIDER="drive")
    def test_full_review_flow(self):
        course = self.teacher_course()
        self.assertEqual(
            self.call("post", f"teacher/courses/{course.pk}/submit/").json()["code"], "no_lessons"
        )
        res = self.call(
            "post",
            f"teacher/courses/{course.pk}/lessons/",
            {"title": "1-dars", "video_url": f"https://drive.google.com/file/d/{FILE_ID}/view"},
        )
        self.assertEqual(res.status_code, 201, res.content)

        res = self.call("post", f"teacher/courses/{course.pk}/submit/")
        self.assertEqual(res.json()["review_status"], "pending")
        self.assertEqual(
            self.call("post", f"teacher/courses/{course.pk}/submit/").json()["code"],
            "invalid_review_status",
        )

        pending = self.call("get", "admin/courses/?review_status=pending", user=self.admin)
        self.assertEqual([c["id"] for c in pending.json()["results"]], [course.pk])

        res = self.call(
            "post",
            f"admin/courses/{course.pk}/reject/",
            {"note": "Video sifatsiz"},
            user=self.admin,
        )
        self.assertEqual(
            (res.json()["review_status"], res.json()["review_note"]), ("rejected", "Video sifatsiz")
        )
        self.assertEqual(self.call("post", f"teacher/courses/{course.pk}/submit/").status_code, 200)

        res = self.call("post", f"admin/courses/{course.pk}/approve/", user=self.admin)
        self.assertEqual(
            (res.json()["review_status"], res.json()["is_published"]), ("approved", True)
        )

        catalog = self.client.get("/api/v1/courses/").json()["results"]
        self.assertEqual(catalog[0]["instructor_name"], "Aziz Karimov")

    def test_reject_requires_note(self):
        course = self.teacher_course(review_status="pending")
        res = self.call("post", f"admin/courses/{course.pk}/reject/", {}, user=self.admin)
        self.assertEqual(res.status_code, 400)

    def test_cannot_delete_published(self):
        course = self.teacher_course(is_published=True, review_status="approved")
        res = self.call("delete", f"teacher/courses/{course.pk}/")
        self.assertEqual(res.json()["code"], "course_published")
        draft = self.teacher_course()
        self.assertEqual(self.call("delete", f"teacher/courses/{draft.pk}/").status_code, 204)

    def test_lessons_quiz_reorder_students_inherited(self):
        course = self.teacher_course()
        lessons = [Lesson.objects.create(course=course, order=i, title=f"D{i}") for i in (1, 2)]
        quiz = {
            "questions": [
                {"text": "?", "answers": [{"text": "a", "is_correct": True}, {"text": "b"}]}
            ]
        }
        self.assertEqual(
            self.call("put", f"teacher/lessons/{lessons[0].pk}/quiz/", quiz).status_code, 200
        )
        ids = [lessons[1].pk, lessons[0].pk]
        res = self.call(
            "post", f"teacher/courses/{course.pk}/lessons/reorder/", {"lesson_ids": ids}
        )
        self.assertEqual([lesson["id"] for lesson in res.json()], ids)
        enrollment = Enrollment.objects.create(student=self.student, course=course)
        services.complete_lesson(enrollment, lessons[1])
        rows = self.call("get", f"teacher/courses/{course.pk}/students/").json()["results"]
        self.assertEqual(rows[0]["lessons_done"], 1)

    def test_teacher_previews_own_unpublished_course(self):
        course = self.teacher_course()
        lesson = Lesson.objects.create(course=course, order=1, title="D1")
        self.assertEqual(self.call("get", f"courses/{course.slug}/").status_code, 200)
        self.assertEqual(self.call("get", f"lessons/{lesson.pk}/").status_code, 200)
        self.assertEqual(
            self.call("get", f"courses/{course.slug}/", user=self.other).status_code, 404
        )
        self.assertEqual(
            self.call("get", f"courses/{course.slug}/", user=self.student).status_code, 404
        )

    def test_stats(self):
        course = self.teacher_course(is_published=True, review_status="approved")
        self.teacher_course(review_status="pending")
        Enrollment.objects.create(student=self.student, course=course)
        self.assertEqual(
            self.call("get", "teacher/stats/").json(),
            {
                "courses": 2,
                "published_courses": 1,
                "pending_review": 1,
                "students": 1,
                "completed": 0,
            },
        )


@override_settings(IMGBB_API_KEY="k")
class ProfileTests(TeacherApiTestCase):
    def test_profile_and_public_instructor(self):
        res = self.call("get", "teacher/profile/")
        self.assertEqual(res.json()["first_name"], "Aziz")
        imgbb = MagicMock(
            json=MagicMock(
                return_value={"success": True, "data": {"url": "https://i.ibb.co/p.png"}}
            )
        )
        buf = BytesIO()
        Image.new("RGB", (5, 5)).save(buf, "PNG")
        photo = SimpleUploadedFile("p.png", buf.getvalue(), content_type="image/png")
        token = RefreshToken.for_user(self.teacher).access_token
        with patch("apps.imgbb.httpx.post", return_value=imgbb):
            res = self.client.patch(
                "/api/v1/teacher/profile/",
                encode_multipart(
                    BOUNDARY, {"specialization": "Python", "bio": "10 yil tajriba", "photo": photo}
                ),
                content_type=MULTIPART_CONTENT,
                HTTP_AUTHORIZATION=f"Bearer {token}",
            )
        self.assertEqual(res.status_code, 200, res.content)
        self.assertEqual(
            TeacherProfile.objects.get(user=self.teacher).photo_url, "https://i.ibb.co/p.png"
        )

        course = self.teacher_course(is_published=True, review_status="approved")
        detail = self.client.get(f"/api/v1/courses/{course.slug}/").json()
        self.assertEqual(
            detail["instructor"],
            {
                "name": "Aziz Karimov",
                "specialization": "Python",
                "bio": "10 yil tajriba",
                "photo_url": "https://i.ibb.co/p.png",
            },
        )

    def test_admin_course_shows_instructor_name_text(self):
        course = Course.objects.create(
            title="Platforma", price=0, is_published=True, instructor_name="Jamoa"
        )
        detail = self.client.get(f"/api/v1/courses/{course.slug}/").json()
        self.assertEqual(detail["instructor"]["name"], "Jamoa")
