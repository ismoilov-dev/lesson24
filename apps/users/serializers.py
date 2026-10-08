from rest_framework import serializers

from apps.imgbb import upload_image

from .models import TeacherProfile, User


class MeSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ["id", "phone", "first_name", "last_name", "role", "language", "telegram_id"]
        read_only_fields = ["id", "phone", "role", "telegram_id"]


class VerifySerializer(serializers.Serializer):
    phone = serializers.CharField(max_length=32)
    code = serializers.RegexField(r"^\d{6}$", error_messages={"invalid": "Kod 6 ta raqam."})


class TokenPairSerializer(serializers.Serializer):
    access = serializers.CharField()
    refresh = serializers.CharField()
    user = MeSerializer()


class ParentInviteSerializer(serializers.Serializer):
    code = serializers.CharField()
    link = serializers.URLField()
    expires_at = serializers.DateTimeField()


class LinkChildSerializer(serializers.Serializer):
    code = serializers.CharField(max_length=16)


class ChildSerializer(serializers.ModelSerializer):
    full_name = serializers.CharField(source="get_full_name")
    linked_at = serializers.DateTimeField()
    courses_count = serializers.IntegerField()
    completed_courses_count = serializers.IntegerField()

    class Meta:
        model = User
        fields = ["id", "full_name", "linked_at", "courses_count", "completed_courses_count"]


class TeacherProfileSerializer(serializers.ModelSerializer):
    """Teacher profili: ism (User'da) + mutaxassislik, bio, rasm (fayl → ImgBB)."""

    first_name = serializers.CharField(source="user.first_name", max_length=150, required=False)
    last_name = serializers.CharField(
        source="user.last_name", max_length=150, required=False, allow_blank=True
    )
    photo = serializers.FileField(
        write_only=True, required=False, help_text="Profil rasmi (JPG/PNG/WEBP) — ImgBB'ga"
    )
    photo_url = serializers.URLField(read_only=True)

    class Meta:
        model = TeacherProfile
        fields = ["first_name", "last_name", "specialization", "bio", "photo", "photo_url"]

    def update(self, instance: TeacherProfile, validated_data: dict) -> TeacherProfile:
        user_data = validated_data.pop("user", {})
        if user_data:
            for field, value in user_data.items():
                setattr(instance.user, field, value)
            instance.user.save(update_fields=list(user_data))
        if (photo := validated_data.pop("photo", None)) is not None:
            instance.photo_url = upload_image(photo, name=instance.user.get_full_name())
        return super().update(instance, validated_data)
