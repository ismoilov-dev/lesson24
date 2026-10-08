import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
from django.utils.text import slugify

from .video import normalize_video_id


class Course(models.Model):
    """Kurs. `owner` — teacher (bo'sh bo'lsa — platforma/admin kursi).

    Teacher kursi: draft → (submit) pending → admin: approved (nashr) | rejected (izoh bilan).
    Katalogda ko'rinish manbai — `is_published`.
    """

    MAX_PRICE = 100_000

    class ReviewStatus(models.TextChoices):
        DRAFT = "draft", "Qoralama"
        PENDING = "pending", "Tekshiruvda"
        APPROVED = "approved", "Tasdiqlangan"
        REJECTED = "rejected", "Qaytarilgan"

    title = models.CharField(max_length=200)
    slug = models.SlugField(max_length=220, unique=True, blank=True)
    description = models.TextField(blank=True)
    # Muqova rasmi ImgBB'da; bu yerda faqat URL (apps/imgbb.py)
    cover_url = models.URLField(max_length=500, blank=True)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        null=True,
        blank=True,
        related_name="owned_courses",
        limit_choices_to={"role": "teacher"},
        help_text="Teacher. Bo'sh — platforma (admin) kursi",
    )
    # Owner bo'lmagan (admin) kurslar uchun ko'rsatiladigan ism
    instructor_name = models.CharField(max_length=150, blank=True)
    review_status = models.CharField(
        max_length=10, choices=ReviewStatus.choices, default=ReviewStatus.DRAFT, db_index=True
    )
    review_note = models.CharField("Admin izohi", max_length=500, blank=True)
    price = models.PositiveIntegerField(help_text="so'm", validators=[MaxValueValidator(MAX_PRICE)])
    is_published = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["is_published", "-created_at"], name="course_published_idx"),
        ]

    def __str__(self) -> str:
        return self.title

    def save(self, *args, **kwargs) -> None:
        if not self.slug:
            self.slug = self._unique_slug()
        super().save(*args, **kwargs)

    def _unique_slug(self) -> str:
        base = slugify(self.title)[:200] or "course"
        slug = base
        while Course.objects.filter(slug=slug).exclude(pk=self.pk).exists():
            slug = f"{base}-{uuid.uuid4().hex[:6]}"
        return slug

    @property
    def display_instructor(self) -> str:
        """Katalog/sertifikat uchun: teacher ismi, bo'lmasa `instructor_name`.

        `owner` ni select_related qiling — aks holda har kurs uchun so'rov ketadi.
        """
        if self.owner_id and (name := self.owner.get_full_name()):
            return name
        return self.instructor_name


class Lesson(models.Model):
    course = models.ForeignKey(Course, on_delete=models.CASCADE, related_name="lessons")
    order = models.PositiveSmallIntegerField()
    title = models.CharField(max_length=200)
    description = models.TextField(blank=True)
    # Video Django'da saqlanmaydi — faqat provayderdagi ID (Google Drive fayl ID / Bunny video ID).
    # Admin Drive havolasini to'liq yopishtirishi mumkin — clean()/serializer ID ga aylantiradi.
    video_id = models.CharField(
        "Video (Google Drive havolasi)",
        max_length=255,
        blank=True,
        help_text="Share → Anyone with the link → havolani to'liq yopishtiring",
    )
    duration_min = models.PositiveSmallIntegerField(default=0)
    # [{"title": "...", "url": "..."}]
    materials = models.JSONField(default=list, blank=True)
    is_free_preview = models.BooleanField(default=False)

    class Meta:
        ordering = ["course", "order"]
        constraints = [
            # DEFERRED: tartibni almashtirish (reorder) bitta tranzaksiyada to'qnashuvsiz ishlaydi
            models.UniqueConstraint(
                fields=["course", "order"],
                name="lesson_course_order_unique",
                deferrable=models.Deferrable.DEFERRED,
            ),
        ]

    def __str__(self) -> str:
        return f"{self.order}. {self.title}"

    def clean(self) -> None:
        try:
            self.video_id = normalize_video_id(self.video_id)
        except ValueError as exc:
            raise ValidationError({"video_id": str(exc)}) from exc


class Quiz(models.Model):
    """Dars oxiridagi test. Bitta darsga bitta test."""

    PASS_PERCENT = 60

    lesson = models.OneToOneField(Lesson, on_delete=models.CASCADE, related_name="quiz")

    class Meta:
        verbose_name_plural = "quizzes"

    def __str__(self) -> str:
        return f"Test: {self.lesson}"


class Question(models.Model):
    quiz = models.ForeignKey(Quiz, on_delete=models.CASCADE, related_name="questions")
    order = models.PositiveSmallIntegerField(default=0)
    text = models.TextField()

    class Meta:
        ordering = ["quiz", "order", "id"]

    def __str__(self) -> str:
        return self.text[:80]


class Answer(models.Model):
    question = models.ForeignKey(Question, on_delete=models.CASCADE, related_name="answers")
    text = models.CharField(max_length=500)
    is_correct = models.BooleanField(default=False)

    class Meta:
        ordering = ["question", "id"]

    def __str__(self) -> str:
        return self.text[:80]


class Enrollment(models.Model):
    student = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="enrollments"
    )
    # PROTECT: o'quvchisi bor (pul to'langan) kursni tasodifan o'chirib bo'lmaydi
    course = models.ForeignKey(Course, on_delete=models.PROTECT, related_name="enrollments")
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["-created_at"]
        constraints = [
            models.UniqueConstraint(fields=["student", "course"], name="enrollment_unique"),
        ]

    def __str__(self) -> str:
        return f"{self.student} — {self.course}"


class LessonProgress(models.Model):
    """Qator dars tugatilganda yaratiladi; `completed_at` bo'lsa — dars tugagan.

    Test bo'lgan dars faqat `quiz_score >= Quiz.PASS_PERCENT` bo'lganda tugaydi, shuning uchun
    kurs tugashi = tugagan qatorlar soni == darslar soni (bitta COUNT so'rovi).
    """

    enrollment = models.ForeignKey(Enrollment, on_delete=models.CASCADE, related_name="progress")
    lesson = models.ForeignKey(Lesson, on_delete=models.CASCADE, related_name="progress")
    quiz_score = models.PositiveSmallIntegerField(
        null=True, blank=True, validators=[MinValueValidator(0), MaxValueValidator(100)]
    )
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name_plural = "lesson progress"
        constraints = [
            models.UniqueConstraint(fields=["enrollment", "lesson"], name="progress_unique"),
        ]

    def __str__(self) -> str:
        return f"{self.enrollment} — {self.lesson}"
