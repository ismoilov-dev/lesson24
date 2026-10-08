from rest_framework.permissions import BasePermission

from .models import Enrollment, Lesson


def can_access_lesson(user, lesson: Lesson) -> bool:
    """Free preview — hamma; pullik dars — yozilgan student, admin va kurs egasi (teacher)."""
    if lesson.is_free_preview:
        return True
    if not user.is_authenticated:
        return False
    if user.is_admin_role or lesson.course.owner_id == user.pk:
        return True
    enrolled = getattr(lesson, "user_enrolled", None)  # view annotatsiyasi bo'lsa — so'rovsiz
    if enrolled is not None:
        return enrolled
    return Enrollment.objects.filter(student=user, course_id=lesson.course_id).exists()


class IsEnrolled(BasePermission):
    """Object-level: `obj` — Lesson."""

    message = "Bu dars uchun kursga yozilish kerak."

    def has_object_permission(self, request, view, obj: Lesson) -> bool:
        return can_access_lesson(request.user, obj)
