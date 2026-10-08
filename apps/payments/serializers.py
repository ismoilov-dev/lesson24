from rest_framework import serializers

from apps.courses.models import Course
from apps.users.models import User

from .models import Order


class OrderCourseSerializer(serializers.ModelSerializer):
    class Meta:
        model = Course
        fields = ["id", "slug", "title"]


class OrderStudentSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(source="get_full_name")

    class Meta:
        model = User
        fields = ["id", "full_name"]


class OrderSerializer(serializers.ModelSerializer):
    """Skrinshot qaytarilmaydi — u faqat admin uchun."""

    course = OrderCourseSerializer()
    student = OrderStudentSerializer()

    class Meta:
        model = Order
        fields = [
            "id",
            "course",
            "student",
            "amount",
            "status",
            "reject_reason",
            "created_at",
            "reviewed_at",
        ]


class OrderCreateSerializer(serializers.Serializer):
    course = serializers.SlugRelatedField(
        slug_field="slug", queryset=Course.objects.filter(is_published=True)
    )
    student = serializers.PrimaryKeyRelatedField(
        queryset=User.objects.filter(role=User.Role.STUDENT),
        required=False,
        allow_null=True,
        help_text="Ota-ona sotib olsa — farzand ID si",
    )
    # Format/hajm tekshiruvi services.validate_screenshot da (Pillow bilan)
    screenshot = serializers.FileField()


class PaymentInfoQuerySerializer(serializers.Serializer):
    course = serializers.SlugRelatedField(
        slug_field="slug", queryset=Course.objects.filter(is_published=True)
    )


class PaymentInfoSerializer(serializers.Serializer):
    card_number = serializers.CharField()
    card_holder = serializers.CharField()
    amount = serializers.IntegerField()
