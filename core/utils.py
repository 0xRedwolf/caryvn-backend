from django.utils import timezone
from core.models import Order, Provider
from core.services.smm_provider import get_provider_client, SMMProviderError
import logging
import time

logger = logging.getLogger(__name__)

def sync_active_orders(provider_slug=None):
    """
    Syncs all pending/processing/in_progress orders with their respective SMM providers.
    Optionally scoped to a single provider by slug.
    Returns a dict with updated count and error count.
    """
    orders = Order.objects.filter(
        provider_order_id__isnull=False,
        status__in=[
            Order.Status.PENDING,
            Order.Status.PROCESSING,
            Order.Status.IN_PROGRESS
        ]
    ).exclude(provider_order_id='').select_related('provider')
    
    # Optionally filter by provider
    if provider_slug:
        orders = orders.filter(provider__slug=provider_slug)
    
    updated = 0
    errors = 0
    
    status_map = {
        'pending': Order.Status.PENDING,
        'processing': Order.Status.PROCESSING,
        'in progress': Order.Status.IN_PROGRESS,
        'completed': Order.Status.COMPLETED,
        'partial': Order.Status.PARTIAL,
        'canceled': Order.Status.CANCELED,
        'cancelled': Order.Status.CANCELED,
        'refunded': Order.Status.REFUNDED,
        'failed': Order.Status.FAILED,
    }

    # Cache provider clients to avoid recreating for each order
    _client_cache = {}

    for order in orders:
        try:
            # Get or create client for this order's provider
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
                provider_status = result['status'].lower()
                new_status = status_map.get(provider_status)
                
                if new_status and order.status != new_status:
                    order.status = new_status
                    
                    if 'remains' in result and result['remains']:
                        order.remains = int(result['remains'])
                    if 'start_count' in result and result['start_count']:
                        order.start_count = int(result['start_count'])
                        
                    if new_status == Order.Status.COMPLETED:
                        order.completed_at = timezone.now()
                        
                    order.save()
                    updated += 1
                else:
                    if 'remains' in result and result['remains']:
                        remains = int(result['remains'])
                        if order.remains != remains:
                            order.remains = remains
                            order.save(update_fields=['remains'])
        
        except Exception as e:
            logger.error(f'Failed to sync order {order.id}: {e}', exc_info=True)
            errors += 1
            
    return {'updated': updated, 'errors': errors}
