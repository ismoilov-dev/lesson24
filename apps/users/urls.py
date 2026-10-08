from django.urls import path
from drf_spectacular.utils import extend_schema, extend_schema_view
from rest_framework.routers import SimpleRouter
from rest_framework_simplejwt.views import TokenRefreshView

from .admin_api import AdminParentLinkViewSet, AdminStatsView, AdminUserViewSet
from .views import (
    ChildProgressView,
    ChildrenView,
    MeView,
    ParentInviteView,
    TeacherProfileView,
    VerifyView,
)

RefreshView = extend_schema_view(post=extend_schema(tags=["auth"]))(TokenRefreshView)

admin_router = SimpleRouter()
admin_router.register("admin/users", AdminUserViewSet, basename="admin-user")
admin_router.register("admin/parent-links", AdminParentLinkViewSet, basename="admin-parent-link")

urlpatterns = [
    path("admin/stats/", AdminStatsView.as_view(), name="admin-stats"),
    path("auth/verify/", VerifyView.as_view(), name="auth-verify"),
    path("auth/refresh/", RefreshView.as_view(), name="auth-refresh"),
    path("me/", MeView.as_view(), name="me"),
    path("me/parent-invites/", ParentInviteView.as_view(), name="parent-invites"),
    path("me/children/", ChildrenView.as_view(), name="children"),
    path("me/children/<int:pk>/progress/", ChildProgressView.as_view(), name="child-progress"),
    # path("teacher/profile/", TeacherProfileView.as_view(), name="teacher-profile"),
    # *admin_router.urls,
]
