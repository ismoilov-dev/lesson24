from django.db.models import (
    Count,
    Exists,
    OuterRef,
    Prefetch,
    Q,
    Subquery,
    Sum,
    Value,
)
from django.db.models.functions import Coalesce
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import filters, generics, mixins, status, viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import PermissionDenied
from rest_framework.permissions import AllowAny
from rest_framework.response import Response

from apps.users.permissions import IsStudent

from . import services
from .models import Course, Enrollment, Lesson, LessonProgress
from .permissions import IsEnrolled
from .queries import enrollments_with_progress
from .serializers import (
    CompleteLessonSerializer,
    CompletionResultSerializer,
    CourseDetailSerializer,
    CourseListSerializer,
    EnrollResultSerializer,
    LessonDetailSerializer,
    MyCourseSerializer,
)


@extend_schema_view(
    list=extend_schema(tags=["courses"], summary="Nashr qilingan kurslar"),
    retrieve=extend_schema(tags=["courses"], summary="Kurs va darslar ro'yxati"),
)
class CourseViewSet(viewsets.ReadOnlyModelViewSet):
    permission_classes = [AllowAny]
    lookup_field = "slug"
    filter_backends = [filters.SearchFilter]
    search_fields = ["title"]

    def get_queryset(self):
        # Hisoblagichlar bitta so'rovda: N+1 yo'q
        qs = (
            Course.objects.filter(self._visible())
            .select_related("owner")
            .annotate(
                lessons_count=Count("lessons"),
                duration_min=Coalesce(Sum("lessons__duration_min"), Value(0)),
            )
            # GROUP BY'li so'rovda Meta.ordering qo'llanmaydi — sahifalash uchun aniq tartib
            .order_by("-created_at", "-id")
        )
        if self.action == "retrieve":
            user = self.request.user
            qs = qs.select_related("owner__teacher_profile")
            lessons = Lesson.objects.annotate(has_quiz=Q(quiz__isnull=False)).order_by("order")
            qs = qs.prefetch_related(Prefetch("lessons", queryset=lessons)).annotate(
                is_enrolled=Exists(
                    Enrollment.objects.filter(course=OuterRef("pk"), student_id=user.pk)
                )
                if user.is_authenticated
                else Value(False)
            )
        return qs

    def _visible(self) -> Q:
        """Katalog ro'yxati — faqat nashr qilinganlar. Kurs sahifasi (retrieve):
        admin — hammasi, teacher — o'z kursi ham (nashrdan oldin oldindan ko'rish)."""
        published = Q(is_published=True)
        user = self.request.user
        if self.action != "retrieve" or not user.is_authenticated:
            return published
        if user.is_admin_role:
            return Q()
        return published | Q(owner=user) if user.is_teacher else published

    def get_serializer_class(self):
        return CourseDetailSerializer if self.action == "retrieve" else CourseListSerializer

    def get_permissions(self):
        return [IsStudent()] if self.action == "enroll" else super().get_permissions()

    @extend_schema(
        tags=["courses"],
        summary="Bepul kursga yozilish (narxi 0)",
        request=None,
        responses={201: EnrollResultSerializer},
    )
    @action(detail=True, methods=["post"])
    def enroll(self, request, slug=None):
        enrollment = services.enroll_free(request.user, self.get_object())
        return Response(EnrollResultSerializer(enrollment).data, status=status.HTTP_201_CREATED)


@extend_schema(tags=["lessons"])
class LessonViewSet(mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    serializer_class = LessonDetailSerializer

    def get_permissions(self):
        if self.action == "complete":
            return [IsStudent()]
        return [AllowAny(), IsEnrolled()]

    def get_queryset(self):
        user = self.request.user
        qs = Lesson.objects.select_related("course")
        # Nashr qilinmagan kurs darslari: admin va kurs egasi (teacher) oldindan ko'radi
        if not (user.is_authenticated and user.is_admin_role):
            visible = Q(course__is_published=True)
            if user.is_authenticated and user.is_teacher:
                visible |= Q(course__owner=user)
            qs = qs.filter(visible)
        if self.action == "retrieve":
            qs = qs.select_related("quiz").prefetch_related("quiz__questions__answers")
            if user.is_authenticated:
                # Yozilish va progress dars so'rovining o'zida — alohida so'rovlar kerak emas
                progress = LessonProgress.objects.filter(
                    lesson=OuterRef("pk"), enrollment__student=user
                )
                qs = qs.annotate(
                    user_enrolled=Exists(
                        Enrollment.objects.filter(course=OuterRef("course"), student=user)
                    ),
                    progress_quiz_score=Subquery(progress.values("quiz_score")[:1]),
                    progress_completed_at=Subquery(progress.values("completed_at")[:1]),
                )
        return qs

    @extend_schema(summary="Dars, imzolangan video URL va test savollari")
    def retrieve(self, request, *args, **kwargs):
        return Response(self.get_serializer(self.get_object()).data)

    @extend_schema(
        summary="Darsni tugatish (test bo'lsa — javoblar bilan)",
        request=CompleteLessonSerializer,
        responses=CompletionResultSerializer,
    )
    @action(detail=True, methods=["post"])
    def complete(self, request, pk=None):
        lesson = self.get_object()
        enrollment = Enrollment.objects.filter(
            student=request.user, course_id=lesson.course_id
        ).first()
        if enrollment is None:
            raise PermissionDenied(IsEnrolled.message)
        serializer = CompleteLessonSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        result = services.complete_lesson(enrollment, lesson, serializer.validated_data["answers"])
        return Response(CompletionResultSerializer(result).data)


@extend_schema(tags=["me"], summary="Mening kurslarim va progress")
class MyCoursesView(generics.ListAPIView):
    serializer_class = MyCourseSerializer
    permission_classes = [IsStudent]

    def get_queryset(self):
        return enrollments_with_progress(student=self.request.user)
