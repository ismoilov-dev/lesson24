from rest_framework import serializers

from .models import Certificate


class CertificateSerializer(serializers.ModelSerializer):
    """Ommaviy sahifa uchun ham ishlatiladi — faqat minimal ma'lumot (telefon va h.k. yo'q)."""

    student_name = serializers.CharField(source="enrollment.student.get_full_name")
    course_title = serializers.CharField(source="enrollment.course.title")
    course_slug = serializers.CharField(source="enrollment.course.slug")
    instructor_name = serializers.CharField(source="enrollment.course.display_instructor")
    url = serializers.URLField()

    class Meta:
        model = Certificate
        fields = [
            "uid",
            "student_name",
            "course_title",
            "course_slug",
            "instructor_name",
            "issued_at",
            "url",
        ]
