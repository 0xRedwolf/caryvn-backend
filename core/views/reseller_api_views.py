"""
Public SMM Panel API v2  Reseller Endpoint.

Single POST endpoint: /api/v2/
Accepts: application/x-www-form-urlencoded

Supported actions:
    services  — List all active services in SMM Panel v2 format
    add       — Place an order
    status    — Get order status by reseller_order_id
    balance   — Get the authenticated user's wallet balance
    refill    — Request a refill for a completed order

Authentication: key=USER_API_KEY in POST body or GET query string.

"""
import logging
from decimal import Decimal, InvalidOperation
from urllib.parse import urlparse

from rest_framework import status, permissions
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.throttling import ScopedRateThrottle
from django.db import transaction as db_transaction
from django.utils import timezone

from ..models import Order, Service, Wallet
from ..authentication import APIKeyAuthentication
from ..services.smm_provider import SMMProviderError, get_provider_client

logger = logging.getLogger(__name__)


def _error(msg: str, http_status: int = 400) -> Response:
    """Return a standard SMM Panel v2 error response."""
    return Response({'error': msg}, status=http_status)


class ResellerAPIView(APIView):
    """
    Public SMM Panel API v2 endpoint.

    All actions are dispatched from a single POST to /api/v2/.
    Authentication is via API key (key= param), handled by APIKeyAuthentication.
    APIKeyAuthentication is scoped here only — it is NOT in DEFAULT_AUTHENTICATION_CLASSES.
    """
    # Scoped: JWT for internal use, APIKey for reseller scripts.
    # This prevents API keys from authenticating on any other endpoint.
    from rest_framework_simplejwt.authentication import JWTAuthentication
    authentication_classes = [JWTAuthentication, APIKeyAuthentication]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'reseller_api'

    def post(self, request):
        action = request.data.get('action', '').strip().lower()

        if action == 'services':
            return self._action_services(request)
        elif action == 'add':
            return self._action_add(request)
        elif action == 'status':
            return self._action_status(request)
        elif action == 'balance':
            return self._action_balance(request)
        elif action == 'refill':
            return self._action_refill(request)
        else:
            return _error('Invalid action. Supported: services, add, status, balance, refill')

    # ─────────────────────────────────────────────────────────────────────────
    # action=services
    # ─────────────────────────────────────────────────────────────────────────

    def _action_services(self, request):
        """
        Return all active services in SMM Panel v2 list format.

        Response (list):
            [
                {
                    "service": <int id>,
                    "name": "...",
                    "type": "Default",
                    "category": "...",
                    "rate": "0.85",
                    "min": "10",
                    "max": "50000",
                    "refill": false,
                    "cancel": false
                },
                ...
            ]
        """
        from django.db.models import Q

        services = (
            Service.objects
            .filter(
                provider__is_active=True,
                provider_is_active=True,
                is_active=True,
            )
            .select_related('provider')
            .order_by('category_name', 'name')
        )

        result = []
        for svc in services:
            result.append({
                'service': svc.id,
                'name': svc.name,
                'type': svc.service_type or 'Default',
                'category': svc.category_name,
                'rate': str(svc.user_rate),
                'min': str(svc.min_quantity),
                'max': str(svc.max_quantity),
                'refill': svc.has_refill,
                'cancel': svc.has_cancel,
                'description': svc.description or '',
                'average_time': svc.average_time or '',
            })

        return Response(result)

    # ─────────────────────────────────────────────────────────────────────────
    # action=add
    # ─────────────────────────────────────────────────────────────────────────

    @db_transaction.atomic
    def _action_add(self, request):
        """
        Place a new order.

        Request params:
            service  <int>   — Service ID (our internal DB id)
            link     <str>   — Target URL
            quantity <int>   — Number of units

        Response:
            {"order": <reseller_order_id>}
        """
        from django.core.cache import cache
        from ..tasks import submit_order_to_provider

        # ── Validate inputs ───────────────────────────────────────────────
        service_id = request.data.get('service')
        link = request.data.get('link', '').strip()
        quantity_raw = request.data.get('quantity')
        comments_raw = request.data.get('comments', '').strip()
        comments = comments_raw[:500] if comments_raw else None  # Max 500 chars

        if not service_id:
            return _error('Missing required parameter: service')
        if not link:
            return _error('Missing required parameter: link')
        if not quantity_raw:
            return _error('Missing required parameter: quantity')

        # Validate link is a proper http/https URL
        try:
            parsed = urlparse(link)
            if parsed.scheme not in ('http', 'https') or not parsed.netloc:
                raise ValueError
        except (ValueError, Exception):
            return _error('Parameter "link" must be a valid http:// or https:// URL')

        try:
            service_id = int(service_id)
        except (TypeError, ValueError):
            return _error('Parameter "service" must be an integer')

        try:
            quantity = int(quantity_raw)
        except (TypeError, ValueError):
            return _error('Parameter "quantity" must be an integer')

        # ── Fetch service ─────────────────────────────────────────────────
        try:
            service = Service.objects.get(
                id=service_id,
                is_active=True,
                provider_is_active=True,
                provider__is_active=True,
            )
        except Service.DoesNotExist:
            return _error('Service not found or currently unavailable')

        # ── Validate quantity ─────────────────────────────────────────────
        if quantity < service.min_quantity:
            return _error(f'Minimum quantity for this service is {service.min_quantity}')
        if quantity > service.max_quantity:
            return _error(f'Maximum quantity for this service is {service.max_quantity}')

        # ── Concurrency / duplicate order lock ────────────────────────────
        lock_key = f'order_lock_{request.user.id}_{service.id}_{link}'
        if not cache.add(lock_key, 'locked', timeout=60):
            return _error('Too many requests. Please wait a moment before trying again.', 429)

        # Active duplicate check
        active_statuses = [Order.Status.PENDING, Order.Status.PROCESSING, Order.Status.IN_PROGRESS]
        if Order.objects.filter(
            user=request.user,
            service=service,
            link=link,
            status__in=active_statuses,
        ).exists():
            cache.delete(lock_key)
            return _error('You already have an active order for this link + service combination. Please wait for it to complete.')

        # ── Calculate charge ──────────────────────────────────────────────
        charge = service.calculate_price(quantity)

        # ── Check balance ─────────────────────────────────────────────────
        wallet = Wallet.objects.get(user=request.user)
        if wallet.balance < charge:
            cache.delete(lock_key)
            return _error(
                f'Insufficient balance. Required: {charge}, Available: {wallet.balance}'
            )

        # ── Create order ───────────────────────────────────────────────────
        order = Order.objects.create(
            user=request.user,
            service=service,
            provider=service.provider,
            link=link,
            quantity=quantity,
            provider_rate=service.provider_rate,
            provider_rate_ngn=service.provider_rate_ngn,
            user_rate=service.user_rate,
            charge=charge,
            status=Order.Status.PENDING,
            source=Order.Source.API,  # Tag this as an API-sourced order
        )

        order.calculate_profit()
        order.save()

        # ── Deduct wallet ─────────────────────────────────────────────────
        wallet.charge(charge, f'Order #{str(order.id)[:8]} - {service.name}')

        # ── Refresh to get reseller_order_id (assigned by post_save signal) ──
        order.refresh_from_db()

        # ── Dispatch Celery task ──────────────────────────────────────────
        order_id_str = str(order.id)

        def _dispatch():
            try:
                submit_order_to_provider.delay(order_id_str, comments)
            except Exception as exc:  # Redis/broker unavailable (e.g. local dev)
                logger.warning(
                    'Celery broker unavailable — order %s queued but not dispatched: %s',
                    order_id_str, exc
                )

        db_transaction.on_commit(_dispatch)

        return Response({'order': order.reseller_order_id}, status=status.HTTP_201_CREATED)

    # ─────────────────────────────────────────────────────────────────────────
    # action=status
    # ─────────────────────────────────────────────────────────────────────────

    def _action_status(self, request):
        """
        Get the status of an order by reseller_order_id.

        Request params:
            order  <int>  — The reseller_order_id returned by action=add

        Response:
            {
                "charge": "1.50",
                "start_count": "1500",
                "status": "In progress",
                "remains": "700",
                "currency": "NGN"
            }
        """
        order_id_raw = request.data.get('order')
        if not order_id_raw:
            return _error('Missing required parameter: order')

        try:
            reseller_order_id = int(order_id_raw)
        except (TypeError, ValueError):
            return _error('Parameter "order" must be an integer')

        try:
            order = Order.objects.select_related('service', 'provider').get(
                reseller_order_id=reseller_order_id,
                user=request.user,
            )
        except Order.DoesNotExist:
            return _error('Order not found')

        # Refresh from provider if active
        if order.provider_order_id and order.provider and order.status in [
            Order.Status.PENDING, Order.Status.PROCESSING, Order.Status.IN_PROGRESS,
        ]:
            try:
                client = get_provider_client(order.provider)
                result = client.get_order_status(
                    order.provider_order_id,
                    user=request.user,
                    order=order,
                )
                if 'status' in result:
                    _update_order_status(order, result)
            except SMMProviderError:
                pass  # Return cached status on provider error

        # Map internal status to SMM Panel v2 display strings
        STATUS_MAP = {
            Order.Status.PENDING: 'Pending',
            Order.Status.PROCESSING: 'Processing',
            Order.Status.IN_PROGRESS: 'In progress',
            Order.Status.COMPLETED: 'Completed',
            Order.Status.PARTIAL: 'Partial',
            Order.Status.CANCELED: 'Canceled',
            Order.Status.REFUNDED: 'Refunded',
            Order.Status.FAILED: 'Failed',
        }

        return Response({
            'charge': str(order.charge),
            'start_count': str(order.start_count) if order.start_count is not None else '0',
            'status': STATUS_MAP.get(order.status, order.status.capitalize()),
            'remains': str(order.remains) if order.remains is not None else '0',
            'currency': order.currency,
        })

    # ─────────────────────────────────────────────────────────────────────────
    # action=balance
    # ─────────────────────────────────────────────────────────────────────────

    def _action_balance(self, request):
        """
        Return the authenticated user's wallet balance.

        Response:
            {"balance": "5000.00", "currency": "NGN"}
        """
        wallet = request.user.wallet
        return Response({
            'balance': f'{wallet.balance:.2f}',
            'currency': wallet.currency,
        })

    # ─────────────────────────────────────────────────────────────────────────
    # action=refill
    # ─────────────────────────────────────────────────────────────────────────

    def _action_refill(self, request):
        """
        Request a refill for a completed order.

        Request params:
            order  <int>  — The reseller_order_id

        Response (success):
            {"refill": <refill_id>}
        """
        order_id_raw = request.data.get('order')
        if not order_id_raw:
            return _error('Missing required parameter: order')

        try:
            reseller_order_id = int(order_id_raw)
        except (TypeError, ValueError):
            return _error('Parameter "order" must be an integer')

        try:
            order = Order.objects.select_related('service', 'provider').get(
                reseller_order_id=reseller_order_id,
                user=request.user,
            )
        except Order.DoesNotExist:
            return _error('Order not found')

        if not order.service or not order.service.has_refill:
            return _error('This service does not support refills')

        if order.status != Order.Status.COMPLETED:
            return _error('Order must be completed before requesting a refill')

        if not order.provider:
            return _error('No provider configured for this order')

        try:
            client = get_provider_client(order.provider)
            result = client.create_refill(
                order_id=order.provider_order_id,
                user=request.user,
                order=order,
            )
        except SMMProviderError as e:
            return _error(str(e), 502)

        if 'refill' in result:
            return Response({'refill': result['refill']})
        elif 'error' in result:
            return _error(str(result['error']))
        else:
            return _error('Refill request failed. Please try again.', 502)


# ─────────────────────────────────────────────────────────────────────────────
# Helper: update order status from provider response
# ─────────────────────────────────────────────────────────────────────────────

def _update_order_status(order: Order, status_result: dict):
    """Update an Order instance from a provider status response dict."""
    STATUS_MAP = {
        'pending': Order.Status.PENDING,
        'processing': Order.Status.PROCESSING,
        'in progress': Order.Status.IN_PROGRESS,
        'completed': Order.Status.COMPLETED,
        'partial': Order.Status.PARTIAL,
        'canceled': Order.Status.CANCELED,
        'cancelled': Order.Status.CANCELED,
        'refunded': Order.Status.REFUNDED,
    }

    provider_status = status_result.get('status', '').lower()
    if provider_status in STATUS_MAP:
        order.status = STATUS_MAP[provider_status]

    if 'start_count' in status_result:
        order.start_count = int(status_result['start_count']) if status_result['start_count'] else None
    if 'remains' in status_result:
        order.remains = int(status_result['remains']) if status_result['remains'] else None

    if order.status == Order.Status.COMPLETED:
        order.completed_at = timezone.now()

    order.save()
