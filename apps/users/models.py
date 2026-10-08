from datetime import timedelta

from django.contrib.auth.models import AbstractUser
from django.db import models
from django.db.models import Q
from django.utils import timezone


class User(AbstractUser):
    class Role(models.TextChoices):
        STUDENT = "student", "Student"
        PARENT = "parent", "Ota-ona"
        TEACHER = "teacher", "O'qituvchi"
        ADMIN = "admin", "Admin"

    class Language(models.TextChoices):
        UZ = "uz", "O'zbekcha"
        RU = "ru", "Русский"
        EN = "en", "English"

    STAFF_ROLES = frozenset({Role.ADMIN})

    role = models.CharField(
        max_length=10, choices=Role.choices, default=Role.STUDENT, db_index=True
    )
    telegram_id = models.BigIntegerField(unique=True, null=True, blank=True)
    # "" = telefon yo'q (masalan, createsuperuser); unikallik faqat bo'sh bo'lmaganlar uchun
    phone = models.CharField(max_length=16, blank=True, default="")
    language = models.CharField(max_length=2, choices=Language.choices, default=Language.UZ)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["phone"], condition=~Q(phone=""), name="users_user_phone_unique"
            ),
        ]

    def __str__(self) -> str:
        return self.get_full_name() or self.phone or self.username

    def save(self, *args, **kwargs) -> None:
        # Admin Django Admin'ga kira oladi; rol pasaytirilsa — kirish ham olinadi.
        self.is_staff = self.is_superuser or self.role in self.STAFF_ROLES
        update_fields = kwargs.get("update_fields")
        if update_fields is not None and "role" in update_fields:
            kwargs["update_fields"] = {*update_fields, "is_staff"}
        super().save(*args, **kwargs)

    @property
    def is_student(self) -> bool:
        return self.role == self.Role.STUDENT

    @property
    def is_parent(self) -> bool:
        return self.role == self.Role.PARENT

    @property
    def is_teacher(self) -> bool:
        return self.role == self.Role.TEACHER

    @property
    def is_admin_role(self) -> bool:
        return self.role == self.Role.ADMIN or self.is_superuser


class OneTimeCode(models.Model):
    """Bir martalik kod: login (6 raqam, 10 daqiqa) va ota-ona taklifi (8 belgi, 24 soat)."""

    class Purpose(models.TextChoices):
        LOGIN = "login", "Login"
        PARENT_LINK = "parent_link", "Ota-ona taklifi"

    TTL = {
        Purpose.LOGIN: timedelta(minutes=10),
        Purpose.PARENT_LINK: timedelta(hours=24),
    }
    MAX_ATTEMPTS = 5

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="one_time_codes")
    purpose = models.CharField(max_length=16, choices=Purpose.choices)
    code = models.CharField(max_length=8)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            # login: foydalanuvchining oxirgi faol kodi
            models.Index(fields=["user", "purpose", "-created_at"], name="otc_user_purpose_idx"),
            # ota-ona taklifi: kod bo'yicha qidirish
            models.Index(fields=["purpose", "code"], name="otc_purpose_code_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.get_purpose_display()} — {self.user}"

    @property
    def is_active(self) -> bool:
        return (
            self.used_at is None
            and self.expires_at > timezone.now()
            and self.attempts < self.MAX_ATTEMPTS
        )


class ParentLink(models.Model):
    parent = models.ForeignKey(User, on_delete=models.CASCADE, related_name="children_links")
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name="parent_links")
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(fields=["parent", "student"], name="parentlink_unique"),
        ]

    def __str__(self) -> str:
        return f"{self.parent} → {self.student}"


class TeacherProfile(models.Model):
    """O'qituvchining ommaviy profili (kurs sahifasida ko'rinadi). Keyin kengaytiriladi."""

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="teacher_profile")
    specialization = models.CharField("Mutaxassislik", max_length=200, blank=True)
    bio = models.TextField(blank=True)
    # Rasm ImgBB'da; bu yerda faqat URL
    photo_url = models.URLField(max_length=500, blank=True)
    updated_at = models.DateTimeField(auto_now=True)

    def __str__(self) -> str:
        return f"Profil: {self.user}"
