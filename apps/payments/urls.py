from django.urls import path
from rest_framework.routers import SimpleRouter

from .admin_api import AdminOrderViewSet
from .views import MyOrdersView, PaymentInfoView

router = SimpleRouter()
router.register("admin/orders", AdminOrderViewSet, basename="admin-order")

urlpatterns = [
    path("me/orders/", MyOrdersView.as_view(), name="my-orders"),
    path("me/orders/payment-info/", PaymentInfoView.as_view(), name="payment-info"),
    *router.urls,
]
