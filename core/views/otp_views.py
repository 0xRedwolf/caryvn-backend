"""
Customer API Views for Virtual Numbers & SMS OTP Verification.
"""
import logging
from decimal import Decimal
from datetime import timedelta
from django.utils import timezone
from rest_framework import status, permissions
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework.pagination import PageNumberPagination

from core.models import OTPOrder, OTPProviderSetting, Wallet
from core.serializers import OTPOrderSerializer
from core.services.zapotp import ZapOTPClient, ZapOTPError

logger = logging.getLogger(__name__)


class StandardResultsSetPagination(PageNumberPagination):
    page_size = 20
    page_size_query_param = 'page_size'
    max_page_size = 100


class OTPStatusView(APIView):
    """
    GET /api/otp/status/
    Returns whether the virtual number service is active for users.
    """
    permission_classes = [permissions.AllowAny]

    def get(self, request):
        setting = OTPProviderSetting.get_settings()
        return Response({
            "is_active": setting.is_active,
        })


class OTPServicesView(APIView):
    """
    GET /api/otp/services/
    Returns list of available verification services for a country with Caryvn pricing applied.
    Query params: country (ISO code, e.g. US), provider (global|usa|globalv2), service (optional)
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        setting = OTPProviderSetting.get_settings()
        if not setting.is_active:
            return Response(
                {"detail": "Virtual number OTP verification service is temporarily undergoing maintenance."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE
            )

        country = request.query_params.get('country', 'US').upper()
        provider = request.query_params.get('provider', 'global')
        service = request.query_params.get('service', None)

        try:
            client = ZapOTPClient()
            raw_services = client.get_services(country=country, provider=provider, service=service)
        except ZapOTPError as e:
            return Response({"detail": str(e)}, status=status.HTTP_502_BAD_GATEWAY)
        except Exception as e:
            logger.error(f"Unexpected error fetching OTP services: {e}", exc_info=True)
            return Response({"detail": "Failed to load services. Please try again."}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)

        # Apply Caryvn hybrid markup
        processed_services = []
        for idx, s in enumerate(raw_services):
            base_price = Decimal(str(s.get('price', 0.00)))
            user_price = setting.calculate_price(base_price)
            
            service_id = str(s.get('service_id') or s.get('service') or s.get('id') or service or '').strip()
            service_name = str(s.get('service_name') or s.get('name') or s.get('service') or service_id).strip()
            pool_id = str(s.get('pool') or s.get('pool_id') or s.get('server') or s.get('id') or '').strip()
            pool_name = str(s.get('pool_name') or s.get('name') or (f"Server Route #{idx + 1}" if service else '')).strip()
            
            item = dict(s)
            item['service_id'] = service_id
            item['service_name'] = service_name
            if pool_id:
                item['pool_id'] = pool_id
            if pool_name:
                item['pool_name'] = pool_name
            item['provider'] = s.get('provider') or provider
            item['wholesale_price'] = float(base_price)
            item['price'] = float(user_price)
            processed_services.append(item)

        return Response({
            "status": "success",
            "country": country,
            "provider": provider,
            "services": processed_services
        })


class OTPRentNumberView(APIView):
    """
    POST /api/otp/rent/
    Atomically charge user's wallet and rent a virtual number via ZapOTP.
    Body:
    {
        "country": "US",
        "service": "whatsapp",
        "service_name": "WhatsApp",
        "provider": "global",
        "rental_type": "short" | "long",
        "days": 3 (if long),
        "pool": "1" (optional)
    }
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        setting = OTPProviderSetting.get_settings()
        if not setting.is_active:
            return Response(
                {"detail": "Virtual number verification is currently disabled."},
                status=status.HTTP_503_SERVICE_UNAVAILABLE
            )

        data = request.data
        country = data.get('country', 'US').upper()
        service_id = data.get('service', '').strip()
        service_name = data.get('service_name', service_id.capitalize())
        rental_type = data.get('rental_type', OTPOrder.RentalType.SHORT)
        provider = data.get('provider', 'usa_long' if rental_type == OTPOrder.RentalType.LONG else 'global')
        rental_days = int(data.get('days', 0)) if rental_type == OTPOrder.RentalType.LONG else 0
        pool = data.get('pool')

        if not service_id:
            return Response({"detail": "Service identifier is required."}, status=status.HTTP_400_BAD_REQUEST)

        # 1. Fetch current upstream price to ensure accuracy
        client = ZapOTPClient()
        try:
            services = client.get_services(country=country, provider=provider, service=service_id)
            matched = None
            if pool:
                matched = next(
                    (s for s in services if str(s.get('pool') or s.get('pool_id') or s.get('id') or '').strip().lower() == str(pool).strip().lower()),
                    None
                )
            if not matched:
                matched = next(
                    (s for s in services if str(s.get('service_id') or s.get('service') or s.get('id') or '').strip().lower() == service_id.lower()),
                    None
                )
            
            # If not directly matched, check if services itself is single dict
            if not matched and isinstance(services, list) and len(services) > 0:
                matched = services[0]

            if matched:
                provider_cost = Decimal(str(matched.get('price', '0.00')))
            else:
                # Fallback to standard price if ZapOTP doesn't return drilldown
                provider_cost = Decimal('450.00')
        except Exception as e:
            logger.warning(f"Could not verify price upstream before rent, using fallback: {e}")
        # Scale cost for long-term duration (base cost is for 3 days)
        if rental_type == OTPOrder.RentalType.LONG and rental_days > 0:
            duration_mult = Decimal(str(max(1.0, float(rental_days) / 3.0)))
            provider_cost = provider_cost * duration_mult

        user_charge = setting.calculate_price(provider_cost)

        # 2. Check user wallet balance
        wallet = request.user.wallet
        if wallet.balance < user_charge:
            return Response(
                {
                    "detail": f"Insufficient wallet balance. You need ₦{user_charge:,.2f} but currently have ₦{wallet.balance:,.2f}. Please top up your wallet.",
                    "required_balance": float(user_charge),
                    "current_balance": float(wallet.balance)
                },
                status=status.HTTP_400_BAD_REQUEST
            )

        # 3. Atomically charge wallet
        charge_desc = f"Virtual Number: {service_name} ({country})"
        try:
            wallet.charge(user_charge, description=charge_desc)
        except ValueError as e:
            return Response({"detail": str(e)}, status=status.HTTP_400_BAD_REQUEST)

        # 4. Dispatch ZapOTP upstream rental
        try:
            if rental_type == OTPOrder.RentalType.LONG:
                rent_res = client.rent_long_number(
                    service=service_id,
                    country=country,
                    days=rental_days or 3,
                    provider="usa_long"
                )
            else:
                rent_res = client.rent_number(
                    country=country,
                    service=service_id,
                    provider=provider,
                    pool=pool
                )
        except ZapOTPError as e:
            # Immediate atomic refund on upstream failure!
            logger.warning(f"ZapOTP rent failed for user {request.user.email}, auto-refunding: {e}")
            wallet.refund(user_charge, description=f"Refund: Failed to reserve {service_name} number")
            return Response(
                {"detail": f"Failed to acquire number from provider: {str(e)}. Your wallet has been 100% refunded."},
                status=status.HTTP_502_BAD_GATEWAY
            )
        except Exception as e:
            logger.error(f"Unexpected error renting number: {e}", exc_info=True)
            wallet.refund(user_charge, description=f"Refund: Error reserving {service_name} number")
            return Response(
                {"detail": "An unexpected error occurred. Your wallet has been 100% refunded."},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        # 5. Parse expiry and create OTPOrder
        upstream_price = rent_res.get('price')
        if not upstream_price or Decimal(str(upstream_price)) <= Decimal('0.00'):
            upstream_price = provider_cost
        expires_at = timezone.now() + timedelta(minutes=15)
        if rental_type == OTPOrder.RentalType.LONG and rental_days > 0:
            expires_at = timezone.now() + timedelta(days=rental_days)

        profit = max(Decimal('0.00'), user_charge - upstream_price)

        order = OTPOrder.objects.create(
            user=request.user,
            provider_order_id=rent_res['order_id'],
            phone_number=rent_res['number'],
            country=country,
            service_id=service_id,
            service_name=service_name,
            provider=provider,
            rental_type=rental_type,
            rental_days=rental_days,
            provider_cost=upstream_price,
            user_charge=user_charge,
            profit=profit,
            status=OTPOrder.Status.PENDING,
            expires_at=expires_at
        )

        serializer = OTPOrderSerializer(order)
        return Response({
            "status": "success",
            "message": "Number rented successfully! Waiting for incoming SMS.",
            "order": serializer.data
        }, status=status.HTTP_201_CREATED)


class OTPOrderPollSMSView(APIView):
    """
    GET /api/otp/orders/<uuid:pk>/sms/
    Polls ZapOTP for incoming verification SMS code.
    If RECEIVED, updates order and marks completion.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request, pk):
        try:
            order = OTPOrder.objects.get(pk=pk, user=request.user)
        except OTPOrder.DoesNotExist:
            return Response({"detail": "Order not found."}, status=status.HTTP_404_NOT_FOUND)

        # If already received, canceled, or refunded, return current state
        if order.status in [OTPOrder.Status.RECEIVED, OTPOrder.Status.CANCELED, OTPOrder.Status.REFUNDED]:
            serializer = OTPOrderSerializer(order)
            return Response({"status": "success", "order": serializer.data})

        # Check if already expired by time
        if timezone.now() >= order.expires_at:
            # Auto-refund if still pending
            if order.status == OTPOrder.Status.PENDING:
                order.status = OTPOrder.Status.EXPIRED
                order.refunded_at = timezone.now()
                order.save(update_fields=['status', 'refunded_at', 'updated_at'])
                request.user.wallet.refund(
                    order.user_charge, 
                    description=f"Auto-Refund: Expired {order.service_name} number"
                )
            serializer = OTPOrderSerializer(order)
            return Response({
                "status": "expired",
                "message": "Verification session expired. Your wallet has been 100% refunded.",
                "order": serializer.data
            })

        # Poll upstream ZapOTP
        client = ZapOTPClient()
        try:
            sms_data = client.get_sms(order.provider_order_id)
        except ZapOTPError as e:
            return Response({"detail": str(e)}, status=status.HTTP_502_BAD_GATEWAY)

        upstream_status = sms_data.get('status', 'PENDING')
        sms_code = sms_data.get('sms_code')
        full_sms = sms_data.get('full_sms', '')

        if upstream_status == 'RECEIVED' and sms_code:
            order.status = OTPOrder.Status.RECEIVED
            order.sms_code = sms_code
            order.full_sms = full_sms
            order.received_at = timezone.now()
            order.save(update_fields=['status', 'sms_code', 'full_sms', 'received_at', 'updated_at'])
        elif upstream_status in ['CANCELED', 'CANCELLED']:
            order.status = OTPOrder.Status.CANCELED
            order.profit = Decimal('0.00')
            order.refunded_at = timezone.now()
            order.save(update_fields=['status', 'profit', 'refunded_at', 'updated_at'])
            request.user.wallet.refund(
                order.user_charge,
                description=f"Refund: Canceled {order.service_name} number"
            )

        serializer = OTPOrderSerializer(order)
        return Response({
            "status": "success",
            "order": serializer.data
        })


class OTPOrderCancelView(APIView):
    """
    POST /api/otp/orders/<uuid:pk>/cancel/
    User initiates manual cancellation and refund after 2 minutes of waiting.
    """
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request, pk):
        try:
            order = OTPOrder.objects.get(pk=pk, user=request.user)
        except OTPOrder.DoesNotExist:
            return Response({"detail": "Order not found."}, status=status.HTTP_404_NOT_FOUND)

        if order.status != OTPOrder.Status.PENDING:
            return Response(
                {"detail": f"Cannot cancel order with status '{order.status}'."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Enforce 2-minute grace window before user can manually cancel
        time_elapsed = timezone.now() - order.created_at
        if time_elapsed.total_seconds() < 120:
            remaining = int(120 - time_elapsed.total_seconds())
            return Response(
                {"detail": f"Please wait at least 2 minutes for SMS to deliver. You can cancel in {remaining}s."},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Dispatch upstream cancellation to ZapOTP to release carrier pool
        try:
            client = ZapOTPClient()
            client.cancel_order(order.provider_order_id)
        except Exception as e:
            logger.warning(f"Could not cancel ZapOTP order {order.provider_order_id}: {e}")

        # Mark canceled and refund 100%
        order.status = OTPOrder.Status.CANCELED
        order.profit = Decimal('0.00')
        order.refunded_at = timezone.now()
        order.save(update_fields=['status', 'profit', 'refunded_at', 'updated_at'])

        request.user.wallet.refund(
            order.user_charge,
            description=f"User Canceled Refund: {order.service_name} ({order.phone_number})"
        )

        serializer = OTPOrderSerializer(order)
        return Response({
            "status": "success",
            "message": "Order canceled. Your wallet balance has been 100% refunded.",
            "order": serializer.data
        })


class OTPOrderHistoryView(APIView):
    """
    GET /api/otp/orders/
    Paginated list of user's OTP verification orders.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        orders = OTPOrder.objects.filter(user=request.user).order_by('-created_at')
        
        status_filter = request.query_params.get('status')
        if status_filter:
            orders = orders.filter(status=status_filter.upper())

        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(orders, request)
        serializer = OTPOrderSerializer(page, many=True)
        return paginator.get_paginated_response(serializer.data)
