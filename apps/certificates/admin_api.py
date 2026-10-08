from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import filters, viewsets

from apps.users.permissions import IsAdminRole

from .models import Certificate
from .serializers import CertificateSerializer
from .views import RELATED


@extend_schema_view(
    list=extend_schema(tags=["admin"], summary="Sertifikatlar"),
    retrieve=extend_schema(tags=["admin"]),
)
class AdminCertificateViewSet(viewsets.ReadOnlyModelViewSet):
    """Faqat o'qish: sertifikat kurs tugaganda avtomatik beriladi."""

    serializer_class = CertificateSerializer
    permission_classes = [IsAdminRole]
    lookup_field = "uid"
    filter_backends = [filters.SearchFilter]
    search_fields = ["enrollment__student__phone", "enrollment__course__title"]
    queryset = Certificate.objects.select_related(*RELATED).order_by("-issued_at")
