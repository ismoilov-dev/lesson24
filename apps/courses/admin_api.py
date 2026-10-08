"""Admin panel API: barcha kurslar/darslar (umumiy baza — manage_api), teacher kurslarini
tekshirish (approve/reject), yozilishlar."""

from django.db import transaction
from django.db.models import ProtectedError
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import mixins, serializers, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import JSONParser
from rest_framework.response import Response

from apps.errors import ServiceError
from apps.users.models import User
from apps.users.permissions import IsAdminRole

from . import services
from .manage_api import CourseManageViewSet, LessonManageViewSet
from .manage_serializers import AdminCourseSerializer
from .models import Course, Enrollment, Lesson
from .queries import enrollments_with_progress

TAGS = ["admin"]
# Swagger'da faqat forma: "cover" — Choose File. (API JSON'ni ham qabul qiladi — fayl'siz.)
COURSE_REQUEST = {"multipart/form-data": AdminCourseSerializer}


class RejectCourseSerializer(serializers.Serializer):
    note = serializers.CharField(max_length=500, help_text="Teacher'ga ko'rinadigan izoh")


@extend_schema_view(
    list=extend_schema(
        summary="Barcha kurslar (teacher'larniki ham)",
        parameters=[
            OpenApiParameter("review_status", str, enum=Course.ReviewStatus.values),
            OpenApiParameter("owner", int, description="Teacher ID"),
        ],
    ),
    create=extend_schema(
        summary="Kurs yaratish: title, description, cover (rasm), price — bitta formada",
        request=COURSE_REQUEST,
    ),
    update=extend_schema(request=COURSE_REQUEST),
    partial_update=extend_schema(
        summary="Kursni tahrirlash (muqova, nashr, teacher biriktirish)", request=COURSE_REQUEST
    ),
    destroy=extend_schema(summary="O'chirish (o'quvchisi yo'q kursni)"),
)
@extend_schema(tags=TAGS)
class AdminCourseViewSet(CourseManageViewSet):
    serializer_class = AdminCourseSerializer
    permission_classes = [IsAdminRole]

    def scope(self):
        qs = Course.objects.all()
        params = self.request.query_params
        if params.get("review_status") in Course.ReviewStatus.values:
            qs = qs.filter(review_status=params["review_status"])
        if (owner := params.get("owner")) and owner.isdigit():
            qs = qs.filter(owner_id=int(owner))
        return qs

    def _fresh(self, course: Course) -> Response:
        return Response(self.get_serializer(self.get_queryset().get(pk=course.pk)).data)

    @extend_schema(summary="Teacher kursini tasdiqlash (nashr qilinadi)", request=None)
    @action(detail=True, methods=["post"], parser_classes=[JSONParser])
    def approve(self, request, pk=None):
        return self._fresh(services.approve_course(self.get_object()))

    @extend_schema(summary="Teacher kursini izoh bilan qaytarish", request=RejectCourseSerializer)
    @action(detail=True, methods=["post"], parser_classes=[JSONParser])
    def reject(self, request, pk=None):
        data = RejectCourseSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        return self._fresh(services.reject_course(self.get_object(), data.validated_data["note"]))


@extend_schema_view(
    list=extend_schema(parameters=[OpenApiParameter("course", int)]),
)
@extend_schema(tags=TAGS)
class AdminLessonViewSet(mixins.ListModelMixin, LessonManageViewSet):
    """Barcha darslar (`?course=<id>`) va test muharriri."""

    permission_classes = [IsAdminRole]

    def scope(self):
        course = self.request.query_params.get("course")
        qs = Lesson.objects.all()
        return qs.filter(course_id=int(course)) if course and course.isdigit() else qs


# --- enrollments --------------------------------------------------------------------------


class AdminEnrollmentSerializer(serializers.ModelSerializer):
    student = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.filter(role=User.Role.STUDENT)
    )
    course = serializers.PrimaryKeyRelatedField(queryset=Course.objects.all())
    student_name = serializers.CharField(source="student.get_full_name", read_only=True)
    course_title = serializers.CharField(source="course.title", read_only=True)
    lessons_total = serializers.IntegerField(read_only=True)
    lessons_done = serializers.IntegerField(read_only=True)

    class Meta:
        model = Enrollment
        fields = [
            "id",
            "student",
            "student_name",
            "course",
            "course_title",
            "lessons_total",
            "lessons_done",
            "created_at",
            "completed_at",
        ]
        read_only_fields = ["created_at", "completed_at"]
        validators: list = []

    def create(self, validated_data: dict) -> Enrollment:
        enrollment, created = Enrollment.objects.get_or_create(**validated_data)
        if not created:
            raise ServiceError("Student bu kursga allaqachon yozilgan.", code="exists")
        return enrollment


@extend_schema_view(
    list=extend_schema(
        tags=TAGS,
        summary="Yozilishlar (progress bilan)",
        parameters=[OpenApiParameter("course", int), OpenApiParameter("student", int)],
    ),
    create=extend_schema(tags=TAGS, summary="Qo'lda kursga yozish (to'lovsiz)"),
    destroy=extend_schema(tags=TAGS, summary="Yozilishni bekor qilish (sertifikatsiz)"),
)
class AdminEnrollmentViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = AdminEnrollmentSerializer
    permission_classes = [IsAdminRole]

    def get_queryset(self):
        filters = {}
        for field in ("course", "student"):
            value = self.request.query_params.get(field)
            if value and value.isdigit():
                filters[f"{field}_id"] = int(value)
        return enrollments_with_progress(**filters)

    def create(self, request, *args, **kwargs):
        serializer = self.get_serializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        enrollment = serializer.save()
        fresh = self.get_queryset().get(pk=enrollment.pk)
        return Response(self.get_serializer(fresh).data, status=status.HTTP_201_CREATED)

    @transaction.atomic
    def perform_destroy(self, instance) -> None:
        try:
            instance.delete()
        except ProtectedError as exc:
            raise ServiceError(
                "Sertifikat berilgan yozilishni o'chirib bo'lmaydi.", code="has_certificate"
            ) from exc
