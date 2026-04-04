"""
Paystack Payment Gateway integration service for Caryvn.
Handles payment initiation, verification, and webhook validation.
"""
import hashlib
import hmac
import json
import logging
import uuid
import requests
from django.conf import settings

logger = logging.getLogger(__name__)

PAYSTACK_BASE_URL = 'https://api.paystack.co'


class PaystackPaymentError(Exception):
    """Exception raised for Paystack payment errors."""
    pass


class PaystackPaymentService:
    """Service for interacting with the Paystack Payment Gateway API."""

    def __init__(self):
        self.base_url = PAYSTACK_BASE_URL
        self.secret_key = getattr(settings, 'PAYSTACK_SECRET_KEY', '')

    def _get_headers(self):
        return {
            'Authorization': f'Bearer {self.secret_key}',
            'Content-Type': 'application/json',
        }

    def generate_reference(self):
        """Generate a unique transaction reference."""
        short_uuid = uuid.uuid4().hex[:12].upper()
        return f'CRV-{short_uuid}'

    def initiate_payment(self, email, amount_naira, transaction_ref, callback_url, customer_name=''):
        """
        Initiate a payment with Paystack.

        Args:
            email: Customer email address
            amount_naira: Amount in Naira (will be converted to kobo)
            transaction_ref: Unique transaction reference
            callback_url: URL to redirect after payment
            customer_name: Customer name (optional)

        Returns:
            dict with authorization_url and transaction_ref
        """
        amount_kobo = int(float(amount_naira) * 100)

        payload = {
            'email': email,
            'amount': amount_kobo,
            'currency': 'NGN',
            'reference': transaction_ref,
            'callback_url': callback_url,
        }

        if customer_name:
            payload['metadata'] = {
                'custom_fields': [
                    {
                        'display_name': 'Customer Name',
                        'variable_name': 'customer_name',
                        'value': customer_name,
                    }
                ]
            }

        try:
            response = requests.post(
                f'{self.base_url}/transaction/initialize',
                json=payload,
                headers=self._get_headers(),
                timeout=30,
            )

            data = response.json()
            logger.info(f'Paystack initiate response: status={response.status_code}, ref={transaction_ref}')

            if response.status_code == 200 and data.get('status') is True:
                authorization_url = data.get('data', {}).get('authorization_url', '')
                if not authorization_url:
                    raise PaystackPaymentError('No authorization URL returned from Paystack')
                return {
                    'checkout_url': authorization_url,
                    'transaction_ref': transaction_ref,
                }
            else:
                error_msg = data.get('message', 'Unknown error from Paystack')
                raise PaystackPaymentError(f'Paystack initiate failed: {error_msg}')

        except requests.RequestException as e:
            logger.error(f'Paystack API request failed: {e}')
            raise PaystackPaymentError(f'Failed to connect to Paystack: {str(e)}')

    def verify_payment(self, transaction_ref):
        """
        Verify a payment transaction with Paystack.

        Args:
            transaction_ref: The transaction reference to verify

        Returns:
            dict with transaction details including success flag and amount
        """
        try:
            response = requests.get(
                f'{self.base_url}/transaction/verify/{transaction_ref}',
                headers=self._get_headers(),
                timeout=30,
            )

            try:
                data = response.json()
            except Exception:
                logger.error(
                    f'Paystack verify response not JSON: status={response.status_code}, '
                    f'content={response.text[:200]}'
                )
                raise PaystackPaymentError(
                    f'Paystack returned invalid response (Status {response.status_code}): '
                    f'{response.text[:100]}'
                )

            logger.info(f'Paystack verify response: status={response.status_code}, ref={transaction_ref}')

            if response.status_code == 200 and data.get('status') is True:
                tx_data = data.get('data', {})
                # Paystack returns amount in kobo
                amount_kobo = tx_data.get('amount', 0)
                return {
                    'success': tx_data.get('status', '').lower() == 'success',
                    'amount_kobo': amount_kobo,
                    'amount_naira': amount_kobo / 100,
                    'reference': tx_data.get('reference', ''),
                    'gateway_response': tx_data.get('gateway_response', ''),
                    'status': tx_data.get('status', ''),
                }
            else:
                error_msg = data.get('message', 'Verification failed')
                raise PaystackPaymentError(f'Paystack verify failed: {error_msg}')

        except requests.RequestException as e:
            logger.error(f'Paystack verify request failed: {e}')
            raise PaystackPaymentError(f'Failed to verify with Paystack: {str(e)}')

    @staticmethod
    def validate_webhook_signature(payload_body, signature, secret_key):
        """
        Validate the webhook signature from Paystack.

        Paystack sends x-paystack-signature header which is
        HMAC-SHA512 of the request body using the secret key.

        Args:
            payload_body: Raw request body bytes
            signature: The x-paystack-signature header value
            secret_key: Your Paystack secret key

        Returns:
            bool: True if signature is valid
        """
        if not signature or not secret_key:
            return False

        expected = hmac.HMAC(
            secret_key.encode('utf-8'),
            payload_body,
            hashlib.sha512
        ).hexdigest()

        return hmac.compare_digest(expected, signature)


# Singleton instance
paystack_service = PaystackPaymentService()
