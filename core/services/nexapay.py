"""
NexaPay Payment Gateway integration service for Caryvn.
Handles virtual account creation, webhook HMAC-SHA256 signature validation, and transaction requery.
"""
import hashlib
import hmac
import json
import logging
import uuid
import requests
from decimal import Decimal
from django.conf import settings

logger = logging.getLogger(__name__)

DEFAULT_NEXAPAY_BASE_URL = 'https://api.nexapay.ng/api/v1/business'


class NexaPayPaymentError(Exception):
    """Exception raised for NexaPay payment errors."""
    pass


class NexaPayPaymentService:
    """Service for interacting with the NexaPay Merchant API."""

    def __init__(self):
        self.base_url = getattr(settings, 'NEXAPAY_BASE_URL', DEFAULT_NEXAPAY_BASE_URL).rstrip('/')
        self.api_key = getattr(settings, 'NEXAPAY_API_KEY', '')
        self.business_id = getattr(settings, 'NEXAPAY_BUSINESS_ID', '')
        self.webhook_secret = getattr(settings, 'NEXAPAY_WEBHOOK_SECRET', '')

    def _get_headers(self):
        return {
            'x-api-key': self.api_key,
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }

    def generate_reference(self):
        """Generate a unique transaction reference with NXP prefix."""
        short_uuid = uuid.uuid4().hex[:12].upper()
        return f'NXP-{short_uuid}'

    def create_virtual_account(
        self,
        amount_naira: Decimal | float | int,
        transaction_ref: str,
        customer_id: str,
        customer_name: str = '',
        customer_email: str = '',
        customer_phone: str = '',
        validity_time_minutes: int = 30
    ) -> dict:
        """
        Create a dynamic virtual bank account via NexaPay for bank transfer collection.

        Args:
            amount_naira: The exact collection amount in NGN
            transaction_ref: Unique transaction reference (CRV/NXP-...)
            customer_id: Internal user ID
            customer_name: Customer full name
            customer_email: Customer email
            customer_phone: Customer phone (optional)
            validity_time_minutes: Expiration in minutes (default 30)

        Returns:
            dict containing:
                - bank_name
                - account_number
                - account_name
                - amount
                - reference
                - expires_at
        """
        if not self.api_key:
            raise NexaPayPaymentError('NexaPay API key is not configured.')
        if not self.business_id:
            raise NexaPayPaymentError('NexaPay Business ID is not configured.')

        amount_val = float(amount_naira)

        payload = {
            'businessId': self.business_id,
            'amount': amount_val,
            'reference': transaction_ref,
            'merchantCustomerId': str(customer_id),
            'merchantReference': transaction_ref,
            'validityTime': validity_time_minutes,
            'amountValidation': 'strict',
            'customerName': customer_name or 'Valued Customer',
            'customerEmail': customer_email or '',
            'metadata': {
                'purpose': 'wallet_topup',
                'customer_id': str(customer_id),
                'transaction_ref': transaction_ref,
            }
        }
        if customer_phone:
            payload['customerPhone'] = customer_phone

        url = f'{self.base_url}/virtual-account/create'
        logger.info(f'NexaPay create_virtual_account: ref={transaction_ref}, amount={amount_val}, url={url}')

        try:
            response = requests.post(
                url,
                json=payload,
                headers=self._get_headers(),
                timeout=30,
            )

            try:
                data = response.json()
            except Exception:
                logger.error(f'NexaPay non-JSON response: status={response.status_code}, text={response.text[:200]}')
                raise NexaPayPaymentError(f'NexaPay returned invalid response (status {response.status_code})')

            logger.info(f'NexaPay create response: status={response.status_code}, ref={transaction_ref}, body={data}')

            if response.status_code in (200, 201):
                # NexaPay may return fields at root level or nested in 'virtualAccount' or 'data'
                va = data.get('virtualAccount') if isinstance(data.get('virtualAccount'), dict) else {}
                sub = data.get('data') if isinstance(data.get('data'), dict) else {}

                account_number = (
                    data.get('accountNumber') or
                    data.get('account_number') or
                    va.get('accountNumber') or
                    va.get('account_number') or
                    sub.get('accountNumber') or
                    sub.get('account_number') or
                    data.get('virtualAccountNumber') or
                    va.get('virtualAccountNumber') or
                    sub.get('virtualAccountNumber') or ''
                )
                bank_name = (
                    data.get('bankName') or
                    data.get('bank_name') or
                    va.get('bankName') or
                    va.get('bank_name') or
                    sub.get('bankName') or
                    sub.get('bank_name') or
                    data.get('bank') or
                    va.get('bank') or
                    'VFD Microfinance Bank'
                )
                account_name = (
                    data.get('accountName') or
                    data.get('account_name') or
                    va.get('accountName') or
                    va.get('account_name') or
                    data.get('customerName') or
                    va.get('customerName') or
                    customer_name or
                    'Caryvn Services'
                )
                expires_at = (
                    data.get('expiresAt') or
                    data.get('expires_at') or
                    va.get('expiresAt') or
                    va.get('expires_at') or
                    va.get('validityTime') or ''
                )

                if not account_number:
                    err = data.get('msg') or data.get('message') or data.get('error') or 'Virtual account number was not returned'
                    raise NexaPayPaymentError(f'NexaPay generation error: {err}')

                return {
                    'account_number': str(account_number),
                    'bank_name': str(bank_name),
                    'account_name': str(account_name),
                    'amount': amount_val,
                    'reference': transaction_ref,
                    'expires_at': str(expires_at),
                    'raw_response': data,
                }
            else:
                error_msg = (
                    data.get('message') or
                    data.get('error') or
                    data.get('msg') or
                    f'Status {response.status_code}'
                )
                raise NexaPayPaymentError(f'NexaPay create failed: {error_msg}')

        except requests.RequestException as e:
            logger.error(f'NexaPay request failed: {e}')
            raise NexaPayPaymentError(f'Failed to connect to NexaPay: {str(e)}')

    def requery_virtual_account(self, merchant_reference: str) -> dict:
        """
        Requery a virtual account status by merchant reference.
        Used by admin to manually check or verify stuck deposits.
        """
        if not self.api_key or not self.business_id:
            raise NexaPayPaymentError('NexaPay credentials not configured')

        url = f'{self.base_url}/virtual-account/history?businessId={self.business_id}'
        try:
            response = requests.get(
                url,
                headers=self._get_headers(),
                timeout=30,
            )
            data = response.json()
            if response.status_code == 200:
                accounts = data.get('data') or data.get('virtualAccounts') or []
                if isinstance(accounts, list):
                    for acc in accounts:
                        if (
                            acc.get('merchantReference') == merchant_reference or
                            acc.get('reference') == merchant_reference
                        ):
                            return {
                                'found': True,
                                'status': acc.get('status'),
                                'account_number': acc.get('accountNumber'),
                                'expires_at': acc.get('expiresAt'),
                                'data': acc,
                            }
                return {'found': False, 'status': 'NOT_FOUND'}
            return {'found': False, 'error': data.get('message', 'Failed to fetch history')}
        except Exception as e:
            logger.error(f'NexaPay requery failed: {e}')
            raise NexaPayPaymentError(f'Requery failed: {str(e)}')

    @staticmethod
    def validate_webhook_signature(payload_body: bytes, signature: str, timestamp: str, secret_key: str) -> bool:
        """
        Validate the HMAC-SHA256 signature from NexaPay.

        NexaPay sends headers:
        - x-nexapay-signature
        - x-nexapay-timestamp

        Signature is HMAC SHA-256 computed over the raw payload and timestamp.
        We test standard variants (timestamp.body, body+timestamp, and body) to ensure robust verification.
        """
        if not signature or not secret_key:
            return False

        secret_bytes = secret_key.encode('utf-8')
        sig_lower = signature.strip().lower()

        # Variant 1: timestamp.body (standard modern webhook scheme)
        if timestamp:
            signed_payload_1 = f'{timestamp}.'.encode('utf-8') + payload_body
            digest_1 = hmac.new(secret_bytes, signed_payload_1, hashlib.sha256).hexdigest().lower()
            if hmac.compare_digest(digest_1, sig_lower):
                return True

            # Variant 2: body + timestamp
            signed_payload_2 = payload_body + timestamp.encode('utf-8')
            digest_2 = hmac.new(secret_bytes, signed_payload_2, hashlib.sha256).hexdigest().lower()
            if hmac.compare_digest(digest_2, sig_lower):
                return True

        # Variant 3: payload_body alone
        digest_3 = hmac.new(secret_bytes, payload_body, hashlib.sha256).hexdigest().lower()
        if hmac.compare_digest(digest_3, sig_lower):
            return True

        return False


# Singleton instance
nexapay_service = NexaPayPaymentService()
