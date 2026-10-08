import uuid

from django.test import TestCase, override_settings
from rest_framework_simplejwt.tokens import RefreshToken

from apps.courses import services
from apps.courses.models import Course, Enrollment, Lesson
from apps.users.models import User

from .models import Certificate


def make_user(phone: str, role: str = User.Role.STUDENT, **extra) -> User:
    return User.objects.create(username=phone, phone=phone, role=role, **extra)


def auth(user: User) -> dict:
    return {"HTTP_AUTHORIZATION": f"Bearer {RefreshToken.for_user(user).access_token}"}


@override_settings(CERTIFICATE_BASE_URL="https://lesson24.uz/certificate/")
class CertificateTests(TestCase):
    def setUp(self):
        self.student = make_user("+998900000002", first_name="Ali", last_name="Valiyev")
        self.course = Course.objects.create(
            title="Python", price=1000, is_published=True, instructor_name="Aziz"
        )
        self.lessons = [
            Lesson.objects.create(course=self.course, order=i, title=f"Dars {i}") for i in (1, 2)
        ]
        self.enrollment = Enrollment.objects.create(student=self.student, course=self.course)

    def finish_course(self):
        for lesson in self.lessons:
            result = services.complete_lesson(self.enrollment, lesson)
        return result

    def test_issued_once_on_course_completion(self):
        services.complete_lesson(self.enrollment, self.lessons[0])
        self.assertFalse(Certificate.objects.exists())
        result = self.finish_course()
        cert = Certificate.objects.get()
        self.assertEqual(result.certificate_uid, cert.uid)
        self.assertEqual(cert.url, f"https://lesson24.uz/certificate/{cert.uid}")
        services.complete_course_if_done(self.enrollment)
        self.assertEqual(Certificate.objects.count(), 1)

    def test_complete_api_returns_uid(self):
        services.complete_lesson(self.enrollment, self.lessons[0])
        res = self.client.post(
            f"/api/v1/lessons/{self.lessons[1].pk}/complete/",
            {},
            content_type="application/json",
            **auth(self.student),
        )
        self.assertEqual(res.json()["certificate_uid"], str(Certificate.objects.get().uid))

    def test_my_certificates(self):
        self.finish_course()
        with self.assertNumQueries(3):  # user, count, sahifa
            res = self.client.get("/api/v1/me/certificates/", **auth(self.student))
        item = res.json()["results"][0]
        self.assertEqual(
            (item["student_name"], item["course_title"], item["instructor_name"]),
            ("Ali Valiyev", "Python", "Aziz"),
        )

    def test_public_verify(self):
        self.finish_course()
        uid = Certificate.objects.get().uid
        with self.assertNumQueries(1):
            res = self.client.get(f"/api/v1/certificates/{uid}/")
        self.assertEqual(res.status_code, 200)
        self.assertNotIn(self.student.phone, res.content.decode())
        self.assertEqual(self.client.get(f"/api/v1/certificates/{uuid.uuid4()}/").status_code, 404)
        self.assertEqual(self.client.get("/api/v1/certificates/123/").status_code, 404)
