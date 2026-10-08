"""Teacher paneli (`/api/v1/teacher/`): faqat o'z kurslari.

Kurs, dars, test, tartib, o'quvchilar — umumiy bazadan (manage_api). Teacher'ga xos:
- yangi kurs `draft` holatida yaratiladi, `owner` = teacher;
- nashr qilish — `submit/` (tekshiruvga) → admin `approve`/`reject`;
- nashr qilingan kursni o'chirib bo'lmaydi; to'lovlar va cheklar ko'rinmaydi.
"""

from django.db.models import Count, Q
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import serializers
from rest_framework.decorators import action
from rest_framework.parsers import JSONParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.errors import ServiceError
from apps.users.permissions import IsTeacher

from . import services
from .manage_api import CourseManageViewSet, LessonManageViewSet
from .manage_serializers import TeacherCourseSerializer
from .models import Course, Enrollment, Lesson

TAGS = ["teacher"]
COURSE_REQUEST = {"multipart/form-data": TeacherCourseSerializer}


@extend_schema_view(
    list=extend_schema(summary="Mening kurslarim"),
    create=extend_schema(
        summary="Kurs yaratish (qoralama): title, description, cover (rasm), price",
        request=COURSE_REQUEST,
    ),
    update=extend_schema(request=COURSE_REQUEST),
    partial_update=extend_schema(summary="Kursni tahrirlash", request=COURSE_REQUEST),
    destroy=extend_schema(summary="O'chirish (faqat nashr qilinmagan va o'quvchisi yo'q kurs)"),
)
@extend_schema(tags=TAGS)
class TeacherCourseViewSet(CourseManageViewSet):
    serializer_class = TeacherCourseSerializer
    permission_classes = [IsTeacher]

    def scope(self):
        return Course.objects.filter(owner=self.request.user)

    def perform_create(self, serializer) -> None:
        serializer.save(
            owner=self.request.user, is_published=False, review_status=Course.ReviewStatus.DRAFT
        )

    def perform_destroy(self, instance) -> None:
        if instance.is_published:
            raise ServiceError(
                "Nashr qilingan kursni o'chirib bo'lmaydi — admin bilan bog'laning.",
                code="course_published",
            )
        super().perform_destroy(instance)

    @extend_schema(
        summary="Kursni admin tekshiruviga yuborish (draft/rejected → pending)", request=None
    )
    @action(detail=True, methods=["post"], parser_classes=[JSONParser])
    def submit(self, request, pk=None):
        course = services.submit_for_review(self.get_object())
        return Response(self.get_serializer(self.get_queryset().get(pk=course.pk)).data)


@extend_schema(tags=TAGS)
class TeacherLessonViewSet(LessonManageViewSet):
    """O'z kurslaridagi darslar va test muharriri."""

    permission_classes = [IsTeacher]

    def scope(self):
        return Lesson.objects.filter(course__owner=self.request.user)


class TeacherStatsSerializer(serializers.Serializer):
    courses = serializers.IntegerField()
    published_courses = serializers.IntegerField()
    pending_review = serializers.IntegerField(help_text="Tekshiruvdagi kurslar")
    students = serializers.IntegerField(help_text="Yozilishlar soni")
    completed = serializers.IntegerField(help_text="Kursni tugatganlar")


@extend_schema(tags=TAGS, summary="Statistika", responses=TeacherStatsSerializer)
class TeacherStatsView(APIView):
    permission_classes = [IsTeacher]

    def get(self, request):
        courses = Course.objects.filter(owner=request.user).aggregate(
            courses=Count("pk"),
            published_courses=Count("pk", filter=Q(is_published=True)),
            pending_review=Count("pk", filter=Q(review_status=Course.ReviewStatus.PENDING)),
        )
        enrollments = Enrollment.objects.filter(course__owner=request.user).aggregate(
            students=Count("pk"), completed=Count("pk", filter=Q(completed_at__isnull=False))
        )
        return Response(TeacherStatsSerializer({**courses, **enrollments}).data)
