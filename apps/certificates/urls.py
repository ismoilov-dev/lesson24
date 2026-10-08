from django.urls import path
from rest_framework.routers import SimpleRouter

from .admin_api import AdminCertificateViewSet
from .views import CertificateVerifyView, MyCertificatesView

router = SimpleRouter()
router.register("admin/certificates", AdminCertificateViewSet, basename="admin-certificate")

urlpatterns = [
    path("me/certificates/", MyCertificatesView.as_view(), name="my-certificates"),
    path("certificates/<uuid:uid>/", CertificateVerifyView.as_view(), name="certificate-verify"),
    *router.urls,
]
