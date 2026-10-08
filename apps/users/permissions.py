"""Role-based DRF permissions.

`IsEnrolled` (lesson access) lives next to the course models in `apps/courses/permissions.py`.
"""

from rest_framework.permissions import BasePermission

from .models import User


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
