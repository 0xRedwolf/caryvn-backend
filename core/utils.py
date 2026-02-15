from django.utils import timezone
from core.models import Order
from core.services.smm_provider import smm_provider, SMMProviderError
import time

def sync_active_orders():
    """
    Syncs all pending/processing/in_progress orders with the SMM provider.
    Returns a dict with updated count and error count.
    """
    orders = Order.objects.filter(
        provider_order_id__isnull=False,
        status__in=[
            Order.Status.PENDING,
            Order.Status.PROCESSING,
            Order.Status.IN_PROGRESS
        ]
    ).exclude(provider_order_id='')
    
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

    for order in orders:
        try:
            # We skip the sleep here to make the API response faster, 
            # or we keep it small. But for a synchronous API request, 
            # iterating many orders with sleep is bad. 
            # Ideally this should be a background task. 
            # But user asked for a button. 
            # We'll remove the sleep for the view, but maybe keep it for large batches?
            # Let's keep a tiny sleep to be safe, or just rely on requests latency.
            
            result = smm_provider.get_order_status(
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
        
        except Exception:
            errors += 1
            
    return {'updated': updated, 'errors': errors}
