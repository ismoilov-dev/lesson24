from django.contrib import admin

from apps.admin_access import AdminRoleOnlyMixin

from .models import Certificate


@admin.register(Certificate)
class CertificateAdmin(AdminRoleOnlyMixin, admin.ModelAdmin):
    """Sertifikat kurs tugaganda avtomatik yaratiladi — qo'lda qo'shilmaydi."""

    list_display = ["uid", "enrollment", "issued_at"]
    search_fields = ["uid", "enrollment__student__phone", "enrollment__course__title"]
    list_select_related = ["enrollment__student", "enrollment__course"]
    readonly_fields = ["uid", "enrollment", "issued_at"]

    def has_add_permission(self, request) -> bool:
        return False
