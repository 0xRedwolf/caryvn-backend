"""
Celery tasks for Caryvn.
"""
import logging
from celery import shared_task

logger = logging.getLogger(__name__)


@shared_task(
    name='core.tasks.submit_order_to_provider',
    bind=True,
    max_retries=2,
    default_retry_delay=10,  # seconds between retries
)
def submit_order_to_provider(self, order_id: str, comments=None):
    """
    Submit a freshly-created order to the SMM provider.

    Runs in the Celery worker so the HTTP response is returned to the user
    immediately (sub-100 ms) rather than waiting for the external API call.

    On success  → order.status = PROCESSING, confirmation email sent.
    On failure  → wallet is auto-refunded, order.status = FAILED.
    On task error (e.g. Redis down) → retried up to max_retries times.
    """
    from django.db import transaction as db_transaction
    from core.models import Order, Wallet
    from core.services.smm_provider import get_provider_client, SMMProviderError
    from core.services.email_service import email_service

    try:
        order = Order.objects.select_related('user', 'service', 'provider').get(id=order_id)
    except Order.DoesNotExist:
        logger.error(f'submit_order_to_provider: Order {order_id} not found — skipping.')
        return

    # Guard: only process truly pending orders (avoid re-processing on replay/retry)
    if order.status != Order.Status.PENDING:
        logger.info(f'submit_order_to_provider: Order {order_id} already in status {order.status} — skipping.')
        return

    wallet = Wallet.objects.get(user=order.user)

    provider_error = None
    try:
        if not order.provider:
            provider_error = 'No provider configured for this service'
        else:
            client = get_provider_client(order.provider)
            result = client.create_order(
                service_id=order.service.external_id,
                link=order.link,
                quantity=order.quantity,
                comments=comments,
                user=order.user,
                order=order,
            )

            if 'order' in result:
                order.provider_order_id = str(result['order'])
                order.status = Order.Status.PROCESSING
                order.save(update_fields=['provider_order_id', 'status'])
            elif 'error' in result:
                provider_error = str(result['error'])

    except SMMProviderError as exc:
        provider_error = str(exc)
    except Exception as exc:
        # Unexpected error — retry the task
        logger.exception(f'submit_order_to_provider: Unexpected error for order {order_id}: {exc}')
        raise self.retry(exc=exc)

    if provider_error:
        # Auto-refund: wallet charge is reversed, order marked failed
        with db_transaction.atomic():
            wallet.refund(order.charge, f'Refund - provider failed: Order #{str(order.id)[:8]}')
            order.status = Order.Status.FAILED
            order.save(update_fields=['status'])
        logger.error(f'Order {order_id} failed → auto-refunded ₦{order.charge}: {provider_error}')
        return

    # Success — send confirmation email (non-critical, never blocks)
    try:
        email_service.send_order_confirmation(order.user, order)
    except Exception as e:
        logger.warning(f'Order confirmation email failed (non-critical): {e}')

    logger.info(f'Order {order_id} submitted to provider successfully.')


@shared_task(name='core.tasks.retry_stuck_orders_task')
def retry_stuck_orders_task():
    """
    Safety net: find PENDING orders that were never submitted to the provider
    (no provider_order_id) and are older than 2 minutes, then submit them now.

    This catches orders where the on_commit Celery dispatch silently failed
    (e.g. Redis blip, worker restart during deploy, etc.).
    """
    from django.utils import timezone
    from datetime import timedelta
    from core.models import Order

    cutoff = timezone.now() - timedelta(minutes=2)
    stuck_orders = Order.objects.filter(
        status=Order.Status.PENDING,
        provider_order_id__isnull=True,
        created_at__lte=cutoff,
    ).exclude(provider_order_id='').values_list('id', flat=True)

    count = 0
    for order_id in stuck_orders:
        submit_order_to_provider.delay(str(order_id))
        count += 1

    if count:
        logger.info(f'retry_stuck_orders_task: re-queued {count} stuck pending orders.')
    return {'requeued': count}


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
    """Sync services from all active providers every 30 minutes."""
    from core.models import Provider
    from core.services.smm_provider import get_provider_client, SMMProviderError
    from core.services.pricing import pricing_service

    logger.info('Starting automatic service sync for all providers...')
    results = {}

    for provider in Provider.objects.filter(is_active=True):
        try:
            client = get_provider_client(provider)
            services = client.get_services(force_refresh=True)

            if not services:
                results[provider.slug] = {'count': 0, 'status': 'skipped', 'reason': 'provider returned empty service list'}
                logger.warning(f'Service sync skipped for {provider.name}: provider returned an empty list. Skipping to avoid wiping active services.')
                continue

            count = pricing_service.sync_service_prices(services, provider=provider)
            results[provider.slug] = {'count': count, 'status': 'success'}
            logger.info(f'Service sync for {provider.name}: {count} services synced')
        except SMMProviderError as e:
            results[provider.slug] = {'error': str(e), 'status': 'failed'}
            logger.error(f'Service sync failed for {provider.name}: {e}')

    return results
