"""
Celery tasks for Caryvn.
"""
import logging
from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(name='core.tasks.sync_orders_task')
def sync_orders_task():
    """Sync all active orders with their respective providers every 30 minutes."""
    from core.utils import sync_active_orders
    
    logger.info('Starting automatic order sync...')
    result = sync_active_orders()
    logger.info(f'Order sync complete: {result}')
    return result


@shared_task(name='core.tasks.sync_services_task')
def sync_services_task():
    """Sync services from all active providers every 6 hours."""
    from core.models import Provider
    from core.services.smm_provider import get_provider_client, SMMProviderError
    from core.services.pricing import pricing_service
    
    logger.info('Starting automatic service sync for all providers...')
    results = {}
    
    for provider in Provider.objects.filter(is_active=True):
        try:
            client = get_provider_client(provider)
            services = client.get_services(force_refresh=True)
            count = pricing_service.sync_service_prices(services, provider=provider)
            results[provider.slug] = {'count': count, 'status': 'success'}
            logger.info(f'Service sync for {provider.name}: {count} services synced')
        except SMMProviderError as e:
            results[provider.slug] = {'error': str(e), 'status': 'failed'}
            logger.error(f'Service sync failed for {provider.name}: {e}')
    
    return results
