"""Admin panel API (`/api/v1/admin/`): dashboard, users, parent links."""

from django.db import IntegrityError, transaction
from django.db.models import Count, Q, Sum
from django.db.models.functions import Coalesce
from django.utils import timezone
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import filters, mixins, serializers, viewsets
from rest_framework.exceptions import PermissionDenied
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.courses.models import Course, Enrollment
from apps.errors import ServiceError
from apps.payments.models import Order

from . import services
from .models import ParentLink, User
from .permissions import IsAdminRole

TAGS = ["admin"]


# --- dashboard ----------------------------------------------------------------------------


class AdminStatsSerializer(serializers.Serializer):
    users = serializers.DictField(child=serializers.IntegerField())
    courses = serializers.DictField(child=serializers.IntegerField())
    enrollments = serializers.DictField(child=serializers.IntegerField())
    orders = serializers.DictField(child=serializers.IntegerField())


@extend_schema(tags=TAGS, summary="Dashboard", responses=AdminStatsSerializer)
class AdminStatsView(APIView):
    permission_classes = [IsAdminRole]

    def get(self, request):
        role = {f"{r}s": Count("pk", filter=Q(role=r)) for r in User.Role.values}
        month_start = timezone.localtime().replace(day=1, hour=0, minute=0, second=0, microsecond=0)
        approved = Q(status=Order.Status.APPROVED)
        # 4 ta aggregate so'rov — jadval hajmidan qat'i nazar
        data = {
            "users": User.objects.aggregate(
                total=Count("pk"), blocked=Count("pk", filter=Q(is_active=False)), **role
            ),
            "courses": Course.objects.aggregate(
                total=Count("pk"), published=Count("pk", filter=Q(is_published=True))
            ),
            "enrollments": Enrollment.objects.aggregate(
                total=Count("pk"), completed=Count("pk", filter=Q(completed_at__isnull=False))
            ),
            "orders": Order.objects.aggregate(
                pending=Count("pk", filter=Q(status=Order.Status.PENDING)),
                revenue_total=Coalesce(Sum("amount", filter=approved), 0),
                revenue_month=Coalesce(
                    Sum("amount", filter=approved & Q(reviewed_at__gte=month_start)), 0
                ),
            ),
        }
        return Response(data)


# --- users --------------------------------------------------------------------------------


class AdminUserSerializer(serializers.ModelSerializer):
    phone = serializers.CharField(max_length=32)

    class Meta:
        model = User
        fields = [
            "id",
            "phone",
            "first_name",
            "last_name",
            "role",
            "language",
            "is_active",
            "is_superuser",
            "telegram_id",
            "date_joined",
            "last_login",
        ]
        read_only_fields = ["is_superuser", "telegram_id", "date_joined", "last_login"]

    def validate_phone(self, value: str) -> str:
        phone = services.normalize_phone(value)
        taken = User.objects.filter(phone=phone)
        if self.instance is not None:
            taken = taken.exclude(pk=self.instance.pk)
        if taken.exists():
            raise serializers.ValidationError("Bu telefon raqam band.")
        return phone

    def validate(self, data: dict) -> dict:
        actor = self.context["request"].user
        target = self.instance
        if target is not None:
            if target.is_superuser and not actor.is_superuser:
                raise PermissionDenied("Superuser'ni faqat superuser o'zgartiradi.")
            if target.pk == actor.pk and (
                data.get("is_active") is False or data.get("role", target.role) != target.role
            ):
                raise serializers.ValidationError("O'z rolingizni o'zgartirib/bloklab bo'lmaydi.")
        return data

    def create(self, validated_data: dict) -> User:
        """Oldindan ro'yxatdan o'tkazish (masalan, yangi admin): botga shu raqam bilan kirganda
        akkaunt avtomatik bog'lanadi va rol saqlanadi."""
        user = User(username=validated_data["phone"], **validated_data)
        user.set_unusable_password()
        user.save()
        return user


@extend_schema_view(
    list=extend_schema(
        tags=TAGS,
        summary="Foydalanuvchilar",
        parameters=[
            OpenApiParameter("role", str, enum=User.Role.values),
            OpenApiParameter("is_active", bool),
        ],
    ),
    create=extend_schema(tags=TAGS, summary="Telefon bo'yicha oldindan ro'yxatdan o'tkazish"),
    retrieve=extend_schema(tags=TAGS),
    partial_update=extend_schema(tags=TAGS, summary="Rol, bloklash, profil"),
)
class AdminUserViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.RetrieveModelMixin,
    mixins.UpdateModelMixin,
    viewsets.GenericViewSet,
):
    """O'chirish yo'q: foydalanuvchi bloklanadi (`is_active=false`) — moliyaviy tarix saqlanadi."""

    serializer_class = AdminUserSerializer
    permission_classes = [IsAdminRole]
    http_method_names = ["get", "post", "patch", "options"]
    filter_backends = [filters.SearchFilter]
    search_fields = ["phone", "first_name", "last_name"]

    def get_queryset(self):
        qs = User.objects.order_by("-date_joined")
        params = self.request.query_params
        if params.get("role") in User.Role.values:
            qs = qs.filter(role=params["role"])
        if params.get("is_active") in ("true", "false"):
            qs = qs.filter(is_active=params["is_active"] == "true")
        return qs


# --- parent links -------------------------------------------------------------------------


class AdminParentLinkSerializer(serializers.ModelSerializer):
    parent = serializers.PrimaryKeyRelatedField(queryset=User.objects.filter(role="parent"))
    student = serializers.PrimaryKeyRelatedField(queryset=User.objects.filter(role="student"))
    parent_name = serializers.CharField(source="parent.get_full_name", read_only=True)
    student_name = serializers.CharField(source="student.get_full_name", read_only=True)

    class Meta:
        model = ParentLink
        fields = ["id", "parent", "parent_name", "student", "student_name", "created_at"]
        validators: list = []  # unikallik create() da DB orqali

    def create(self, validated_data: dict) -> ParentLink:
        try:
            with transaction.atomic():  # savepoint: xatodan keyin tranzaksiya ishlashda davom etadi
                return super().create(validated_data)
        except IntegrityError as exc:
            raise ServiceError("Bu bog'lanish allaqachon mavjud.", code="exists") from exc


@extend_schema_view(
    list=extend_schema(
        tags=TAGS,
        summary="Ota-ona bog'lanishlari",
        parameters=[OpenApiParameter("parent", int), OpenApiParameter("student", int)],
    ),
    create=extend_schema(tags=TAGS),
    destroy=extend_schema(tags=TAGS),
)
class AdminParentLinkViewSet(
    mixins.ListModelMixin,
    mixins.CreateModelMixin,
    mixins.DestroyModelMixin,
    viewsets.GenericViewSet,
):
    serializer_class = AdminParentLinkSerializer
    permission_classes = [IsAdminRole]

    def get_queryset(self):
        qs = ParentLink.objects.select_related("parent", "student").order_by("-created_at")
        for field in ("parent", "student"):
            value = self.request.query_params.get(field)
            if value and value.isdigit():
                qs = qs.filter(**{f"{field}_id": int(value)})
        return qs
