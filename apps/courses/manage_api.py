"""Kontent boshqaruvi uchun umumiy viewset'lar (admin va teacher API'lari meros oladi).

Farq faqat quyidagilarda: `permission_classes`, `serializer_class`, `scope()` (kim nimani
ko'radi) va panelga xos qo'shimcha action'lar. Yangi rol qo'shilsa — shu klasslardan meros olinadi.
"""

from django.db.models import Max, OuterRef, ProtectedError, QuerySet
from drf_spectacular.utils import OpenApiExample, extend_schema
from rest_framework import mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.response import Response

from apps.errors import ServiceError

from . import services
from .manage_serializers import (
    CourseStudentSerializer,
    LessonManageSerializer,
    QuizEditorSerializer,
    ReorderSerializer,
)
from .models import Course, Enrollment, Lesson, Quiz
from .queries import enrollments_with_progress, subquery_count

LESSON_EXAMPLE = OpenApiExample(
    "Video dars",
    value={
        "title": "1-dars: Kirish",
        "video_url": "https://drive.google.com/file/d/1AbCdEfGhIjKlMnOpQrStUvWxYz012345/view",
        "description": "Python nima va nega kerak",
        "duration_min": 12,
        "is_free_preview": False,
    },
    request_only=True,
)


class CourseManageViewSet(viewsets.ModelViewSet):
    """Kurs CRUD + darslar ro'yxati/qo'shish + tartib + o'quvchilar progressi."""

    # Kurs: multipart (muqova fayli bilan) yoki JSON; darslar/reorder action'lari — faqat JSON
    parser_classes = [MultiPartParser, JSONParser]

    def scope(self) -> QuerySet[Course]:
        """Panel ko'radigan kurslar. Meros oluvchi klass qayta yozadi."""
        raise NotImplementedError

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):  # sxema generatsiyasi: user yo'q
            return Course.objects.none()
        return (
            self.scope()
            .select_related("owner")
            .annotate(
                # Ikki xil JOIN'dagi COUNT bir-birini ko'paytirmasin — subquery'lar
                lessons_count=subquery_count(
                    Lesson.objects.filter(course=OuterRef("pk")), "course"
                ),
                students_count=subquery_count(
                    Enrollment.objects.filter(course=OuterRef("pk")), "course"
                ),
            )
            .order_by("-created_at")
        )

    def perform_destroy(self, instance) -> None:
        try:
            instance.delete()
        except ProtectedError as exc:
            raise ServiceError(
                "O'quvchisi yoki buyurtmasi bor kursni o'chirib bo'lmaydi — nashrdan oling.",
                code="course_in_use",
            ) from exc

    @extend_schema(summary="Kurs darslari", responses=LessonManageSerializer(many=True))
    @action(detail=True, methods=["get"], pagination_class=None, parser_classes=[JSONParser])
    def lessons(self, request, pk=None):
        course = self.get_object()
        lessons = course.lessons.select_related("quiz").order_by("order")
        return Response(LessonManageSerializer(lessons, many=True).data)

    @lessons.mapping.post
    @extend_schema(
        summary="Dars qo'shish: title, video_url (Google Drive havolasi) va boshqalar",
        request=LessonManageSerializer,
        responses={201: LessonManageSerializer},
        examples=[LESSON_EXAMPLE],
    )
    def create_lesson(self, request, pk=None):
        course = self.get_object()
        serializer = LessonManageSerializer(data=request.data, context={"course": course})
        serializer.is_valid(raise_exception=True)
        order = serializer.validated_data.pop("order", None)
        if order is None:
            order = (course.lessons.aggregate(m=Max("order"))["m"] or 0) + 1
        lesson = serializer.save(course=course, order=order)
        return Response(LessonManageSerializer(lesson).data, status=status.HTTP_201_CREATED)

    @extend_schema(
        summary="Darslar tartibini o'zgartirish",
        request=ReorderSerializer,
        responses=LessonManageSerializer(many=True),
    )
    @action(detail=True, methods=["post"], url_path="lessons/reorder", parser_classes=[JSONParser])
    def reorder(self, request, pk=None):
        course = self.get_object()
        serializer = ReorderSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.reorder_lessons(course, serializer.validated_data["lesson_ids"])
        return self.lessons(request, pk)

    @extend_schema(summary="Kurs o'quvchilari va progressi")
    @action(detail=True, methods=["get"], serializer_class=CourseStudentSerializer)
    def students(self, request, pk=None):
        course = self.get_object()
        page = self.paginate_queryset(enrollments_with_progress(course=course))
        return self.get_paginated_response(CourseStudentSerializer(page, many=True).data)


class LessonManageViewSet(
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    """Bitta dars: ko'rish/tahrirlash/o'chirish + test muharriri.
    Dars qo'shish — `courses/{id}/lessons/`."""

    serializer_class = LessonManageSerializer

    def scope(self) -> QuerySet[Lesson]:
        raise NotImplementedError

    def get_queryset(self):
        if getattr(self, "swagger_fake_view", False):
            return Lesson.objects.none()
        return self.scope().select_related("course", "quiz").order_by("course_id", "order")

    @extend_schema(summary="Test (to'g'ri javoblar bilan)", responses=QuizEditorSerializer)
    @action(detail=True, methods=["get"])
    def quiz(self, request, pk=None):
        lesson = self.get_object()
        quiz = getattr(lesson, "quiz", None)
        if quiz is None:
            return Response({"pass_percent": Quiz.PASS_PERCENT, "questions": []})
        quiz = Quiz.objects.prefetch_related("questions__answers").get(pk=quiz.pk)
        return Response(QuizEditorSerializer(quiz).data)

    @quiz.mapping.put
    @extend_schema(
        summary="Testni to'liq almashtirish",
        request=QuizEditorSerializer,
        responses=QuizEditorSerializer,
    )
    def replace_quiz(self, request, pk=None):
        lesson = self.get_object()
        serializer = QuizEditorSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        services.replace_quiz(lesson, serializer.validated_data["questions"])
        return self.quiz(request, pk)

    @quiz.mapping.delete
    @extend_schema(summary="Testni o'chirish", responses={204: None})
    def delete_quiz(self, request, pk=None):
        lesson = self.get_object()
        if hasattr(lesson, "quiz"):
            lesson.quiz.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)
