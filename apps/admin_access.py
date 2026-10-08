"""Role-based access for Django Admin.

Admins get `is_staff` from their role but no Django model permissions, so access is decided here
by role (`role=admin` or superuser) instead of the `auth.Permission` tables.
"""


class AdminRoleOnlyMixin:
    """Only `role=admin` or a superuser."""

    def _allowed(self, request) -> bool:
        return request.user.is_active and request.user.is_admin_role

    def has_module_permission(self, request) -> bool:
        return self._allowed(request)

    def has_view_permission(self, request, obj=None) -> bool:
        return self._allowed(request)

    def has_add_permission(self, request, *args) -> bool:
        return self._allowed(request)

    def has_change_permission(self, request, obj=None) -> bool:
        return self._allowed(request)

    def has_delete_permission(self, request, obj=None) -> bool:
        return self._allowed(request)
