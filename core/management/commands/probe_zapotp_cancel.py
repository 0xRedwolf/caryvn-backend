import requests
from django.core.management.base import BaseCommand
from core.models import OTPOrder, OTPProviderSetting
from core.services.zapotp import ZapOTPClient


class Command(BaseCommand):
    help = 'Probes ZapOTP API to identify the exact cancellation endpoint / payload'

    def add_arguments(self, parser):
        parser.add_argument('--order-id', type=str, help='ZapOTP provider order ID to test cancel on')
        parser.add_argument('--phone', type=str, help='Phone number to look up order')

    def handle(self, *args, **options):
        setting = OTPProviderSetting.get_settings()
        api_key = setting.api_key
        base_url = (setting.base_url or 'https://zapotp.com/account/api/v1').rstrip('/')

        if not api_key:
            self.stderr.write("ZapOTP API Key is not configured.")
            return

        order_id = options.get('order_id')
        phone = options.get('phone')

        target_order = None
        if order_id:
            target_order = OTPOrder.objects.filter(provider_order_id=order_id).first()
        elif phone:
            target_order = OTPOrder.objects.filter(phone_number__icontains=phone).first()
        else:
            # Pick latest order (pending, canceled, or any)
            target_order = OTPOrder.objects.order_by('-created_at').first()

        if not target_order and not order_id:
            self.stderr.write("No OTP order found in database to probe.")
            return

        provider_id = order_id or (target_order.provider_order_id if target_order else None)
        phone_display = target_order.phone_number if target_order else 'Unknown'

        self.stdout.write(f"Target ZapOTP Provider Order ID: {provider_id} (Phone: {phone_display})")
        self.stdout.write(f"Base URL: {base_url}\n")

        headers = {
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
        }

        # Step 1: Check initial status
        self.stdout.write("--- Step 1: Query current status on ZapOTP ---")
        try:
            r = requests.get(f"{base_url}/sms", params={"order_id": provider_id}, headers=headers, timeout=10)
            self.stdout.write(f"GET /sms?order_id={provider_id} -> HTTP {r.status_code}: {r.text}\n")
        except Exception as e:
            self.stdout.write(f"Error checking initial status: {e}\n")

        # Step 2: Test candidate cancel calls
        candidates = [
            ("POST", f"{base_url}/rent", None, {"action": "cancel", "order_id": str(provider_id)}),
            ("POST", f"{base_url}/rent", None, {"action": "cancel", "id": str(provider_id)}),
            ("POST", f"{base_url}/rent", None, {"action": "cancel", "order_id": str(provider_id), "provider": "global", "service": "generic"}),
            ("POST", f"{base_url}/sms", None, {"action": "cancel", "order_id": str(provider_id)}),
            ("POST", f"{base_url}/sms", None, {"order_id": str(provider_id), "status": "CANCELED"}),
            ("GET", f"{base_url}/sms", {"order_id": str(provider_id), "action": "cancel"}, None),
            ("GET", f"{base_url}/sms", {"order_id": str(provider_id), "status": "cancel"}, None),
            ("GET", f"{base_url}/sms", {"order_id": str(provider_id), "status": "CANCELED"}, None),
            ("GET", f"{base_url}/sms", {"order_id": str(provider_id), "cancel": "1"}, None),
            ("POST", f"{base_url}/cancel", None, {"order_id": str(provider_id)}),
            ("GET", f"{base_url}/cancel", {"order_id": str(provider_id)}, None),
            ("POST", f"{base_url}/orders/cancel", None, {"order_id": str(provider_id)}),
            ("DELETE", f"{base_url}/sms", {"order_id": str(provider_id)}, None),
            ("DELETE", f"{base_url}/orders", {"order_id": str(provider_id)}, None),
        ]

        self.stdout.write("--- Step 2: Probing candidate cancel endpoints ---")
        for method, url, params, json_body in candidates:
            label = f"{method} {url}"
            if params:
                label += f"?{params}"
            if json_body:
                label += f" JSON={json_body}"

            try:
                if method == "GET":
                    resp = requests.get(url, params=params, headers=headers, timeout=8)
                elif method == "POST":
                    resp = requests.post(url, json=json_body, headers=headers, timeout=8)
                elif method == "DELETE":
                    resp = requests.delete(url, params=params, json=json_body, headers=headers, timeout=8)
                else:
                    continue

                self.stdout.write(f"[{method}] {url} -> HTTP {resp.status_code}: {resp.text[:200]}")
            except Exception as ex:
                self.stdout.write(f"[{method}] {url} -> ERROR: {ex}")

        # Step 3: Check status after probes
        self.stdout.write("\n--- Step 3: Final status check on ZapOTP ---")
        try:
            r2 = requests.get(f"{base_url}/sms", params={"order_id": provider_id}, headers=headers, timeout=10)
            self.stdout.write(f"GET /sms?order_id={provider_id} -> HTTP {r2.status_code}: {r2.text}\n")
        except Exception as e:
            self.stdout.write(f"Error checking final status: {e}\n")
