"""
Celery tasks for Caryvn.
"""
import logging
from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name='core.tasks.sync_orders_task')
def sync_orders_task():
    """Sync all active orders with the provider every 30 minutes."""
    from core.utils import sync_active_orders
    
    logger.info('Starting automatic order sync...')
    result = sync_active_orders()
    logger.info(f'Order sync complete: {result}')
    return result


@shared_task(name='core.tasks.sync_services_task')
def sync_services_task():
    """Sync services from provider every 6 hours."""
    from core.services.smm_provider import smm_provider, SMMProviderError
    from core.services.pricing import pricing_service
    
    logger.info('Starting automatic service sync...')
    try:
        services = smm_provider.get_services(force_refresh=True)
        count = pricing_service.sync_service_prices(services)
        logger.info(f'Service sync complete: {count} services synced')
        return {'count': count, 'status': 'success'}
    except SMMProviderError as e:
        logger.error(f'Service sync failed: {e}')
        return {'error': str(e), 'status': 'failed'}
