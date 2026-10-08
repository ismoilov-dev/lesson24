from django.db.models import Q
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from rest_framework import generics, status
from rest_framework.parsers import MultiPartParser
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.users.permissions import IsStudentOrParent

from . import services
from .models import Order
from .serializers import (
    OrderCreateSerializer,
    OrderSerializer,
    PaymentInfoQuerySerializer,
    PaymentInfoSerializer,
)


@extend_schema_view(
    get=extend_schema(tags=["orders"], summary="Buyurtmalarim va holati"),
    post=extend_schema(
        tags=["orders"],
        summary="Chek skrinshoti bilan buyurtma",
        request={"multipart/form-data": OrderCreateSerializer},
        responses={201: OrderSerializer},
    ),
)
class MyOrdersView(generics.ListAPIView):
    serializer_class = OrderSerializer
    permission_classes = [IsStudentOrParent]
    parser_classes = [MultiPartParser]

    def get_queryset(self):
        user = self.request.user
        # O'zi to'lagan va (student uchun) ota-onasi uning nomiga to'lagan buyurtmalar
        return Order.objects.filter(Q(user=user) | Q(student=user)).select_related(
            "course", "student"
        )

    def post(self, request):
        serializer = OrderCreateSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        order = services.create_order(payer=request.user, **serializer.validated_data)
        return Response(OrderSerializer(order).data, status=status.HTTP_201_CREATED)


@extend_schema(
    tags=["orders"],
    summary="Karta raqami va summa",
    parameters=[OpenApiParameter("course", str, required=True, description="Kurs slug")],
    responses=PaymentInfoSerializer,
)
class PaymentInfoView(APIView):
    def get(self, request):
        query = PaymentInfoQuerySerializer(data=request.query_params)
        query.is_valid(raise_exception=True)
        info = services.payment_info(query.validated_data["course"])
        return Response(PaymentInfoSerializer(info).data)
