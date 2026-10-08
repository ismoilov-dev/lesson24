from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from .models import Course, Enrollment, Lesson
from .video import lesson_video_url, video_type


class CourseShortSerializer(serializers.ModelSerializer):
    instructor_name = serializers.CharField(source="display_instructor", read_only=True)

    class Meta:
        model = Course
        fields = ["id", "slug", "title", "cover_url", "instructor_name"]


class CourseListSerializer(serializers.ModelSerializer):
    instructor_name = serializers.CharField(
        source="display_instructor", read_only=True, help_text="Teacher ismi (yoki admin kiritgan)"
    )
    lessons_count = serializers.IntegerField()
    duration_min = serializers.IntegerField()

    class Meta:
        model = Course
        fields = [
            "id",
            "slug",
            "title",
            "cover_url",
            "price",
            "instructor_name",
            "lessons_count",
            "duration_min",
        ]


class LessonShortSerializer(serializers.ModelSerializer):
    has_quiz = serializers.BooleanField()

    class Meta:
        model = Lesson
        fields = ["id", "order", "title", "duration_min", "is_free_preview", "has_quiz"]


class InstructorSerializer(serializers.Serializer):
    name = serializers.CharField()
    specialization = serializers.CharField()
    bio = serializers.CharField()
    photo_url = serializers.CharField()


class CourseDetailSerializer(CourseListSerializer):
    instructor = serializers.SerializerMethodField()
    lessons = LessonShortSerializer(many=True)
    is_enrolled = serializers.BooleanField()

    class Meta(CourseListSerializer.Meta):
        fields = [
            *CourseListSerializer.Meta.fields,
            "description",
            "instructor",
            "lessons",
            "is_enrolled",
        ]

    @extend_schema_field(InstructorSerializer)
    def get_instructor(self, obj) -> dict:
        profile = getattr(obj.owner, "teacher_profile", None) if obj.owner_id else None
        return {
            "name": obj.display_instructor,
            "specialization": profile.specialization if profile else "",
            "bio": profile.bio if profile else "",
            "photo_url": profile.photo_url if profile else "",
        }


class AnswerPublicSerializer(serializers.Serializer):
    """`is_correct` clientga hech qachon berilmaydi."""

    id = serializers.IntegerField()
    text = serializers.CharField()


class QuestionPublicSerializer(serializers.Serializer):
    id = serializers.IntegerField()
    text = serializers.CharField()
    answers = AnswerPublicSerializer(many=True)


class QuizPublicSerializer(serializers.Serializer):
    pass_percent = serializers.SerializerMethodField()
    questions = QuestionPublicSerializer(many=True)

    def get_pass_percent(self, obj) -> int:
        return obj.PASS_PERCENT


class LessonProgressSerializer(serializers.Serializer):
    quiz_score = serializers.IntegerField(allow_null=True)
    completed_at = serializers.DateTimeField(allow_null=True)


class LessonDetailSerializer(serializers.ModelSerializer):
    course = serializers.SlugRelatedField(slug_field="slug", read_only=True)
    video_url = serializers.SerializerMethodField()
    video_type = serializers.SerializerMethodField(
        help_text="iframe — Drive player (<iframe src>); hls — hls.js"
    )
    quiz = serializers.SerializerMethodField()
    progress = serializers.SerializerMethodField()

    class Meta:
        model = Lesson
        fields = [
            "id",
            "course",
            "order",
            "title",
            "description",
            "duration_min",
            "materials",
            "is_free_preview",
            "video_url",
            "video_type",
            "quiz",
            "progress",
        ]

    def get_video_url(self, obj) -> str | None:
        return lesson_video_url(obj.video_id)

    def get_video_type(self, obj) -> str | None:
        return video_type() if obj.video_id else None

    @extend_schema_field(QuizPublicSerializer(allow_null=True))
    def get_quiz(self, obj):
        quiz = getattr(obj, "quiz", None)
        return QuizPublicSerializer(quiz).data if quiz else None

    @extend_schema_field(LessonProgressSerializer(allow_null=True))
    def get_progress(self, obj):
        score = getattr(obj, "progress_quiz_score", None)
        completed_at = getattr(obj, "progress_completed_at", None)
        if score is None and completed_at is None:
            return None
        return {"quiz_score": score, "completed_at": completed_at}


class CompleteLessonSerializer(serializers.Serializer):
    answers = serializers.ListField(
        child=serializers.IntegerField(min_value=1),
        required=False,
        default=list,
        max_length=200,
        help_text="Tanlangan javoblar ID lari (har bir savolga bittadan)",
    )


class CompletionResultSerializer(serializers.Serializer):
    lesson_completed = serializers.BooleanField()
    quiz_score = serializers.IntegerField(allow_null=True)
    passed = serializers.BooleanField()
    course_completed = serializers.BooleanField()
    certificate_uid = serializers.UUIDField(allow_null=True)


class MyCourseSerializer(serializers.Serializer):
    course = CourseShortSerializer()
    lessons_total = serializers.IntegerField()
    lessons_done = serializers.IntegerField()
    progress_percent = serializers.SerializerMethodField()
    next_lesson_id = serializers.IntegerField(allow_null=True)
    created_at = serializers.DateTimeField()
    completed_at = serializers.DateTimeField(allow_null=True)

    def get_progress_percent(self, obj) -> int:
        return round(obj.lessons_done * 100 / obj.lessons_total) if obj.lessons_total else 0


class EnrollResultSerializer(serializers.ModelSerializer):
    course = serializers.SlugRelatedField(slug_field="slug", read_only=True)

    class Meta:
        model = Enrollment
        fields = ["course", "created_at"]
