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

    @property
    def current_api_key(self) -> str:
        import os
        key = getattr(settings, 'NEXAPAY_API_KEY', '') or os.environ.get('NEXAPAY_API_KEY', '') or self.api_key
        return str(key).strip()

    @property
    def current_business_id(self) -> str:
        import os
        bid = getattr(settings, 'NEXAPAY_BUSINESS_ID', '') or os.environ.get('NEXAPAY_BUSINESS_ID', '') or self.business_id
        return str(bid).strip()

    def _get_headers(self, auth_mode: str = 'api_key'):
        key = self.current_api_key
        bid = self.current_business_id
        env = 'test' if (key and 'test' in key.lower()) else 'prod'
        headers = {
            'x-api-key': key,
            'x-business-env': env,
            'x-business-id': bid,
            'Content-Type': 'application/json',
            'Accept': 'application/json',
        }
        if auth_mode == 'bearer':
            headers['Authorization'] = f'Bearer {key}'
        elif auth_mode == 'raw':
            headers['Authorization'] = key
        return headers

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
        """
        api_key = self.current_api_key
        business_id = self.current_business_id

        if not api_key:
            raise NexaPayPaymentError('NexaPay API key is not configured.')
        if not business_id:
            raise NexaPayPaymentError('NexaPay Business ID is not configured.')

        amount_val = float(amount_naira)

        payload = {
            'businessId': business_id,
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
                headers=self._get_headers('api_key'),
                timeout=30,
            )

            try:
                data = response.json()
            except Exception:
                logger.error(f'NexaPay non-JSON response: status={response.status_code}, text={response.text[:200]}')
                raise NexaPayPaymentError(f'NexaPay returned invalid response (status {response.status_code})')

            logger.info(f'NexaPay create response: status={response.status_code}, ref={transaction_ref}, body={data}')

            if response.status_code in (200, 201):
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
                    'amount': str(amount_val),
                    'reference': transaction_ref,
                    'expires_at': str(expires_at),
                }

            err_msg = data.get('msg') or data.get('message') or data.get('error') or f'HTTP {response.status_code}'
            raise NexaPayPaymentError(f'NexaPay returned error: {err_msg}')

        except requests.RequestException as e:
            logger.error(f'NexaPay request failed: {e}')
            raise NexaPayPaymentError(f'Failed to connect to NexaPay: {str(e)}')

    def requery_virtual_account(self, merchant_reference: str) -> dict:
        """
        Requery a virtual account status by merchant reference.
        Tests candidate endpoints on both https://api.nexapay.ng/api/v1 and /api/v1/business
        using standard x-api-key authentication.
        """
        api_key = self.current_api_key
        business_id = self.current_business_id

        if not api_key:
            raise NexaPayPaymentError('NexaPay credentials not configured')

        ref_clean = str(merchant_reference).strip()
        headers = self._get_headers('api_key')
        last_error = ''

        # Base URL candidates: documentation states https://api.nexapay.ng/api/v1
        base_candidates = [
            'https://api.nexapay.ng/api/v1',
            self.base_url,
        ]
        # De-duplicate base candidates while preserving order
        seen_bases = set()
        clean_bases = []
        for b in base_candidates:
            b_norm = b.rstrip('/')
            if b_norm not in seen_bases:
                seen_bases.add(b_norm)
                clean_bases.append(b_norm)

        attempts = []

        # Step 1: Query transactions list endpoints
        for base in clean_bases:
            tx_urls = [
                f'{base}/transactions?limit=100',
                f'{base}/transactions?businessId={business_id}&limit=100' if business_id else None,
            ]
            for tx_url in tx_urls:
                if not tx_url:
                    continue
                try:
                    tx_res = requests.get(tx_url, headers=headers, timeout=15)
                    body_snippet = tx_res.text[:300]
                    attempts.append({'url': tx_url, 'status': tx_res.status_code, 'body': body_snippet})
                    logger.info(f"NexaPay attempt: GET {tx_url} -> status={tx_res.status_code}, body={body_snippet}")
                    if tx_res.status_code == 200:
                        tx_json = tx_res.json()
                        tx_list = tx_json.get('transactions') or tx_json.get('data') or []
                        if isinstance(tx_list, list):
                            for tx in tx_list:
                                t_ref = str(
                                    tx.get('reference') or
                                    tx.get('merchantReference') or
                                    tx.get('transactionId') or
                                    tx.get('id') or ''
                                ).strip()
                                t_desc = str(tx.get('description') or '')

                                if t_ref == ref_clean or ref_clean in t_desc or ref_clean in t_ref:
                                    t_status = str(tx.get('status') or '').lower()
                                    is_paid = t_status in ('successful', 'success', 'paid', 'completed', 'credited', 'funded')
                                    logger.info(f"NexaPay transactions ledger match for ref={ref_clean}: id={tx.get('id')}, status={t_status}")
                                    return {
                                        'found': True,
                                        'confirmed': is_paid,
                                        'status': 'successful' if is_paid else t_status.upper(),
                                        'amount': tx.get('amount'),
                                        'transaction_id': tx.get('id'),
                                        'data': tx,
                                        'attempts': attempts,
                                    }
                    else:
                        last_error = tx_res.text
                except Exception as e:
                    logger.warning(f'NexaPay query error on {tx_url}: {e}')
                    attempts.append({'url': tx_url, 'error': str(e)})
                    last_error = str(e)

        # Step 2: Query single transaction details by reference / ID
        for base in clean_bases:
            detail_urls = [
                f'{base}/transactions/{ref_clean}',
                f'{base}/transactions/{ref_clean}?businessId={business_id}' if business_id else None,
            ]
            for detail_url in detail_urls:
                if not detail_url:
                    continue
                try:
                    detail_res = requests.get(detail_url, headers=headers, timeout=15)
                    body_snippet = detail_res.text[:300]
                    attempts.append({'url': detail_url, 'status': detail_res.status_code, 'body': body_snippet})
                    logger.info(f"NexaPay attempt: GET {detail_url} -> status={detail_res.status_code}, body={body_snippet}")
                    if detail_res.status_code == 200:
                        d_json = detail_res.json()
                        tx = d_json.get('transaction') or d_json.get('data') or d_json
                        if isinstance(tx, dict):
                            t_status = str(tx.get('status') or '').lower()
                            is_paid = t_status in ('successful', 'success', 'paid', 'completed', 'credited', 'funded')
                            return {
                                'found': True,
                                'confirmed': is_paid,
                                'status': 'successful' if is_paid else t_status.upper(),
                                'amount': tx.get('amount'),
                                'transaction_id': tx.get('id'),
                                'data': tx,
                                'attempts': attempts,
                            }
                    elif detail_res.status_code != 404:
                        last_error = detail_res.text
                except Exception as e:
                    logger.warning(f'NexaPay query error on {detail_url}: {e}')
                    attempts.append({'url': detail_url, 'error': str(e)})

        # Step 3: Query Virtual Accounts History endpoints
        for base in clean_bases:
            va_urls = [
                f'{base}/virtual-account/history?businessId={business_id}' if business_id else None,
                f'{base}/virtual-account/history',
            ]
            for va_url in va_urls:
                if not va_url:
                    continue
                try:
                    va_res = requests.get(va_url, headers=headers, timeout=15)
                    body_snippet = va_res.text[:300]
                    attempts.append({'url': va_url, 'status': va_res.status_code, 'body': body_snippet})
                    logger.info(f"NexaPay attempt: GET {va_url} -> status={va_res.status_code}, body={body_snippet}")
                    if va_res.status_code == 200:
                        va_json = va_res.json()
                        accounts = va_json.get('data') or va_json.get('virtualAccounts') or []
                        if isinstance(accounts, list):
                            for acc in accounts:
                                m_ref = str(
                                    acc.get('merchantReference') or
                                    acc.get('reference') or
                                    acc.get('merchant_reference') or
                                    acc.get('transaction_ref') or
                                    acc.get('transactionReference') or ''
                                ).strip()
                                if m_ref == ref_clean:
                                    acc_payment_status = str(
                                        acc.get('paymentStatus') or
                                        acc.get('payment_status') or
                                        acc.get('fundingStatus') or ''
                                    ).upper()
                                    is_paid = (
                                        acc_payment_status in ('PAID', 'SUCCESS', 'SUCCESSFUL', 'COMPLETED', 'CREDITED')
                                        or acc.get('isPaid') is True
                                        or acc.get('paid') is True
                                    )
                                    raw_status = str(acc.get('status') or 'ACTIVE').upper()
                                    return {
                                        'found': True,
                                        'confirmed': is_paid,
                                        'status': 'successful' if is_paid else raw_status,
                                        'message': 'Payment confirmed' if is_paid else f'Virtual account {raw_status.lower()}, awaiting transfer',
                                        'account_number': acc.get('accountNumber') or acc.get('account_number'),
                                        'expires_at': acc.get('expiresAt') or acc.get('expires_at'),
                                        'data': acc,
                                        'attempts': attempts,
                                    }
                        return {'found': False, 'confirmed': False, 'status': 'NOT_FOUND', 'message': 'Reference not found on NexaPay', 'attempts': attempts}
                    else:
                        last_error = va_res.text
                except Exception as e:
                    logger.error(f'NexaPay virtual-account/history query failed on {va_url}: {e}')
                    attempts.append({'url': va_url, 'error': str(e)})
                    last_error = str(e)

        return {'found': False, 'confirmed': False, 'status': 'NOT_PAID', 'error': last_error or 'Could not verify on NexaPay', 'attempts': attempts}

    @staticmethod
    def validate_webhook_signature(payload_body: bytes, signature: str, timestamp: str, secret_key: str) -> bool:
        """
        Validate the HMAC-SHA256 signature from NexaPay.

        NexaPay sends headers:
        - x-nexapay-signature
        - x-nexapay-timestamp

        Signature is HMAC SHA-256 computed over the raw payload and timestamp.
        """
        if not signature or not secret_key:
            return False

        secret_bytes = secret_key.strip().encode('utf-8')
        sig_raw = signature.strip()

        # Handle signature formats like "t=123,v1=abc..." or "sha256=abc..."
        sig_lower = sig_raw.lower()
        if ',' in sig_raw:
            parts = sig_raw.split(',')
            for part in parts:
                if '=' in part:
                    k, v = part.strip().split('=', 1)
                    k_clean = k.strip().lower()
                    if k_clean in ('v1', 'sig', 'signature'):
                        sig_lower = v.strip().lower()
                    elif k_clean == 't' and not timestamp:
                        timestamp = v.strip()
        elif sig_lower.startswith('sha256='):
            sig_lower = sig_lower[7:]
        elif sig_lower.startswith('v1='):
            sig_lower = sig_lower[3:]

        # Variant 1: raw body + timestamp
        if timestamp:
            signed_payload_1 = payload_body + timestamp.encode('utf-8')
            digest_1 = hmac.new(secret_bytes, signed_payload_1, hashlib.sha256).hexdigest().lower()
            if hmac.compare_digest(digest_1, sig_lower):
                return True

            # Variant 2: timestamp + raw body
            signed_payload_2 = timestamp.encode('utf-8') + payload_body
            digest_2 = hmac.new(secret_bytes, signed_payload_2, hashlib.sha256).hexdigest().lower()
            if hmac.compare_digest(digest_2, sig_lower):
                return True

            # Variant 3: timestamp.body
            signed_payload_3 = f'{timestamp}.'.encode('utf-8') + payload_body
            digest_3 = hmac.new(secret_bytes, signed_payload_3, hashlib.sha256).hexdigest().lower()
            if hmac.compare_digest(digest_3, sig_lower):
                return True

            # Variant 4: timestamp:body
            signed_payload_4 = f'{timestamp}:'.encode('utf-8') + payload_body
            digest_4 = hmac.new(secret_bytes, signed_payload_4, hashlib.sha256).hexdigest().lower()
            if hmac.compare_digest(digest_4, sig_lower):
                return True

        # Variant 5: payload_body alone
        digest_5 = hmac.new(secret_bytes, payload_body, hashlib.sha256).hexdigest().lower()
        if hmac.compare_digest(digest_5, sig_lower):
            return True

        return False


# Singleton instance
nexapay_service = NexaPayPaymentService()
