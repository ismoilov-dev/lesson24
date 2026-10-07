"""Role-based DRF permissions.

`IsEnrolled` (lesson access) lives next to the course models in `apps/courses/permissions.py`.
"""

from rest_framework.permissions import BasePermission

from .models import ParentLink, User


class _RolePermission(BasePermission):
    roles: frozenset[str] = frozenset()

    def has_permission(self, request, view) -> bool:
        user = request.user
        return bool(user and user.is_authenticated and user.role in self.roles)


class IsStudent(_RolePermission):
    roles = frozenset({User.Role.STUDENT})


class IsParent(_RolePermission):
    roles = frozenset({User.Role.PARENT})


class IsStudentOrParent(_RolePermission):
    roles = frozenset({User.Role.STUDENT, User.Role.PARENT})


class IsTeacher(_RolePermission):
    roles = frozenset({User.Role.TEACHER})


class IsAdminRole(BasePermission):
    """`role=admin` or a Django superuser."""

    def has_permission(self, request, view) -> bool:
        user = request.user
        return bool(user and user.is_authenticated and user.is_admin_role)


class IsParentOfStudent(IsParent):
    """Object-level: `obj` is a student linked to the requesting parent."""

    def has_object_permission(self, request, view, obj: User) -> bool:
        return ParentLink.objects.filter(parent=request.user, student=obj).exists()
