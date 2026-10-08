"""Kontent boshqaruvi (admin va teacher uchun umumiy): kurs/dars tahriri, test muharriri,
o'quvchilar progressi."""

from rest_framework import serializers

from apps.imgbb import upload_image
from apps.users.models import User

from .models import Course, Lesson, Quiz
from .video import lesson_video_url, normalize_video_id


class CourseManageSerializer(serializers.ModelSerializer):
    """Kurs bitta formada: nom, tavsif, muqova rasmi (fayl → ImgBB), narx.

    Teacher va admin uchun umumiy; `is_published` bu yerda faqat o'qish uchun —
    nashr qilish admin tekshiruvi orqali (approve). Admin varianti pastda kengaytiradi.
    """

    cover = serializers.FileField(
        write_only=True,
        required=False,
        help_text="Muqova RASMI (JPG/PNG/WEBP, maks. 5 MB) — ImgBB'ga yuklanadi, "
        "javobda `cover_url` qaytadi",
    )
    cover_url = serializers.URLField(read_only=True, help_text="Muqova rasmi URL (ImgBB)")
    slug = serializers.SlugField(
        read_only=True, help_text="Saytdagi manzil (lesson24.uz/courses/<slug>), nomdan yasaladi"
    )
    lessons_count = serializers.IntegerField(read_only=True)
    students_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = Course
        fields = [
            "id",
            "title",
            "description",
            "cover",
            "cover_url",
            "price",
            "slug",
            "is_published",
            "review_status",
            "review_note",
            "lessons_count",
            "students_count",
            "created_at",
            "updated_at",
        ]
        read_only_fields = [
            "is_published",
            "review_status",
            "review_note",
            "created_at",
            "updated_at",
        ]
        extra_kwargs = {
            "price": {"help_text": "So'm, 0–100 000. 0 — bepul kurs"},
            "review_status": {
                "help_text": "draft → pending (tekshiruvda) → approved (nashr) | rejected"
            },
            "review_note": {"help_text": "Admin izohi (qaytarilganda)"},
        }

    def _upload_cover(self, validated_data: dict) -> dict:
        cover = validated_data.pop("cover", None)
        if cover is not None:
            title = validated_data.get("title") or getattr(self.instance, "title", "")
            validated_data["cover_url"] = upload_image(cover, name=title)
        return validated_data

    def create(self, validated_data: dict) -> Course:
        return super().create(self._upload_cover(validated_data))

    def update(self, instance: Course, validated_data: dict) -> Course:
        return super().update(instance, self._upload_cover(validated_data))


class TeacherCourseSerializer(CourseManageSerializer):
    """Teacher: o'z kursi. Nashr — `submit/` → admin tasdig'i."""


class AdminCourseSerializer(CourseManageSerializer):
    """Admin: teacher biriktirish, o'qituvchi ismi (platforma kursi), to'g'ridan-to'g'ri nashr."""

    owner = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.filter(role=User.Role.TEACHER),
        required=False,
        allow_null=True,
        help_text="Teacher ID. Bo'sh — platforma (admin) kursi",
    )
    owner_name = serializers.CharField(source="owner.get_full_name", read_only=True, default="")

    class Meta(CourseManageSerializer.Meta):
        fields = [*CourseManageSerializer.Meta.fields, "owner", "owner_name", "instructor_name"]
        read_only_fields = ["review_status", "review_note", "created_at", "updated_at"]
        extra_kwargs = {
            **CourseManageSerializer.Meta.extra_kwargs,
            "instructor_name": {
                "help_text": "Platforma kursi uchun ko'rsatiladigan o'qituvchi ismi"
            },
            "is_published": {"help_text": "true — katalogda ko'rinadi"},
        }


class MaterialSerializer(serializers.Serializer):
    title = serializers.CharField(max_length=200)
    url = serializers.URLField()


class VideoUrlField(serializers.CharField):
    """Yozish: Google Drive havolasi (yoki ID) → `video_id`. O'qish: player havolasi."""

    def to_internal_value(self, data) -> str:
        try:
            return normalize_video_id(super().to_internal_value(data))
        except ValueError as exc:
            raise serializers.ValidationError(str(exc)) from exc

    def to_representation(self, value) -> str | None:
        return lesson_video_url(value)


class LessonManageSerializer(serializers.ModelSerializer):
    """Dars: nom, video URL (Google Drive), tavsif va qolganlari — bitta JSON."""

    video_url = VideoUrlField(
        source="video_id",
        required=False,
        allow_blank=True,
        max_length=500,
        help_text="Google Drive havolasi (Share → Anyone with the link). "
        "Javobda — studentga beriladigan player havolasi",
    )
    materials = MaterialSerializer(many=True, required=False)
    order = serializers.IntegerField(
        min_value=1, required=False, help_text="Bo'sh bo'lsa — oxiriga qo'shiladi"
    )
    has_quiz = serializers.SerializerMethodField()

    class Meta:
        model = Lesson
        fields = [
            "id",
            "course",
            "title",
            "video_url",
            "description",
            "duration_min",
            "is_free_preview",
            "materials",
            "order",
            "has_quiz",
        ]
        read_only_fields = ["course"]
        extra_kwargs = {
            "duration_min": {"help_text": "Davomiyligi, daqiqada"},
            "is_free_preview": {"help_text": "true — sotib olmaganlar ham ko'radi"},
        }

    def get_has_quiz(self, obj) -> bool:
        return hasattr(obj, "quiz")

    def validate_order(self, value: int) -> int:
        course = self.context["course"] if self.instance is None else self.instance.course
        clash = Lesson.objects.filter(course=course, order=value)
        if self.instance is not None:
            clash = clash.exclude(pk=self.instance.pk)
        if clash.exists():
            raise serializers.ValidationError("Bu tartib raqami band. Reorder'dan foydalaning.")
        return value


class ReorderSerializer(serializers.Serializer):
    lesson_ids = serializers.ListField(child=serializers.IntegerField(), min_length=1)


class AnswerEditorSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    text = serializers.CharField(max_length=500)
    is_correct = serializers.BooleanField(default=False)


class QuestionEditorSerializer(serializers.Serializer):
    id = serializers.IntegerField(read_only=True)
    text = serializers.CharField()
    answers = AnswerEditorSerializer(many=True, min_length=2, max_length=10)

    def validate_answers(self, answers: list[dict]) -> list[dict]:
        # Student har savolga bitta javob tanlaydi — demak to'g'ri javob aynan bitta
        if sum(a["is_correct"] for a in answers) != 1:
            raise serializers.ValidationError("Aynan bitta to'g'ri javob belgilang.")
        return answers


class QuizEditorSerializer(serializers.Serializer):
    """Admin uchun: `is_correct` bilan. PUT butun testni almashtiradi."""

    pass_percent = serializers.SerializerMethodField()
    questions = QuestionEditorSerializer(many=True, min_length=1, max_length=100)

    def get_pass_percent(self, obj) -> int:
        return Quiz.PASS_PERCENT


class CourseStudentSerializer(serializers.Serializer):
    student_id = serializers.IntegerField(source="student.id")
    full_name = serializers.CharField(source="student.get_full_name")
    lessons_total = serializers.IntegerField()
    lessons_done = serializers.IntegerField()
    progress_percent = serializers.SerializerMethodField()
    created_at = serializers.DateTimeField()
    completed_at = serializers.DateTimeField(allow_null=True)

    def get_progress_percent(self, obj) -> int:
        return round(obj.lessons_done * 100 / obj.lessons_total) if obj.lessons_total else 0
