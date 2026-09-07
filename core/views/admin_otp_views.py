"""
Admin API Views for Virtual Numbers & ZapOTP Management.
Controls margins, API keys, balance monitoring, and global order audits.
"""
import logging
from decimal import Decimal
from rest_framework import status, permissions
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination

from core.models import OTPOrder, OTPProviderSetting
from core.serializers import OTPOrderAdminSerializer, OTPProviderSettingSerializer
from core.services.zapotp import ZapOTPClient, ZapOTPError

logger = logging.getLogger(__name__)


class AdminPagination(PageNumberPagination):
    page_size = 25
    page_size_query_param = 'page_size'
    max_page_size = 100


class AdminOTPSettingView(APIView):
    """
    GET /api/admin/otp/settings/
    PATCH /api/admin/otp/settings/
    Admin reads and updates ZapOTP API keys, markups, thresholds.
    """
    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        settings = OTPProviderSetting.get_settings()
        serializer = OTPProviderSettingSerializer(settings)
        return Response(serializer.data)

    def patch(self, request):
        settings = OTPProviderSetting.get_settings()
        serializer = OTPProviderSettingSerializer(settings, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class AdminOTPBalanceView(APIView):
    """
    GET /api/admin/otp/balance/
    Fetches real-time upstream ZapOTP balance & account details.
    """
    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        client = ZapOTPClient()
        try:
            balance_info = client.get_balance()
            return Response({"status": "success", "data": balance_info})
        except ZapOTPError as e:
            return Response({"detail": str(e)}, status=status.HTTP_502_BAD_GATEWAY)
        except Exception as e:
            logger.error(f"Error fetching admin ZapOTP balance: {e}")
            return Response({"detail": "Failed to fetch upstream ZapOTP balance."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class AdminOTPOrdersView(APIView):
    """
    GET /api/admin/otp/orders/
    Full searchable audit log of all customer OTP verification orders + summary stats.
    """
    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        queryset = OTPOrder.objects.select_related('user').all().order_by('-created_at')

        # Filters
        status_filter = request.query_params.get('status')
        if status_filter:
            queryset = queryset.filter(status=status_filter.upper())

        search = request.query_params.get('search')
        if search:
            queryset = queryset.filter(
                phone_number__icontains=search
            ) | queryset.filter(
                provider_order_id__icontains=search
            ) | queryset.filter(
                user__email__icontains=search
            ) | queryset.filter(
                service_name__icontains=search
            )

        # Summary Analytics
        total_orders = OTPOrder.objects.count()
        received_count = OTPOrder.objects.filter(status=OTPOrder.Status.RECEIVED).count()
        refunded_count = OTPOrder.objects.filter(status__in=[OTPOrder.Status.REFUNDED, OTPOrder.Status.CANCELED, OTPOrder.Status.EXPIRED]).count()
        total_profit = sum(o.profit for o in OTPOrder.objects.filter(status=OTPOrder.Status.RECEIVED))

        success_rate = (received_count / total_orders * 100) if total_orders > 0 else 0

        paginator = AdminPagination()
        page = paginator.paginate_queryset(queryset, request)
        serializer = OTPOrderAdminSerializer(page, many=True)

        response = paginator.get_paginated_response(serializer.data)
        response.data['analytics'] = {
            "total_orders": total_orders,
            "received_count": received_count,
            "refunded_count": refunded_count,
            "success_rate": round(success_rate, 1),
            "total_profit": float(total_profit)
        }
        return response
