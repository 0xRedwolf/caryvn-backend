from django.utils import timezone
from core.models import Order, Provider
from core.services.smm_provider import get_provider_client, SMMProviderError
from core.services.order_reconciliation import apply_order_status_sync, cancel_dead_pending_orders
import logging

logger = logging.getLogger(__name__)

def sync_active_orders(provider_slug=None):
    """
    Syncs all pending/processing/in_progress orders with their respective SMM providers.
    Handles automatic partial refunds, upstream cancellation refunds, and dead order cleanup.
    Optionally scoped to a single provider by slug.
    Returns a dict with updated count, error count, and cleaned dead order count.
    """
    orders = Order.objects.filter(
        provider_order_id__isnull=False,
        status__in=[
            Order.Status.PENDING,
            Order.Status.PROCESSING,
            Order.Status.IN_PROGRESS
        ]
    ).exclude(provider_order_id='').select_related('provider', 'user', 'user__wallet')
    
    # Optionally filter by provider
    if provider_slug:
        orders = orders.filter(provider__slug=provider_slug)
    
    updated = 0
    errors = 0

    # Cache provider clients to avoid recreating for each order
    _client_cache = {}

    for order in orders:
        try:
            provider = order.provider
            if not provider:
                errors += 1
                continue
            
            if provider.pk not in _client_cache:
                _client_cache[provider.pk] = get_provider_client(provider)
            client = _client_cache[provider.pk]
            
            result = client.get_order_status(
                order.provider_order_id, user=order.user, order=order
            )
            
            if 'status' in result:
                old_status = order.status
                old_remains = order.remains
                apply_order_status_sync(order, result)
                if order.status != old_status or order.remains != old_remains:
                    updated += 1
        
        except Exception as e:
            logger.error(f'Failed to sync order {order.id}: {e}', exc_info=True)
            errors += 1

    # Safety Watchdog: clean up any dead pending orders (>15m unsubmitted)
    dead_summary = {'canceled': 0}
    try:
        dead_summary = cancel_dead_pending_orders(max_age_minutes=15)
    except Exception as w_err:
        logger.error(f'Failed running dead order watchdog: {w_err}', exc_info=True)

    return {
        'updated': updated,
        'errors': errors,
        'dead_orders_cleaned': dead_summary.get('canceled', 0),
    }

