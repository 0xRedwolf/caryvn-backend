"""
Squad Payment Gateway integration service for Caryvn.
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


class SquadPaymentError(Exception):
    """Exception raised for Squad payment errors."""
    pass


class SquadPaymentService:
    """Service for interacting with the Squad Payment Gateway API."""

    def __init__(self):
        self.base_url = getattr(settings, 'SQUAD_BASE_URL', 'https://sandbox-api-d.squadco.com')
        self.secret_key = getattr(settings, 'SQUAD_SECRET_KEY', '')

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
        Initiate a payment with Squad.
        
        Args:
            email: Customer email address
            amount_naira: Amount in Naira (will be converted to kobo)
            transaction_ref: Unique transaction reference
            callback_url: URL to redirect after payment
            customer_name: Customer name (optional)
        
        Returns:
            dict with checkout_url and transaction_ref
        """
        amount_kobo = int(float(amount_naira) * 100)

        payload = {
            'email': email,
            'amount': amount_kobo,
            'currency': 'NGN',
            'initiate_type': 'inline',
            'transaction_ref': transaction_ref,
            'callback_url': callback_url,
        }

        if customer_name:
            payload['customer_name'] = customer_name

        try:
            response = requests.post(
                f'{self.base_url}/transaction/initiate',
                json=payload,
                headers=self._get_headers(),
                timeout=30,
            )

            data = response.json()
            logger.info(f'Squad initiate response: status={response.status_code}, data={data}')

            if response.status_code == 200 and data.get('status') == 200:
                checkout_url = data.get('data', {}).get('checkout_url', '')
                if not checkout_url:
                    raise SquadPaymentError('No checkout URL returned from Squad')
                return {
                    'checkout_url': checkout_url,
                    'transaction_ref': transaction_ref,
                }
            else:
                error_msg = data.get('message', 'Unknown error from Squad')
                raise SquadPaymentError(f'Squad initiate failed: {error_msg}')

        except requests.RequestException as e:
            logger.error(f'Squad API request failed: {e}')
            raise SquadPaymentError(f'Failed to connect to Squad: {str(e)}')

    def verify_payment(self, transaction_ref):
        """
        Verify a payment transaction with Squad.
        
        Args:
            transaction_ref: The transaction reference to verify
        
        Returns:
            dict with transaction details including status and amount
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
                # Squad sometimes returns 500 internal server error HTML
                logger.error(f'Squad verify response not JSON: status={response.status_code}, content={response.text[:200]}')
                raise SquadPaymentError(f'Squad returned invalid response (Status {response.status_code}): {response.text[:100]}')

            logger.info(f'Squad verify response: status={response.status_code}, ref={transaction_ref}')

            if response.status_code == 200 and data.get('status') == 200:
                tx_data = data.get('data', {})
                return {
                    'success': tx_data.get('transaction_status', '').lower() == 'success',
                    'amount_kobo': tx_data.get('transaction_amount', 0),
                    'amount_naira': tx_data.get('transaction_amount', 0) / 100,
                    'reference': tx_data.get('transaction_ref', ''),
                    'gateway_ref': tx_data.get('gateway_ref', ''),
                    'status': tx_data.get('transaction_status', ''),
                }
            else:
                error_msg = data.get('message', 'Verification failed')
                raise SquadPaymentError(f'Squad verify failed: {error_msg}')

        except requests.RequestException as e:
            logger.error(f'Squad verify request failed: {e}')
            raise SquadPaymentError(f'Failed to verify with Squad: {str(e)}')

    @staticmethod
    def validate_webhook_signature(payload_body, signature, secret_key):
        """
        Validate the webhook signature from Squad.
        
        Squad sends x-squad-encrypted-body header which is
        HMAC-SHA512 of the request body using the secret key.
        
        Args:
            payload_body: Raw request body bytes
            signature: The x-squad-encrypted-body header value
            secret_key: Your Squad secret key
        
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
squad_service = SquadPaymentService()
