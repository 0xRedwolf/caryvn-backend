import logging
from decimal import Decimal
from datetime import timedelta
from django.db import transaction as db_transaction
from django.db.models import Q
from django.utils import timezone
from core.models import Order, Transaction

logger = logging.getLogger(__name__)

STATUS_MAP = {
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


def _order_has_refund(order: Order) -> bool:
    """Check if an order has already been credited a refund transaction."""
    short_id = str(order.id)[:8]
    return Transaction.objects.filter(
        wallet=order.user.wallet,
        type=Transaction.Type.REFUND,
        description__icontains=short_id,
    ).exists()


def apply_order_status_sync(order: Order, status_result: dict) -> Order:
    """
    Safely reconcile an Order from an upstream SMM provider status response.

    Handles:
    - Status updates (Pending, Processing, In Progress, Completed, Partial, Canceled, Refunded).
    - Start count and remains tracking.
    - Partial delivery calculations and proportional wallet refund.
    - Upstream cancellation/refund 100% wallet refund.
    - Email notifications with refund breakdown.
    - Idempotency guards to prevent duplicate refunds across concurrent tasks.
    """
    raw_status = status_result.get('status', '').lower().strip()
    new_status = STATUS_MAP.get(raw_status)
    if not new_status:
        return order

    # Parse numeric indicators
    remains_val = None
    if 'remains' in status_result and status_result['remains'] not in (None, ''):
        try:
            remains_val = max(0, int(status_result['remains']))
        except (ValueError, TypeError):
            pass

    start_count_val = None
    if 'start_count' in status_result and status_result['start_count'] not in (None, ''):
        try:
            start_count_val = int(status_result['start_count'])
        except (ValueError, TypeError):
            pass

    # Wrap status change and financial reconciliation in atomic block with row lock
    with db_transaction.atomic():
        locked_order = Order.objects.select_for_update().get(pk=order.pk)

        # Update start count if available
        if start_count_val is not None:
            locked_order.start_count = start_count_val

        # Update remains if available
        if remains_val is not None:
            locked_order.remains = remains_val

        # Only process financial actions if status transitioned or remains changed on partial
        status_changed = locked_order.status != new_status
        locked_order.status = new_status

        refund_issued = Decimal('0')
        email_status_label = None

        # ─── PARTIAL DELIVERY ──────────────────────────────────────────────
        if new_status == Order.Status.PARTIAL:
            email_status_label = 'Partial Delivery'
            remains = locked_order.remains if locked_order.remains is not None else remains_val

            if not _order_has_refund(locked_order):
                if remains is not None and locked_order.quantity > 0:
                    if remains >= locked_order.quantity:
                        # Zero delivered -> full refund
                        refund_issued = locked_order.charge
                        desc = f"Partial delivery refund: Order #{str(locked_order.id)[:8]} (0/{locked_order.quantity} delivered)"
                    elif remains > 0:
                        # Proportional refund for undelivered units
                        ratio = Decimal(str(remains)) / Decimal(str(locked_order.quantity))
                        refund_issued = round(ratio * locked_order.charge, 2)
                        desc = (
                            f"Partial delivery refund: Order #{str(locked_order.id)[:8]} "
                            f"({remains}/{locked_order.quantity} undelivered)"
                        )
                    else:
                        refund_issued = Decimal('0')
                        desc = ''

                    if refund_issued > Decimal('0'):
                        locked_order.user.wallet.refund(refund_issued, description=desc)
                        logger.info(
                            f"Order {locked_order.id} partial refund: credited ₦{refund_issued} to "
                            f"{locked_order.user.email} ({remains}/{locked_order.quantity} remains)."
                        )

                    # Recalculate profit based on actual delivered units
                    delivered = max(0, locked_order.quantity - (remains or 0))
                    rate_to_use = (
                        locked_order.provider_rate_ngn
                        if locked_order.provider_rate_ngn is not None
                        else locked_order.provider_rate
                    )
                    if rate_to_use:
                        provider_cost = (rate_to_use / Decimal('1000')) * Decimal(str(delivered))
                        net_revenue = locked_order.charge - refund_issued
                        locked_order.profit = max(Decimal('0'), net_revenue - provider_cost)

        # ─── UPSTREAM CANCELED / REFUNDED ─────────────────────────────────
        elif new_status in (Order.Status.CANCELED, Order.Status.REFUNDED):
            email_status_label = 'Canceled' if new_status == Order.Status.CANCELED else 'Refunded'

            if not _order_has_refund(locked_order):
                refund_issued = locked_order.charge
                desc = f"Order {email_status_label.lower()} refund: Order #{str(locked_order.id)[:8]}"
                locked_order.user.wallet.refund(refund_issued, description=desc)
                locked_order.profit = Decimal('0')
                logger.info(
                    f"Order {locked_order.id} {new_status}: 100% refunded ₦{refund_issued} to {locked_order.user.email}."
                )

        # ─── COMPLETED ────────────────────────────────────────────────────
        elif new_status == Order.Status.COMPLETED:
            email_status_label = 'Completed'
            if not locked_order.completed_at:
                locked_order.completed_at = timezone.now()

        locked_order.save()

    # Send milestone email notification outside the lock if status changed to terminal/milestone
    if status_changed and email_status_label:
        try:
            from core.services.email_service import email_service
            email_service.send_order_status_email(
                locked_order,
                status_display=email_status_label,
                refund_amount=refund_issued if refund_issued > Decimal('0') else None,
            )
        except Exception as em_err:
            logger.warning(
                f"Failed to send milestone email for order {locked_order.id} ({email_status_label}): {em_err}"
            )

    return locked_order


def cancel_dead_pending_orders(max_age_minutes: int = 15) -> dict:
    """
    Watchdog: find orders stuck in PENDING for longer than `max_age_minutes`
    that have NO provider_order_id (they never reached upstream provider).

    Safely cancels them and refunds 100% back to customer wallet.
    """
    cutoff = timezone.now() - timedelta(minutes=max_age_minutes)
    dead_orders = (
        Order.objects.filter(
            status=Order.Status.PENDING,
            created_at__lte=cutoff,
        )
        .filter(Q(provider_order_id__isnull=True) | Q(provider_order_id=''))
        .select_related('user', 'user__wallet')
    )

    canceled_count = 0
    total_refunded = Decimal('0')
    errors = []

    for order in dead_orders:
        try:
            with db_transaction.atomic():
                locked_order = Order.objects.select_for_update().get(pk=order.pk)
                # Re-verify state under lock
                if (
                    locked_order.status != Order.Status.PENDING
                    or locked_order.provider_order_id
                ):
                    continue

                refund_amount = locked_order.charge
                if not _order_has_refund(locked_order) and refund_amount > Decimal('0'):
                    desc = f"Auto-refund (unsubmitted order timeout): Order #{str(locked_order.id)[:8]}"
                    locked_order.user.wallet.refund(refund_amount, description=desc)
                    total_refunded += refund_amount

                locked_order.status = Order.Status.CANCELED
                locked_order.profit = Decimal('0')
                locked_order.save(update_fields=['status', 'profit', 'status_updated_at'])
                canceled_count += 1

                logger.warning(
                    f"Dead order watchdog: canceled & refunded order {locked_order.id} "
                    f"(charge ₦{refund_amount}, pending > {max_age_minutes}m with no provider ID)."
                )

            # Customer notification
            try:
                from core.services.email_service import email_service
                email_service.send_order_status_email(
                    order,
                    status_display='Canceled & Refunded',
                    refund_amount=refund_amount,
                )
            except Exception as em_err:
                logger.warning(f"Failed to send cancellation email for dead order {order.id}: {em_err}")

        except Exception as err:
            logger.error(f"Error canceling dead order {order.id}: {err}", exc_info=True)
            errors.append({'order_id': str(order.id), 'error': str(err)})

    return {
        'canceled': canceled_count,
        'total_refunded': str(total_refunded),
        'errors': errors,
    }


def stuck_upstream_order_watchdog(
    pending_timeout_hours: int = 1,
    processing_timeout_hours: int = 4
) -> dict:
    """
    Watchdog for orders that reached the upstream provider (has provider_order_id),
    but have been stuck in PENDING or PROCESSING beyond safe timeouts.

    SAFETY RULE:
    1. Attempts to cancel via upstream API (action: 'cancel').
    2. If upstream confirms cancellation: cancels locally & 100% refunds customer.
    3. If upstream rejects or fails: DOES NOT refund. Creates an AdminNotification
       warning the admin to prevent double-spend leaks.
    """
    from core.services.smm_provider import get_provider_client, SMMProviderError
    from core.models import AdminNotification

    now = timezone.now()
    pending_cutoff = now - timedelta(hours=pending_timeout_hours)
    processing_cutoff = now - timedelta(hours=processing_timeout_hours)

    stuck_candidates = (
        Order.objects.filter(
            Q(status=Order.Status.PENDING, created_at__lte=pending_cutoff)
            | Q(status=Order.Status.PROCESSING, created_at__lte=processing_cutoff)
        )
        .exclude(provider_order_id='')
        .filter(provider_order_id__isnull=False)
        .select_related('provider', 'user', 'user__wallet')
    )

    auto_canceled = 0
    flagged_for_review = 0
    errors = []

    for order in stuck_candidates:
        provider = order.provider
        if not provider:
            continue

        try:
            client = get_provider_client(provider)
            cancel_res = client.cancel_order(order.provider_order_id, user=order.user, order=order)

            if cancel_res.get('success'):
                # Upstream approved cancel -> safe to cancel and refund locally!
                with db_transaction.atomic():
                    locked_order = Order.objects.select_for_update().get(pk=order.pk)
                    if locked_order.status in (Order.Status.COMPLETED, Order.Status.CANCELED, Order.Status.REFUNDED):
                        continue

                    refund_amount = locked_order.charge
                    if not _order_has_refund(locked_order) and refund_amount > Decimal('0'):
                        desc = f"Auto-refund (upstream cancel timeout): Order #{str(locked_order.id)[:8]}"
                        locked_order.user.wallet.refund(refund_amount, description=desc)

                    locked_order.status = Order.Status.CANCELED
                    locked_order.profit = Decimal('0')
                    locked_order.save(update_fields=['status', 'profit', 'status_updated_at'])
                    auto_canceled += 1

                try:
                    from core.services.email_service import email_service
                    email_service.send_order_status_email(
                        order,
                        status_display='Canceled & Refunded',
                        refund_amount=refund_amount,
                    )
                except Exception as em_err:
                    logger.warning(f"Failed to send cancellation email for order {order.id}: {em_err}")

                logger.info(
                    f"Watchdog auto-canceled order {order.id} with upstream provider confirmation ({provider.name})."
                )

            else:
                # Upstream rejected or failed cancel -> DO NOT refund, alert admin!
                flagged_for_review += 1
                err_msg = cancel_res.get('error', 'Provider rejected cancellation')
                logger.warning(
                    f"Order {order.id} (provider order {order.provider_order_id}) stuck at {provider.name}, "
                    f"but upstream rejected cancel: '{err_msg}'. Flagging for admin review."
                )

                # Avoid spamming duplicate admin notifications for same order in 24h
                recent_notif = AdminNotification.objects.filter(
                    title__icontains=str(order.id)[:8],
                    created_at__gte=now - timedelta(hours=24)
                ).exists()

                if not recent_notif:
                    try:
                        AdminNotification.objects.create(
                            notification_type=AdminNotification.NotificationType.SYSTEM,
                            severity=AdminNotification.Severity.WARNING,
                            title=f"Slow Upstream Order: #{str(order.id)[:8]}",
                            message=(
                                f"Order #{str(order.id)[:8]} (Provider ID: {order.provider_order_id}) at "
                                f"{provider.name} has been {order.status} for > {pending_timeout_hours}h. "
                                f"Watchdog attempted cancellation, but provider returned: '{err_msg}'. "
                                f"Customer was NOT refunded to prevent double delivery. Please review manually."
                            ),
                            data={
                                'order_id': str(order.id),
                                'provider_id': provider.id,
                                'provider_order_id': order.provider_order_id,
                                'provider_error': err_msg,
                            }
                        )
                    except Exception as n_err:
                        logger.warning(f"Failed to create admin notification: {n_err}")

        except Exception as e:
            logger.error(f"Error in stuck_upstream_order_watchdog for order {order.id}: {e}", exc_info=True)
            errors.append({'order_id': str(order.id), 'error': str(e)})

    return {
        'auto_canceled': auto_canceled,
        'flagged_for_review': flagged_for_review,
        'errors': errors,
    }

