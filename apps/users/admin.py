from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as DjangoUserAdmin

from .models import OneTimeCode, ParentLink, User


class ParentLinkInline(admin.TabularInline):
    model = ParentLink
    fk_name = "student"
    extra = 0
    autocomplete_fields = ["parent"]
    verbose_name_plural = "Ota-onalar"


@admin.register(User)
class UserAdmin(DjangoUserAdmin):
    list_display = ["id", "__str__", "phone", "role", "telegram_id", "is_active", "date_joined"]
    list_filter = ["role", "is_active", "language"]
    search_fields = ["phone", "first_name", "last_name", "username", "telegram_id"]
    ordering = ["-date_joined"]
    readonly_fields = ["telegram_id", "is_staff", "last_login", "date_joined"]
    fieldsets = [
        (None, {"fields": ["username", "password"]}),
        ("Profil", {"fields": ["first_name", "last_name", "phone", "language", "telegram_id"]}),
        ("Rol va kirish", {"fields": ["role", "is_active", "is_staff", "is_superuser"]}),
        ("Sanalar", {"fields": ["last_login", "date_joined"]}),
    ]
    add_fieldsets = [
        (None, {"fields": ["username", "phone", "role", "password1", "password2"]}),
    ]
    inlines = [ParentLinkInline]


@admin.register(ParentLink)
class ParentLinkAdmin(admin.ModelAdmin):
    list_display = ["id", "parent", "student", "created_at"]
    search_fields = ["parent__phone", "student__phone", "parent__first_name", "student__first_name"]
    autocomplete_fields = ["parent", "student"]
    list_select_related = ["parent", "student"]


@admin.register(OneTimeCode)
class OneTimeCodeAdmin(admin.ModelAdmin):
    """Faqat ko'rish uchun (nosozliklarni tekshirish). Kod qiymati ko'rsatilmaydi."""

    list_display = ["id", "user", "purpose", "created_at", "expires_at", "used_at", "attempts"]
    list_filter = ["purpose"]
    search_fields = ["user__phone"]
    list_select_related = ["user"]
    exclude = ["code"]

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False
