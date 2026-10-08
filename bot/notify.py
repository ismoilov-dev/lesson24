"""Telegram notifications sent from Django (web process) without Celery.

- Services call `notify.<event>(id)` inside `transaction.on_commit`, so nothing is sent when the
  transaction rolls back.
- Sending runs in a small background thread pool: the HTTP response does not wait for Telegram.
  Each task loads fresh data by ID and closes its DB connection afterwards.
- Failures are logged, never raised: a Telegram outage must not break payments or lessons.
"""

import json
import logging
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from html import escape

import httpx
from django.conf import settings
from django.db import close_old_connections, transaction

logger = logging.getLogger(__name__)
_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="tg-notify")

APPROVE, REJECT = "a", "r"


def order_keyboard(order_id: int) -> dict:
    return {
        "inline_keyboard": [
            [
                {"text": "✅ Tasdiqlash", "callback_data": f"ord:{APPROVE}:{order_id}"},
                {"text": "❌ Rad etish", "callback_data": f"ord:{REJECT}:{order_id}"},
            ]
        ]
    }


# --- transport --------------------------------------------------------------------------


def _api(method: str, data: dict, files: dict | None = None) -> bool:
    token = settings.TELEGRAM_BOT_TOKEN
    if not token:
        return False
    if "reply_markup" in data:
        data = {**data, "reply_markup": json.dumps(data["reply_markup"])}
    try:
        response = httpx.post(
            f"https://api.telegram.org/bot{token}/{method}", data=data, files=files, timeout=5
        )
        body = response.json()
    except (httpx.HTTPError, ValueError) as exc:
        logger.warning("Telegram %s failed: %s", method, type(exc).__name__)
        return False
    if not body.get("ok"):
        logger.warning("Telegram %s error: %s", method, body.get("description"))
        return False
    return True


def send_message(chat_id: int | str, text: str, reply_markup: dict | None = None) -> bool:
    data = {"chat_id": chat_id, "text": text, "parse_mode": "HTML"}
    if reply_markup:
        data["reply_markup"] = reply_markup
    return _api("sendMessage", data)


def _run(task: Callable[[], None]) -> None:
    try:
        task()
    except Exception:
        logger.exception("Notification task failed")
    finally:
        close_old_connections()


def _schedule(task: Callable[[], None]) -> None:
    """After commit; in a thread unless TELEGRAM_NOTIFY_SYNC (tests)."""

    def submit() -> None:
        if settings.TELEGRAM_NOTIFY_SYNC:
            try:
                task()
            except Exception:
                logger.exception("Notification task failed")
        else:
            _executor.submit(_run, task)

    transaction.on_commit(submit)


def _send_to_users(users, text: str) -> None:
    for user in users:
        if user and user.telegram_id:
            send_message(user.telegram_id, text)


# --- events -----------------------------------------------------------------------------


def order_caption(order) -> str:
    student = order.student
    lines = [
        f"🧾 <b>Buyurtma #{order.pk}</b>",
        f"Kurs: {escape(order.course.title)}",
        f"Summa: {order.amount:,} so'm".replace(",", " "),
        f"Student: {escape(str(student))} ({student.phone})",
    ]
    if order.user_id != order.student_id:
        lines.append(f"To'lovchi (ota-ona): {escape(str(order.user))} ({order.user.phone})")
    return "\n".join(lines)


def _new_order(order_id: int) -> None:
    from apps.payments.models import Order

    chat_id = settings.TELEGRAM_ADMIN_CHAT_ID
    if not chat_id:
        return
    order = Order.objects.select_related("course", "student", "user").get(pk=order_id)
    data = {
        "chat_id": chat_id,
        "caption": order_caption(order),
        "parse_mode": "HTML",
        "reply_markup": order_keyboard(order.pk),
    }
    with order.screenshot.open("rb") as file:
        content = file.read()
    name = order.screenshot.name.rsplit("/", 1)[-1]
    # sendPhoto ba'zi formatlarni (masalan, katta WEBP) rad etadi — unda hujjat sifatida
    if not _api("sendPhoto", data, files={"photo": (name, content)}):
        _api("sendDocument", data, files={"document": (name, content)})


def _order_reviewed(order_id: int) -> None:
    from apps.payments.models import Order

    order = Order.objects.select_related("course", "student", "user").get(pk=order_id)
    course = escape(order.course.title)
    if order.status == Order.Status.APPROVED:
        text = f"🎉 To'lov tasdiqlandi! <b>{course}</b> kursi ochildi."
    else:
        text = (
            f"❌ <b>{course}</b> kursi uchun to'lov rad etildi.\n"
            f"Sabab: {escape(order.reject_reason)}"
        )
    recipients = {order.student_id: order.student, order.user_id: order.user}
    _send_to_users(recipients.values(), text)


def _course_completed(enrollment_id: int) -> None:
    from apps.certificates.models import Certificate
    from apps.courses.models import Enrollment
    from apps.users.models import User

    enrollment = Enrollment.objects.select_related("student", "course").get(pk=enrollment_id)
    certificate = Certificate.objects.filter(enrollment=enrollment).first()
    course = escape(enrollment.course.title)
    link = f"\n\n📜 Sertifikat: {certificate.url}" if certificate else ""
    _send_to_users(
        [enrollment.student], f"🏆 Tabriklaymiz! <b>{course}</b> kursini tugatdingiz.{link}"
    )
    parents = User.objects.filter(children_links__student=enrollment.student)
    child = escape(str(enrollment.student))
    _send_to_users(parents, f"🏆 Farzandingiz {child} <b>{course}</b> kursini tugatdi.{link}")


def _course_submitted(course_id: int) -> None:
    from apps.courses.models import Course

    chat_id = settings.TELEGRAM_ADMIN_CHAT_ID
    if not chat_id:
        return
    course = Course.objects.select_related("owner").get(pk=course_id)
    send_message(
        chat_id,
        f"📝 <b>Kurs tekshiruvga yuborildi</b>\n"
        f"Kurs: {escape(course.title)} (#{course.pk})\n"
        f"O'qituvchi: {escape(str(course.owner))}\n"
        f"Admin panel: admin/courses/?review_status=pending",
    )


def _course_reviewed(course_id: int) -> None:
    from apps.courses.models import Course

    course = Course.objects.select_related("owner").get(pk=course_id)
    title = escape(course.title)
    if course.review_status == Course.ReviewStatus.APPROVED:
        text = f"✅ <b>{title}</b> kursingiz tasdiqlandi va katalogda nashr qilindi."
    else:
        text = f"↩️ <b>{title}</b> kursingiz qaytarildi.\nIzoh: {escape(course.review_note)}"
    _send_to_users([course.owner], text)


def course_submitted(course_id: int) -> None:
    _schedule(lambda: _course_submitted(course_id))


def course_reviewed(course_id: int) -> None:
    _schedule(lambda: _course_reviewed(course_id))


def new_order(order_id: int) -> None:
    _schedule(lambda: _new_order(order_id))


def order_reviewed(order_id: int) -> None:
    _schedule(lambda: _order_reviewed(order_id))


def course_completed(enrollment_id: int) -> None:
    _schedule(lambda: _course_completed(enrollment_id))
