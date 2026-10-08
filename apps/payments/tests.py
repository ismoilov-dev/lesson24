import io
import shutil
import tempfile

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from PIL import Image
from rest_framework_simplejwt.tokens import RefreshToken

from apps.courses.models import Course, Enrollment
from apps.errors import ServiceError
from apps.users.models import ParentLink, User

from . import services

MEDIA = tempfile.mkdtemp()


def make_user(phone: str, role: str = User.Role.STUDENT, **extra) -> User:
    return User.objects.create(username=phone, phone=phone, role=role, **extra)


def image_file(fmt: str = "PNG", name: str = "chek.png", size=(20, 20)) -> SimpleUploadedFile:
    buffer = io.BytesIO()
    Image.new("RGB", size, "white").save(buffer, fmt)
    return SimpleUploadedFile(name, buffer.getvalue(), content_type=f"image/{fmt.lower()}")


def auth(user: User) -> dict:
    return {"HTTP_AUTHORIZATION": f"Bearer {RefreshToken.for_user(user).access_token}"}


@override_settings(
    MEDIA_ROOT=MEDIA, PAYMENT_CARD_NUMBER="8600 0000 0000 0000", PAYMENT_CARD_HOLDER="LESSON24"
)
class PaymentTestCase(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        self.student = make_user("+998900000002")
        self.parent = make_user("+998900000003", role=User.Role.PARENT)
        self.admin = make_user("+998900000004", role=User.Role.ADMIN)
        self.course = Course.objects.create(title="Python", price=50_000, is_published=True)


class OrderServiceTests(PaymentTestCase):
    def test_student_order_and_approve(self):
        order = services.create_order(
            payer=self.student, course=self.course, screenshot=image_file()
        )
        self.assertEqual(
            (order.student, order.amount, order.status), (self.student, 50_000, "pending")
        )
        self.assertTrue(order.screenshot.name.startswith("payments/"))
        self.assertNotIn("chek", order.screenshot.name)  # uuid nom

        services.approve_order(order, self.admin)
        order.refresh_from_db()
        self.assertEqual((order.status, order.reviewed_by), ("approved", self.admin))
        self.assertTrue(
            Enrollment.objects.filter(student=self.student, course=self.course).exists()
        )

        with self.assertRaises(ServiceError):  # ikkinchi marta ko'rib bo'lmaydi
            services.reject_order(order, self.admin, "x")
        with self.assertRaises(ServiceError):  # endi yozilgan
            services.create_order(payer=self.student, course=self.course, screenshot=image_file())

    def test_amount_is_frozen(self):
        order = services.create_order(
            payer=self.student, course=self.course, screenshot=image_file()
        )
        Course.objects.filter(pk=self.course.pk).update(price=99_000)
        order.refresh_from_db()
        self.assertEqual(order.amount, 50_000)

    def test_one_pending_per_student_course(self):
        services.create_order(payer=self.student, course=self.course, screenshot=image_file())
        with self.assertRaises(ServiceError) as ctx:
            services.create_order(payer=self.student, course=self.course, screenshot=image_file())
        self.assertEqual(ctx.exception.code, "pending_exists")

    def test_reject_requires_reason_then_new_order_allowed(self):
        order = services.create_order(
            payer=self.student, course=self.course, screenshot=image_file()
        )
        with self.assertRaises(ServiceError):
            services.reject_order(order, self.admin, "  ")
        services.reject_order(order, self.admin, "Summa mos emas")
        order.refresh_from_db()
        self.assertEqual((order.status, order.reject_reason), ("rejected", "Summa mos emas"))
        services.create_order(payer=self.student, course=self.course, screenshot=image_file())

    def test_parent_buys_only_for_linked_child(self):
        with self.assertRaises(ServiceError):
            services.create_order(payer=self.parent, course=self.course, screenshot=image_file())
        with self.assertRaises(ServiceError):
            services.create_order(
                payer=self.parent, course=self.course, screenshot=image_file(), student=self.student
            )
        ParentLink.objects.create(parent=self.parent, student=self.student)
        order = services.create_order(
            payer=self.parent, course=self.course, screenshot=image_file(), student=self.student
        )
        self.assertEqual((order.user, order.student), (self.parent, self.student))

    def test_student_cannot_buy_for_someone_else(self):
        other = make_user("+998900000005")
        with self.assertRaises(ServiceError):
            services.create_order(
                payer=self.student, course=self.course, screenshot=image_file(), student=other
            )

    def test_screenshot_validation(self):
        fake = SimpleUploadedFile("chek.png", b"not an image", content_type="image/png")
        gif = image_file("GIF", "chek.gif")
        for bad in (fake, gif):
            with self.assertRaises(ServiceError):
                services.validate_screenshot(bad)
        with override_settings(PAYMENT_SCREENSHOT_MAX_BYTES=10), self.assertRaises(ServiceError):
            services.validate_screenshot(image_file())
        services.validate_screenshot(image_file("JPEG", "chek.jpg"))
        services.validate_screenshot(image_file("WEBP", "chek.webp"))


class OrderApiTests(PaymentTestCase):
    def test_payment_info(self):
        res = self.client.get(
            f"/api/v1/me/orders/payment-info/?course={self.course.slug}", **auth(self.student)
        )
        self.assertEqual(
            res.json(),
            {"card_number": "8600 0000 0000 0000", "card_holder": "LESSON24", "amount": 50_000},
        )
        self.assertEqual(
            self.client.get("/api/v1/me/orders/payment-info/?course=x").status_code, 401
        )

    def test_create_and_list(self):
        res = self.client.post(
            "/api/v1/me/orders/",
            {"course": self.course.slug, "screenshot": image_file()},
            **auth(self.student),
        )
        self.assertEqual(res.status_code, 201, res.content)
        self.assertEqual(res.json()["status"], "pending")
        self.assertNotIn("screenshot", res.json())

        res = self.client.get("/api/v1/me/orders/", **auth(self.student))
        self.assertEqual(res.json()["count"], 1)

    def test_parent_order_visible_to_child(self):
        ParentLink.objects.create(parent=self.parent, student=self.student)
        res = self.client.post(
            "/api/v1/me/orders/",
            {"course": self.course.slug, "student": self.student.pk, "screenshot": image_file()},
            **auth(self.parent),
        )
        self.assertEqual(res.status_code, 201, res.content)
        for user in (self.parent, self.student):
            self.assertEqual(self.client.get("/api/v1/me/orders/", **auth(user)).json()["count"], 1)

    def test_errors(self):
        res = self.client.post(
            "/api/v1/me/orders/",
            {"course": self.course.slug, "screenshot": SimpleUploadedFile("a.png", b"x")},
            **auth(self.student),
        )
        self.assertEqual(res.json()["code"], "invalid_image")
        res = self.client.post(
            "/api/v1/me/orders/",
            {"course": self.course.slug, "screenshot": image_file()},
            **auth(self.admin),  # buyurtmani faqat student / ota-ona beradi
        )
        self.assertEqual(res.status_code, 403)


class OrderAdminTests(PaymentTestCase):
    def setUp(self):
        super().setUp()
        self.order = services.create_order(
            payer=self.student, course=self.course, screenshot=image_file()
        )

    @override_settings(USE_X_ACCEL_REDIRECT=False)
    def test_screenshot_only_for_admin(self):
        url = f"/admin/payments/order/{self.order.pk}/screenshot/"
        self.client.force_login(self.admin)
        res = self.client.get(url)
        self.assertEqual(res.status_code, 200)
        self.assertIn("private", res["Cache-Control"])
        self.assertIn("no-store", res["Cache-Control"])
        self.client.force_login(self.student)  # staff emas → admin login sahifasiga
        res = self.client.get(url)
        self.assertEqual(res.status_code, 302)
        self.assertIn("/admin/login/", res["Location"])

    @override_settings(USE_X_ACCEL_REDIRECT=True)
    def test_screenshot_x_accel(self):
        self.client.force_login(self.admin)
        res = self.client.get(f"/admin/payments/order/{self.order.pk}/screenshot/")
        self.assertEqual(res["X-Accel-Redirect"], f"/protected-media/{self.order.screenshot.name}")

    def test_change_form_approves_via_service(self):
        self.client.force_login(self.admin)
        res = self.client.post(
            f"/admin/payments/order/{self.order.pk}/change/",
            {"status": "approved", "reject_reason": ""},
        )
        self.assertEqual(res.status_code, 302)
        self.order.refresh_from_db()
        self.assertEqual((self.order.status, self.order.reviewed_by), ("approved", self.admin))
        self.assertTrue(Enrollment.objects.filter(student=self.student).exists())

    def test_reject_without_reason_is_form_error(self):
        self.client.force_login(self.admin)
        res = self.client.post(
            f"/admin/payments/order/{self.order.pk}/change/",
            {"status": "rejected", "reject_reason": ""},
        )
        self.assertEqual(res.status_code, 200)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "pending")

    def test_bulk_approve_and_pending_first(self):
        other = make_user("+998900000006")
        old = services.create_order(payer=other, course=self.course, screenshot=image_file())
        services.approve_order(old, self.admin)
        self.client.force_login(self.admin)
        page = self.client.get("/admin/payments/order/").content.decode()
        link = "/admin/payments/order/{}/change/"
        self.assertLess(page.index(link.format(self.order.pk)), page.index(link.format(old.pk)))
        self.client.post(
            "/admin/payments/order/",
            {"action": "approve_selected", "_selected_action": [self.order.pk]},
        )
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "approved")
