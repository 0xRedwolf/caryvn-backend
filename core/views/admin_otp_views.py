"""
Admin API Views for Virtual Numbers & ZapOTP Management.
Controls margins, API keys, balance monitoring, and global order audits.
"""
import logging
from decimal import Decimal
from django.utils import timezone
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
        # Auto-sync up to 3 recent pending orders from ZapOTP so admin sees latest codes on page load
        recent_pending = OTPOrder.objects.filter(
            status=OTPOrder.Status.PENDING,
            created_at__gte=timezone.now() - timezone.timedelta(minutes=25)
        ).order_by('-created_at')[:3]

        if recent_pending:
            client = ZapOTPClient()
            for p_order in recent_pending:
                try:
                    sms_data = client.get_sms(p_order.provider_order_id)
                    up_status = str(sms_data.get('status', '')).upper().strip()
                    code = sms_data.get('sms_code')
                    f_sms = str(sms_data.get('full_sms', '')).strip()
                    if (up_status in ['RECEIVED', 'FINISHED', 'SUCCESS', 'COMPLETED'] and (code or f_sms)) or (code and len(str(code).strip()) >= 3):
                        p_order.status = OTPOrder.Status.RECEIVED
                        p_order.sms_code = str(code).strip() if code else (f_sms[:50] if f_sms else 'DELIVERED')
                        p_order.full_sms = f_sms or str(code or '')
                        p_order.received_at = timezone.now()
                        p_order.save(update_fields=['status', 'sms_code', 'full_sms', 'received_at', 'updated_at'])
                except Exception:
                    pass

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


class AdminOTPSyncOrderView(APIView):
    """
    POST /api/admin/otp/orders/<uuid:pk>/sync/
    Superadmin forces a real-time status check and OTP sync from ZapOTP.
    """
    permission_classes = [permissions.IsAdminUser]

    def post(self, request, pk):
        from django.utils import timezone
        try:
            order = OTPOrder.objects.get(pk=pk)
        except OTPOrder.DoesNotExist:
            return Response({"detail": "Order not found."}, status=status.HTTP_404_NOT_FOUND)

        client = ZapOTPClient()
        try:
            sms_data = client.get_sms(order.provider_order_id)
        except Exception as e:
            return Response({"detail": f"ZapOTP query failed: {e}"}, status=status.HTTP_502_BAD_GATEWAY)

        upstream_status = str(sms_data.get('status', 'PENDING')).upper().strip()
        sms_code = sms_data.get('sms_code')
        full_sms = str(sms_data.get('full_sms', '')).strip()

        updated = False
        if (upstream_status in ['RECEIVED', 'FINISHED', 'SUCCESS', 'COMPLETED'] and (sms_code or full_sms)) or (sms_code and len(str(sms_code).strip()) >= 3):
            order.status = OTPOrder.Status.RECEIVED
            order.sms_code = str(sms_code).strip() if sms_code else (full_sms[:50] if full_sms else 'DELIVERED')
            order.full_sms = full_sms or str(sms_code or '')
            order.received_at = timezone.now()
            order.save(update_fields=['status', 'sms_code', 'full_sms', 'received_at', 'updated_at'])
            updated = True
        elif upstream_status in ['CANCELED', 'CANCELLED'] and not sms_code:
            if order.status != OTPOrder.Status.CANCELED:
                order.status = OTPOrder.Status.CANCELED
                order.profit = Decimal('0.00')
                order.refunded_at = timezone.now()
                order.save(update_fields=['status', 'profit', 'refunded_at', 'updated_at'])
                order.user.wallet.refund(
                    order.user_charge,
                    description=f"Provider Cancel: {order.service_name} ({order.phone_number})"
                )
                updated = True

        serializer = OTPOrderAdminSerializer(order)
        msg = f"Synced with ZapOTP: Status is '{order.status}'"
        if order.sms_code:
            msg += f" (Code: {order.sms_code})"
        return Response({
            "status": "success",
            "message": msg,
            "order": serializer.data,
            "updated": updated
        })


class AdminOTPCancelRefundView(APIView):
    """
    POST /api/admin/otp/orders/<uuid:pk>/cancel/
    Superadmin cancels order and refunds user.
    Body: {"force": bool}
    If force=False: queries ZapOTP first. If ZapOTP delivered the code, rejects cancellation.
    If force=True: admin override, proceeds with cancel & refund regardless.
    """
    permission_classes = [permissions.IsAdminUser]

    def post(self, request, pk):
        from django.utils import timezone
        try:
            order = OTPOrder.objects.get(pk=pk)
        except OTPOrder.DoesNotExist:
            return Response({"detail": "Order not found."}, status=status.HTTP_404_NOT_FOUND)

        if order.status in [OTPOrder.Status.CANCELED, OTPOrder.Status.REFUNDED, OTPOrder.Status.EXPIRED]:
            return Response({"detail": f"Order is already in '{order.status}' status."}, status=status.HTTP_400_BAD_REQUEST)

        force = bool(request.data.get('force', False))
        refund = bool(request.data.get('refund', True))

        if not force:
            # Check upstream ZapOTP first
            client = ZapOTPClient()
            try:
                sms_data = client.get_sms(order.provider_order_id)
                upstream_status = str(sms_data.get('status', '')).upper().strip()
                sms_code = sms_data.get('sms_code')
                full_sms = str(sms_data.get('full_sms', '')).strip()

                if (upstream_status in ['RECEIVED', 'FINISHED', 'SUCCESS', 'COMPLETED'] and (sms_code or full_sms)) or (sms_code and len(str(sms_code).strip()) >= 3):
                    order.status = OTPOrder.Status.RECEIVED
                    order.sms_code = str(sms_code).strip() if sms_code else (full_sms[:50] if full_sms else 'DELIVERED')
                    order.full_sms = full_sms or str(sms_code or '')
                    order.received_at = timezone.now()
                    order.save(update_fields=['status', 'sms_code', 'full_sms', 'received_at', 'updated_at'])
                    serializer = OTPOrderAdminSerializer(order)
                    return Response({
                        "detail": f"Upstream ZapOTP already fulfilled this order (Status: {upstream_status}, Code: {order.sms_code}). Standard cancel rejected to prevent financial loss. Use Force Cancel if you still want to cancel.",
                        "fulfilled": True,
                        "order": serializer.data
                    }, status=status.HTTP_400_BAD_REQUEST)
            except Exception as e:
                logger.warning(f"ZapOTP check warning during admin cancel: {e}")

        # Attempt to notify ZapOTP
        try:
            client = ZapOTPClient()
            client.cancel_order(order.provider_order_id)
        except Exception:
            pass

        # Execute cancellation
        order.status = OTPOrder.Status.CANCELED
        order.profit = Decimal('0.00')
        if refund:
            order.refunded_at = timezone.now()
            order.save(update_fields=['status', 'profit', 'refunded_at', 'updated_at'])
            order.user.wallet.refund(
                order.user_charge,
                description=f"Admin Cancel & Refund: {order.service_name} ({order.phone_number})"
            )
            msg = f"Order #{str(order.id)[:8].upper()} canceled and ₦{float(order.user_charge):,.2f} refunded to {order.user.email}."
        else:
            order.save(update_fields=['status', 'profit', 'updated_at'])
            msg = f"Order #{str(order.id)[:8].upper()} canceled without refund."

        serializer = OTPOrderAdminSerializer(order)
        return Response({
            "status": "success",
            "message": msg,
            "order": serializer.data
        })


class AdminOTPCompleteOrderView(APIView):
    """
    POST /api/admin/otp/orders/<uuid:pk>/complete/
    Superadmin forces order to completed (RECEIVED) state, optionally providing an SMS code.
    """
    permission_classes = [permissions.IsAdminUser]

    def post(self, request, pk):
        from django.utils import timezone
        try:
            order = OTPOrder.objects.get(pk=pk)
        except OTPOrder.DoesNotExist:
            return Response({"detail": "Order not found."}, status=status.HTTP_404_NOT_FOUND)

        code = request.data.get('sms_code')
        order.status = OTPOrder.Status.RECEIVED
        if code:
            order.sms_code = str(code).strip()
        elif not order.sms_code:
            order.sms_code = 'DELIVERED'
        order.received_at = timezone.now()
        order.save(update_fields=['status', 'sms_code', 'received_at', 'updated_at'])

        serializer = OTPOrderAdminSerializer(order)
        return Response({
            "status": "success",
            "message": f"Order #{str(order.id)[:8].upper()} marked as completed.",
            "order": serializer.data
        })


class AdminOTPDeleteOrderView(APIView):
    """
    DELETE /api/admin/otp/orders/<uuid:pk>/
    Superadmin permanently deletes an OTP order record.
    """
    permission_classes = [permissions.IsAdminUser]

    def delete(self, request, pk):
        try:
            order = OTPOrder.objects.get(pk=pk)
        except OTPOrder.DoesNotExist:
            return Response({"detail": "Order not found."}, status=status.HTTP_404_NOT_FOUND)

        order_id_str = str(order.id)[:8].upper()
        order.delete()
        return Response({
            "status": "success",
            "message": f"Order #{order_id_str} deleted successfully."
        })

