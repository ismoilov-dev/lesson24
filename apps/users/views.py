from django.db.models import F, OuterRef
from django.shortcuts import get_object_or_404
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework import generics, status
from rest_framework.parsers import JSONParser, MultiPartParser
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.throttling import ScopedRateThrottle
from rest_framework.views import APIView
from rest_framework_simplejwt.tokens import RefreshToken

from apps.courses.models import Enrollment
from apps.courses.queries import enrollments_with_progress, subquery_count
from apps.courses.serializers import MyCourseSerializer

from . import services
from .models import TeacherProfile, User
from .permissions import IsParent, IsStudent, IsTeacher
from .serializers import (
    ChildSerializer,
    LinkChildSerializer,
    MeSerializer,
    ParentInviteSerializer,
    TeacherProfileSerializer,
    TokenPairSerializer,
    VerifySerializer,
)


@extend_schema(tags=["auth"], request=VerifySerializer, responses=TokenPairSerializer)
class VerifyView(APIView):
    """Telegram botdan olingan kod → JWT (access + refresh) va profil."""

    permission_classes = [AllowAny]
    authentication_classes: list = []
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = "auth_verify"

    def post(self, request):
        serializer = VerifySerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        user = services.verify_login_code(**serializer.validated_data)
        refresh = RefreshToken.for_user(user)
        return Response(
            {
                "access": str(refresh.access_token),
                "refresh": str(refresh),
                "user": MeSerializer(user).data,
            }
        )


@extend_schema(tags=["me"])
class MeView(generics.RetrieveUpdateAPIView):
    serializer_class = MeSerializer
    http_method_names = ["get", "patch", "options"]

    def get_object(self):
        return self.request.user


@extend_schema(tags=["parents"], summary="Ota-ona uchun taklif kodi/havolasi", request=None)
class ParentInviteView(APIView):
    permission_classes = [IsStudent]

    @extend_schema(responses={201: ParentInviteSerializer})
    def post(self, request):
        invite = services.create_parent_invite(request.user)
        return Response(ParentInviteSerializer(invite).data, status=status.HTTP_201_CREATED)


def children_of(parent: User):
    return User.objects.filter(parent_links__parent=parent)


@extend_schema_view(
    get=extend_schema(tags=["parents"], summary="Bog'langan farzandlar"),
    post=extend_schema(
        tags=["parents"],
        summary="Kod orqali farzandga bog'lanish",
        request=LinkChildSerializer,
        responses={201: ChildSerializer},
    ),
)
class ChildrenView(generics.ListAPIView):
    serializer_class = ChildSerializer
    permission_classes = [IsParent]

    def get_queryset(self):
        enrollments = Enrollment.objects.filter(student=OuterRef("pk"))
        return (
            children_of(self.request.user)
            .annotate(
                linked_at=F("parent_links__created_at"),
                courses_count=subquery_count(enrollments, "student"),
                completed_courses_count=subquery_count(
                    enrollments.filter(completed_at__isnull=False), "student"
                ),
            )
            .order_by("parent_links__created_at")
        )

    def post(self, request):
        serializer = LinkChildSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        link = services.accept_parent_invite(request.user, serializer.validated_data["code"])
        child = self.get_queryset().get(pk=link.student_id)
        return Response(ChildSerializer(child).data, status=status.HTTP_201_CREATED)


@extend_schema(tags=["parents"], summary="Farzandning kurslari va progressi")
class ChildProgressView(generics.ListAPIView):
    serializer_class = MyCourseSerializer
    permission_classes = [IsParent]

    def get_queryset(self):
        # Bog'lanmagan farzand — 404 (ma'lumot borligini ham oshkor qilmaymiz)
        child = get_object_or_404(children_of(self.request.user), pk=self.kwargs["pk"])
        return enrollments_with_progress(student=child)


@extend_schema_view(
    get=extend_schema(tags=["teacher"], summary="Mening teacher profilim"),
    patch=extend_schema(
        tags=["teacher"],
        summary="Profilni tahrirlash (ism, mutaxassislik, bio, rasm)",
        request={"multipart/form-data": TeacherProfileSerializer},
    ),
)
class TeacherProfileView(generics.RetrieveUpdateAPIView):
    serializer_class = TeacherProfileSerializer
    permission_classes = [IsTeacher]
    parser_classes = [MultiPartParser, JSONParser]
    http_method_names = ["get", "patch", "options"]

    def get_object(self):
        profile, _ = TeacherProfile.objects.select_related("user").get_or_create(
            user=self.request.user
        )
        return profile
