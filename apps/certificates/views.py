from drf_spectacular.utils import extend_schema
from rest_framework import generics
from rest_framework.permissions import AllowAny

from apps.users.permissions import IsStudent

from .models import Certificate
from .serializers import CertificateSerializer

RELATED = ["enrollment__student", "enrollment__course__owner"]


@extend_schema(tags=["me"], summary="Sertifikatlarim")
class MyCertificatesView(generics.ListAPIView):
    serializer_class = CertificateSerializer
    permission_classes = [IsStudent]

    def get_queryset(self):
        return Certificate.objects.filter(enrollment__student=self.request.user).select_related(
            *RELATED
        )


@extend_schema(tags=["certificates"], summary="Sertifikatni tekshirish (QR havola)")
class CertificateVerifyView(generics.RetrieveAPIView):
    serializer_class = CertificateSerializer
    permission_classes = [AllowAny]
    authentication_classes: list = []
    lookup_field = "uid"
    queryset = Certificate.objects.select_related(*RELATED)
