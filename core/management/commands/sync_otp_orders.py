import logging
from django.core.management.base import BaseCommand
from django.utils import timezone
from core.models import OTPOrder
from core.services.zapotp import ZapOTPClient, ZapOTPError

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Polls ZapOTP upstream and synchronizes pending virtual number OTP orders'

    def handle(self, *args, **options):
        self.stdout.write("Checking for pending OTP orders to synchronize with ZapOTP...")

        pending_orders = OTPOrder.objects.filter(
            status=OTPOrder.Status.PENDING
        ).order_by('-created_at')

        total = pending_orders.count()
        if total == 0:
            self.stdout.write(self.style.SUCCESS("No pending OTP orders found."))
            return

        self.stdout.write(f"Found {total} pending OTP order(s). Polling ZapOTP...")

        client = ZapOTPClient()
        received_count = 0
        canceled_count = 0
        still_pending_count = 0
        error_count = 0

        for order in pending_orders:
            try:
                sms_data = client.get_sms(order.provider_order_id)
                upstream_status = str(sms_data.get('status', 'PENDING')).upper().strip()
                sms_code = sms_data.get('sms_code')
                full_sms = str(sms_data.get('full_sms', '')).strip()

                if (upstream_status in ['RECEIVED', 'FINISHED', 'SUCCESS', 'COMPLETED'] and (sms_code or full_sms)) or (sms_code and len(str(sms_code).strip()) >= 3):
                    order.status = OTPOrder.Status.RECEIVED
                    order.sms_code = str(sms_code).strip() if sms_code else (full_sms[:50] if full_sms else 'DELIVERED')
                    order.full_sms = full_sms or str(sms_code or '')
                    order.received_at = timezone.now()
                    order.save(update_fields=['status', 'sms_code', 'full_sms', 'received_at', 'updated_at'])
                    received_count += 1
                    self.stdout.write(self.style.SUCCESS(
                        f"Order #{str(order.id)[:8]} ({order.phone_number}): RECEIVED! Code: '{order.sms_code}'"
                    ))
                elif upstream_status in ['CANCELED', 'CANCELLED'] and not sms_code:
                    order.status = OTPOrder.Status.CANCELED
                    order.refunded_at = timezone.now()
                    order.save(update_fields=['status', 'refunded_at', 'updated_at'])
                    canceled_count += 1
                    self.stdout.write(self.style.WARNING(
                        f"Order #{str(order.id)[:8]} ({order.phone_number}): CANCELED upstream by ZapOTP."
                    ))
                else:
                    still_pending_count += 1
                    self.stdout.write(
                        f"Order #{str(order.id)[:8]} ({order.phone_number}): Still pending waiting for SMS."
                    )
            except ZapOTPError as e:
                error_count += 1
                self.stdout.write(self.style.ERROR(
                    f"Order #{str(order.id)[:8]} ({order.phone_number}): ZapOTP Error: {e}"
                ))
            except Exception as e:
                error_count += 1
                self.stdout.write(self.style.ERROR(
                    f"Order #{str(order.id)[:8]} ({order.phone_number}): Unexpected error: {e}"
                ))

        self.stdout.write(self.style.SUCCESS(
            f"Sync complete. Received: {received_count}, Canceled: {canceled_count}, Pending: {still_pending_count}, Errors: {error_count}"
        ))
