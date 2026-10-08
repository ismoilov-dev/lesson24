"""Read-side querysets shared by several views (student, parent, admin)."""

from django.db.models import Count, Exists, IntegerField, OuterRef, QuerySet, Subquery
from django.db.models.functions import Coalesce

from .models import Enrollment, Lesson, LessonProgress


def subquery_count(queryset: QuerySet, group_by: str) -> Coalesce:
    """Correlated COUNT(*) — JOIN + GROUP BY qatorlarni ko'paytirmaydi."""
    counted = queryset.order_by().values(group_by).annotate(n=Count("pk")).values("n")
    return Coalesce(Subquery(counted), 0, output_field=IntegerField())


def enrollments_with_progress(**filters) -> QuerySet[Enrollment]:
    """Enrollments (filtered by `filters`) with `lessons_total`, `lessons_done`, `next_lesson_id`.

    One query regardless of the number of rows.
    """
    completed = LessonProgress.objects.filter(completed_at__isnull=False)
    # "Davom ettirish": shu yozilish bo'yicha tugatilmagan birinchi dars
    not_done = ~Exists(completed.filter(lesson=OuterRef("pk"), enrollment=OuterRef(OuterRef("pk"))))
    next_lesson = Lesson.objects.filter(not_done, course=OuterRef("course")).order_by("order")
    return (
        Enrollment.objects.filter(**filters)
        .select_related("student", "course__owner")
        .annotate(
            lessons_total=subquery_count(
                Lesson.objects.filter(course=OuterRef("course")), "course"
            ),
            lessons_done=subquery_count(completed.filter(enrollment=OuterRef("pk")), "enrollment"),
            next_lesson_id=Subquery(next_lesson.values("pk")[:1]),
        )
        .order_by("-created_at")
    )
