import io
import shutil
import tempfile
from unittest.mock import AsyncMock, MagicMock, patch

from aiogram.fsm.context import FSMContext
from aiogram.fsm.storage.base import StorageKey
from aiogram.fsm.storage.memory import MemoryStorage
from asgiref.sync import async_to_sync
from django.core.files.uploadedfile import SimpleUploadedFile
from django.db import transaction
from django.test import TestCase, override_settings
from PIL import Image

from apps.courses import services as course_services
from apps.courses.models import Course, Enrollment, Lesson
from apps.payments import services as payment_services
from apps.payments.models import Order
from apps.users.models import ParentLink, User

from . import notify, orders

MEDIA = tempfile.mkdtemp()


def make_user(phone: str, role: str = User.Role.STUDENT, **extra) -> User:
    return User.objects.create(username=phone, phone=phone, role=role, **extra)


def image_file() -> SimpleUploadedFile:
    buffer = io.BytesIO()
    Image.new("RGB", (10, 10)).save(buffer, "PNG")
    return SimpleUploadedFile("chek.png", buffer.getvalue(), content_type="image/png")


@override_settings(
    MEDIA_ROOT=MEDIA,
    TELEGRAM_NOTIFY_SYNC=True,
    TELEGRAM_BOT_TOKEN="test-token",
    TELEGRAM_ADMIN_CHAT_ID="-100123",
    CERTIFICATE_BASE_URL="https://lesson24.uz/certificate/",
)
class NotifyTestCase(TestCase):
    @classmethod
    def tearDownClass(cls):
        super().tearDownClass()
        shutil.rmtree(MEDIA, ignore_errors=True)

    def setUp(self):
        self.student = make_user("+998900000002", telegram_id=2, first_name="Ali")
        self.parent = make_user("+998900000003", role=User.Role.PARENT, telegram_id=3)
        self.admin = make_user("+998900000004", role=User.Role.ADMIN, telegram_id=4)
        self.course = Course.objects.create(title="Python <3", price=50_000, is_published=True)
        patcher = patch.object(notify, "_api", return_value=True)
        self.api = patcher.start()
        self.addCleanup(patcher.stop)

    def sent(self) -> list[tuple[str, dict]]:
        return [(c.args[0], c.args[1]) for c in self.api.call_args_list]

    def create_order(self, payer=None, student=None) -> Order:
        with self.captureOnCommitCallbacks(execute=True):
            return payment_services.create_order(
                payer=payer or self.student,
                course=self.course,
                screenshot=image_file(),
                student=student,
            )


class NotifyTests(NotifyTestCase):
    def test_new_order_goes_to_admin_group_with_buttons(self):
        order = self.create_order()
        method, data = self.sent()[0]
        self.assertEqual((method, data["chat_id"]), ("sendPhoto", "-100123"))
        self.assertIn("50 000 so'm", data["caption"])
        self.assertIn("Python &lt;3", data["caption"])  # HTML escape
        self.assertEqual(
            data["reply_markup"]["inline_keyboard"][0][0]["callback_data"], f"ord:a:{order.pk}"
        )
        self.assertIn("photo", self.api.call_args.kwargs["files"])

    def test_photo_failure_falls_back_to_document(self):
        self.api.side_effect = [False, True]
        self.create_order()
        self.assertEqual([m for m, _ in self.sent()], ["sendPhoto", "sendDocument"])

    def test_approve_notifies_student_and_parent_payer(self):
        ParentLink.objects.create(parent=self.parent, student=self.student)
        order = self.create_order(payer=self.parent, student=self.student)
        self.api.reset_mock()
        with self.captureOnCommitCallbacks(execute=True):
            payment_services.approve_order(order, self.admin)
        chats = sorted(data["chat_id"] for _, data in self.sent())
        self.assertEqual(chats, [2, 3])
        self.assertIn("kursi ochildi", self.sent()[0][1]["text"])

    def test_reject_sends_reason(self):
        order = self.create_order()
        self.api.reset_mock()
        with self.captureOnCommitCallbacks(execute=True):
            payment_services.reject_order(order, self.admin, "Summa mos emas")
        [(_, data)] = self.sent()
        self.assertEqual(data["chat_id"], 2)
        self.assertIn("Summa mos emas", data["text"])

    def test_course_completed_notifies_student_and_parents(self):
        ParentLink.objects.create(parent=self.parent, student=self.student)
        lesson = Lesson.objects.create(course=self.course, order=1, title="Dars")
        enrollment = Enrollment.objects.create(student=self.student, course=self.course)
        with self.captureOnCommitCallbacks(execute=True):
            course_services.complete_lesson(enrollment, lesson)
        texts = {data["chat_id"]: data["text"] for _, data in self.sent()}
        self.assertEqual(set(texts), {2, 3})
        self.assertIn("https://lesson24.uz/certificate/", texts[2])
        self.assertIn("Farzandingiz", texts[3])

    def test_users_without_telegram_are_skipped(self):
        User.objects.filter(pk=self.student.pk).update(telegram_id=None)
        order = self.create_order()
        self.api.reset_mock()
        with self.captureOnCommitCallbacks(execute=True):
            payment_services.approve_order(order, self.admin)
        self.assertEqual(self.sent(), [])

    def test_nothing_sent_on_rollback(self):
        with self.captureOnCommitCallbacks(execute=True) as callbacks:
            try:
                with transaction.atomic():
                    payment_services.create_order(
                        payer=self.student, course=self.course, screenshot=image_file()
                    )
                    raise RuntimeError
            except RuntimeError:
                pass
        self.assertEqual(callbacks, [])
        self.api.assert_not_called()


class ApiTransportTests(TestCase):
    @override_settings(TELEGRAM_BOT_TOKEN="")
    def test_no_token_no_request(self):
        with patch.object(notify.httpx, "post") as post:
            self.assertFalse(notify.send_message(1, "x"))
        post.assert_not_called()

    @override_settings(TELEGRAM_BOT_TOKEN="t")
    def test_errors_are_swallowed(self):
        with patch.object(notify.httpx, "post", side_effect=notify.httpx.ConnectError("x")):
            self.assertFalse(notify.send_message(1, "x"))
        response = MagicMock()
        response.json.return_value = {"ok": False, "description": "chat not found"}
        with patch.object(notify.httpx, "post", return_value=response) as post:
            self.assertFalse(notify.send_message(1, "x", reply_markup={"a": 1}))
        self.assertEqual(post.call_args.kwargs["data"]["reply_markup"], '{"a": 1}')


class OrderButtonTests(NotifyTestCase):
    def setUp(self):
        super().setUp()
        self.order = self.create_order()
        self.state = FSMContext(
            storage=MemoryStorage(), key=StorageKey(bot_id=1, chat_id=-100123, user_id=4)
        )

    def callback(self, data: str, telegram_id: int = 4) -> MagicMock:
        cb = MagicMock()
        cb.data, cb.from_user.id = data, telegram_id
        cb.answer = AsyncMock()
        cb.message.caption = "🧾 Buyurtma"
        cb.message.message_id = 77
        cb.message.edit_caption = AsyncMock()
        cb.message.reply = AsyncMock()
        return cb

    def test_admin_approves(self):
        cb = self.callback(f"ord:a:{self.order.pk}")
        async_to_sync(orders.order_button)(cb, self.state)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "approved")
        self.assertIn("✅ Tasdiqladi", cb.message.edit_caption.call_args.kwargs["caption"])

        again = self.callback(f"ord:a:{self.order.pk}")
        async_to_sync(orders.order_button)(again, self.state)
        self.assertTrue(again.answer.call_args.kwargs["show_alert"])

    def test_non_admin_cannot_press(self):
        cb = self.callback(f"ord:a:{self.order.pk}", telegram_id=2)
        async_to_sync(orders.order_button)(cb, self.state)
        self.order.refresh_from_db()
        self.assertEqual(self.order.status, "pending")
        self.assertTrue(cb.answer.call_args.kwargs["show_alert"])

    def test_reject_with_reason(self):
        async_to_sync(orders.order_button)(self.callback(f"ord:r:{self.order.pk}"), self.state)
        self.assertEqual(async_to_sync(self.state.get_state)(), orders.RejectOrder.reason.state)
        message = MagicMock(text="Chek o'qilmaydi")
        message.from_user.id, message.chat.id = 4, -100123
        message.reply = AsyncMock()
        message.bot.edit_message_reply_markup = AsyncMock()
        async_to_sync(orders.reject_reason)(message, self.state)
        self.order.refresh_from_db()
        self.assertEqual(
            (self.order.status, self.order.reject_reason), ("rejected", "Chek o'qilmaydi")
        )
        message.bot.edit_message_reply_markup.assert_awaited_once()
        self.assertIsNone(async_to_sync(self.state.get_state)())


class CourseReviewNotifyTests(NotifyTestCase):
    def setUp(self):
        super().setUp()
        self.owner = make_user("+998900000009", role=User.Role.TEACHER, telegram_id=9)
        self.draft = Course.objects.create(owner=self.owner, title="Teacher kursi", price=0)
        Lesson.objects.create(course=self.draft, order=1, title="D1")

    def test_submit_goes_to_admin_group(self):
        with self.captureOnCommitCallbacks(execute=True):
            course_services.submit_for_review(self.draft)
        [(_, data)] = self.sent()
        self.assertEqual(data["chat_id"], "-100123")
        self.assertIn("tekshiruvga yuborildi", data["text"])

    def test_review_result_goes_to_teacher(self):
        with self.captureOnCommitCallbacks(execute=True):
            course_services.reject_course(self.draft, "Video sifatsiz")
        [(_, data)] = self.sent()
        self.assertEqual(data["chat_id"], 9)
        self.assertIn("Video sifatsiz", data["text"])
        self.api.reset_mock()
        with self.captureOnCommitCallbacks(execute=True):
            course_services.approve_course(self.draft)
        self.assertIn("tasdiqlandi", self.sent()[0][1]["text"])
