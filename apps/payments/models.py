from django.conf import settings
from django.db import models
from django.db.models import Q

from apps.uploads import UploadTo


class Order(models.Model):
    """Qo'lda to'lov: karta orqali o'tkazma + chek skrinshoti, admin tasdiqlaydi.

    Keyin Payme/Click qo'shilganda model saqlanadi — faqat `provider`, `provider_txn_id` qo'shiladi.
    """

    class Status(models.TextChoices):
        PENDING = "pending", "Kutilmoqda"
        APPROVED = "approved", "Tasdiqlandi"
        REJECTED = "rejected", "Rad etildi"

    # PROTECT: moliyaviy yozuvlar foydalanuvchi/kurs o'chirilganda yo'qolmasin
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="orders",
        help_text="To'lovchi (student yoki ota-ona)",
    )
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="student_orders",
        help_text="Kurs kimga ochiladi",
    )
    course = models.ForeignKey("courses.Course", on_delete=models.PROTECT, related_name="orders")
    amount = models.PositiveIntegerField(help_text="Buyurtma paytidagi narx (so'm)")
    # media/payments/ — ommaviy emas, faqat admin ko'radi (X-Accel-Redirect)
    screenshot = models.ImageField(upload_to=UploadTo("payments"))
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    reject_reason = models.CharField(max_length=500, blank=True)
    reviewed_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="+",
    )
    reviewed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [models.Index(fields=["status", "-created_at"], name="order_status_idx")]
        constraints = [
            # Bir student + kurs uchun bir vaqtda faqat bitta pending (race'da ham DB kafolati)
            models.UniqueConstraint(
                fields=["student", "course"],
                condition=Q(status="pending"),
                name="order_one_pending_per_student_course",
            ),
        ]

    def __str__(self) -> str:
        return f"#{self.pk} {self.course} — {self.student}"


# Admin ro'yxatlari uchun: pending'lar tepada
PENDING_FIRST = models.Case(
    models.When(status=Order.Status.PENDING, then=models.Value(0)),
    default=models.Value(1),
    output_field=models.IntegerField(),
)
