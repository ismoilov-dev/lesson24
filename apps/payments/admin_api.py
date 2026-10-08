"""Admin panel API: orders — list, screenshot (JWT-protected), approve / reject."""

from django.urls import reverse
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import filters, mixins, serializers, viewsets
from rest_framework.decorators import action
from rest_framework.response import Response

from apps.files import protected_file_response
from apps.users.permissions import IsAdminRole

from . import services
from .models import PENDING_FIRST, Order
from .serializers import OrderSerializer, OrderStudentSerializer

TAGS = ["admin"]


class AdminOrderSerializer(OrderSerializer):
    user = OrderStudentSerializer()
    payer_phone = serializers.CharField(source="user.phone")
    student_phone = serializers.CharField(source="student.phone")
    reviewed_by = OrderStudentSerializer(allow_null=True)
    screenshot_url = serializers.SerializerMethodField()

    class Meta(OrderSerializer.Meta):
        fields = [
            *OrderSerializer.Meta.fields,
            "user",
            "payer_phone",
            "student_phone",
            "reviewed_by",
            "screenshot_url",
        ]

    def get_screenshot_url(self, obj) -> str:
        # JWT talab qiladi: frontend fetch() qilib blob URL sifatida ko'rsatadi
        return reverse("admin-order-screenshot", args=[obj.pk])


class RejectSerializer(serializers.Serializer):
    reason = serializers.CharField(max_length=500)


@extend_schema_view(
    list=extend_schema(
        tags=TAGS,
        summary="Buyurtmalar (pending tepada)",
        parameters=[OpenApiParameter("status", str, enum=Order.Status.values)],
    ),
    retrieve=extend_schema(tags=TAGS),
)
class AdminOrderViewSet(mixins.ListModelMixin, mixins.RetrieveModelMixin, viewsets.GenericViewSet):
    serializer_class = AdminOrderSerializer
    permission_classes = [IsAdminRole]
    filter_backends = [filters.SearchFilter]
    search_fields = ["student__phone", "user__phone", "student__first_name", "course__title"]

    def get_queryset(self):
        qs = Order.objects.select_related("course", "student", "user", "reviewed_by")
        status = self.request.query_params.get("status")
        if status in Order.Status.values:
            qs = qs.filter(status=status)
        return qs.order_by(PENDING_FIRST, "-created_at")

    @extend_schema(
        tags=TAGS, summary="Chek skrinshoti", responses={(200, "image/*"): OpenApiTypes.BINARY}
    )
    @action(detail=True, methods=["get"])
    def screenshot(self, request, pk=None):
        return protected_file_response(self.get_object().screenshot)

    @extend_schema(tags=TAGS, summary="Tasdiqlash (kurs ochiladi)", request=None)
    @action(detail=True, methods=["post"])
    def approve(self, request, pk=None):
        order = services.approve_order(self.get_object(), request.user)
        return Response(self.get_serializer(self.get_queryset().get(pk=order.pk)).data)

    @extend_schema(tags=TAGS, summary="Rad etish", request=RejectSerializer)
    @action(detail=True, methods=["post"])
    def reject(self, request, pk=None):
        data = RejectSerializer(data=request.data)
        data.is_valid(raise_exception=True)
        order = services.reject_order(
            self.get_object(), request.user, data.validated_data["reason"]
        )
        return Response(self.get_serializer(self.get_queryset().get(pk=order.pk)).data)
