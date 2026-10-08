from django import forms
from django.contrib import admin, messages
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404
from django.urls import path, reverse
from django.utils.html import format_html

from apps.admin_access import AdminRoleOnlyMixin
from apps.errors import ServiceError
from apps.files import protected_file_response

from . import services
from .models import PENDING_FIRST, Order


class OrderReviewForm(forms.ModelForm):
    """Holat faqat pending'dan o'zgaradi; rad etishda sabab majburiy."""

    class Meta:
        model = Order
        fields = ["status", "reject_reason"]

    def clean(self):
        data = super().clean()
        if data.get("status") == Order.Status.REJECTED and not data.get("reject_reason"):
            self.add_error("reject_reason", "Rad etish sababini yozing.")
        return data


@admin.register(Order)
class OrderAdmin(AdminRoleOnlyMixin, admin.ModelAdmin):
    form = OrderReviewForm
    list_display = ["id", "student", "user", "course", "amount", "status", "created_at"]
    list_filter = ["status", "course"]
    search_fields = ["student__phone", "user__phone", "student__first_name", "course__title"]
    list_select_related = ["student", "user", "course"]
    actions = ["approve_selected"]
    readonly_fields = [
        "user",
        "student",
        "course",
        "amount",
        "screenshot_preview",
        "reviewed_by",
        "reviewed_at",
        "created_at",
    ]
    fields = [
        "screenshot_preview",
        "user",
        "student",
        "course",
        "amount",
        "created_at",
        "status",
        "reject_reason",
        "reviewed_by",
        "reviewed_at",
    ]

    def get_ordering(self, request):
        # pending'lar tepada (changelist get_queryset tartibini qayta yozadi)
        return [PENDING_FIRST, "-created_at"]

    def get_readonly_fields(self, request, obj=None):
        if obj is not None and obj.status != Order.Status.PENDING:
            return [*self.readonly_fields, "status", "reject_reason"]
        return self.readonly_fields

    def has_add_permission(self, request) -> bool:
        return False  # buyurtmalar faqat API orqali keladi

    def has_delete_permission(self, request, obj=None) -> bool:
        return False  # moliyaviy yozuv

    # --- skrinshot ------------------------------------------------------------------------

    def get_urls(self):
        view = self.admin_site.admin_view(self.screenshot_view)
        return [
            path("<int:pk>/screenshot/", view, name="payments_order_screenshot"),
            *super().get_urls(),
        ]

    def screenshot_view(self, request, pk: int):
        if not self.has_view_permission(request):
            raise PermissionDenied
        return protected_file_response(get_object_or_404(Order, pk=pk).screenshot)

    @admin.display(description="Chek")
    def screenshot_preview(self, obj) -> str:
        url = reverse("admin:payments_order_screenshot", args=[obj.pk])
        return format_html(
            '<a href="{0}" target="_blank"><img src="{0}" style="max-height:480px"></a>', url
        )

    # --- tasdiqlash / rad etish (services orqali) -----------------------------------------

    def save_model(self, request, obj, form, change) -> None:
        if "status" not in form.changed_data:
            return
        try:
            if obj.status == Order.Status.APPROVED:
                services.approve_order(obj, request.user)
            elif obj.status == Order.Status.REJECTED:
                services.reject_order(obj, request.user, obj.reject_reason)
        except ServiceError as exc:
            self.message_user(request, exc.message, messages.ERROR)

    @admin.action(description="Tanlanganlarni tasdiqlash")
    def approve_selected(self, request, queryset) -> None:
        approved = 0
        for order in queryset.filter(status=Order.Status.PENDING):
            try:
                services.approve_order(order, request.user)
                approved += 1
            except ServiceError:
                continue
        self.message_user(request, f"Tasdiqlandi: {approved}", messages.SUCCESS)
