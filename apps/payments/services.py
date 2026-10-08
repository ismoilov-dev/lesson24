"""Manual payments: create an order with a receipt screenshot; admin approves or rejects."""

from django.conf import settings
from django.core.files.uploadedfile import UploadedFile
from django.db import IntegrityError, transaction
from django.utils import timezone

from apps.courses.models import Course, Enrollment
from apps.errors import ServiceError
from apps.files import validate_image
from apps.users.models import ParentLink, User
from bot import notify

from .models import Order


def validate_screenshot(file: UploadedFile) -> None:
    """To'lov cheki: haqiqiy JPG/PNG/WEBP, maks. 5 MB."""
    validate_image(file, settings.PAYMENT_SCREENSHOT_MAX_BYTES)


def payment_info(course: Course) -> dict:
    return {
        "card_number": settings.PAYMENT_CARD_NUMBER,
        "card_holder": settings.PAYMENT_CARD_HOLDER,
        "amount": course.price,
    }


def _resolve_student(payer: User, student: User | None) -> User:
    if payer.is_student:
        if student is not None and student.pk != payer.pk:
            raise ServiceError("Student faqat o'zi uchun sotib oladi.", code="invalid_student")
        return payer
    if payer.is_parent:
        if student is None:
            raise ServiceError("Farzandni tanlang.", code="student_required")
        if not ParentLink.objects.filter(parent=payer, student=student).exists():
            raise ServiceError("Bu farzand sizga bog'lanmagan.", code="invalid_student")
        return student
    raise ServiceError("Buyurtmani student yoki ota-ona beradi.", code="forbidden", status=403)


def create_order(
    *, payer: User, course: Course, screenshot: UploadedFile, student: User | None = None
) -> Order:
    if not course.is_published:
        raise ServiceError("Kurs topilmadi.", code="not_found", status=404)
    student = _resolve_student(payer, student)
    if Enrollment.objects.filter(student=student, course=course).exists():
        raise ServiceError("Bu kurs allaqachon ochilgan.", code="already_enrolled")
    validate_screenshot(screenshot)
    duplicate = ServiceError("Bu kurs uchun buyurtma ko'rib chiqilmoqda.", code="pending_exists")
    if Order.objects.filter(student=student, course=course, status=Order.Status.PENDING).exists():
        raise duplicate
    try:
        with transaction.atomic():
            order = Order.objects.create(
                user=payer,
                student=student,
                course=course,
                amount=course.price,
                screenshot=screenshot,
            )
    except IntegrityError as exc:  # parallel so'rov: partial unique constraint ushladi
        raise duplicate from exc
    notify.new_order(order.pk)
    return order


def _lock_pending(order_id: int) -> Order:
    order = Order.objects.select_for_update().get(pk=order_id)
    if order.status != Order.Status.PENDING:
        raise ServiceError("Buyurtma allaqachon ko'rib chiqilgan.", code="already_reviewed")
    return order


@transaction.atomic
def approve_order(order: Order, reviewer: User) -> Order:
    """One transaction: order approved + enrollment created."""
    order = _lock_pending(order.pk)
    order.status = Order.Status.APPROVED
    order.reviewed_by = reviewer
    order.reviewed_at = timezone.now()
    order.save(update_fields=["status", "reviewed_by", "reviewed_at"])
    Enrollment.objects.get_or_create(student_id=order.student_id, course_id=order.course_id)
    notify.order_reviewed(order.pk)
    return order


@transaction.atomic
def reject_order(order: Order, reviewer: User, reason: str) -> Order:
    reason = (reason or "").strip()
    if not reason:
        raise ServiceError("Rad etish sababini yozing.", code="reason_required")
    order = _lock_pending(order.pk)
    order.status = Order.Status.REJECTED
    order.reject_reason = reason[:500]
    order.reviewed_by = reviewer
    order.reviewed_at = timezone.now()
    order.save(update_fields=["status", "reject_reason", "reviewed_by", "reviewed_at"])
    notify.order_reviewed(order.pk)
    return order
