"""
Payment views for Caryvn.
Handles Squad payment initiation, verification, and webhook processing.
"""
import json
import logging
import re
from decimal import Decimal
from rest_framework import status, permissions
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.throttling import UserRateThrottle
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from core.models import Transaction, Wallet
from core.services.squad import squad_service, SquadPaymentError

logger = logging.getLogger(__name__)

MIN_TOPUP_AMOUNT = Decimal('100')      # ₦100 minimum
MAX_TOPUP_AMOUNT = Decimal('500000')   # ₦500,000 maximum


class InitiateTopupView(APIView):
    """Initiate a wallet top-up via Squad payment."""

    def post(self, request):
        amount = request.data.get('amount')
        callback_url = request.data.get('callback_url', '')

        if not amount:
            return Response(
                {'error': 'Amount is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            amount = Decimal(str(amount))
        except Exception:
            return Response(
                {'error': 'Invalid amount'},
                status=status.HTTP_400_BAD_REQUEST
            )

        if amount < MIN_TOPUP_AMOUNT:
            return Response(
                {'error': f'Minimum top-up amount is ₦{MIN_TOPUP_AMOUNT:,.0f}'},
                status=status.HTTP_400_BAD_REQUEST
            )

        if amount > MAX_TOPUP_AMOUNT:
            return Response(
                {'error': f'Maximum top-up amount is ₦{MAX_TOPUP_AMOUNT:,.0f}'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Generate reference and create pending transaction
        reference = squad_service.generate_reference()
        wallet = request.user.wallet

        try:
            transaction = wallet.create_pending_deposit(
                amount=amount,
                payment_reference=reference,
                payment_gateway='squad',
                description=f'Wallet top-up via Squad (₦{amount:,.2f})',
            )
        except Exception as e:
            logger.error(f'Failed to create pending transaction: {e}')
            return Response(
                {'error': 'Failed to create transaction'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        # Call Squad to initiate payment
        try:
            result = squad_service.initiate_payment(
                email=request.user.email,
                amount_naira=amount,
                transaction_ref=reference,
                callback_url=callback_url,
                customer_name=request.user.get_full_name(),
            )

            return Response({
                'checkout_url': result['checkout_url'],
                'reference': reference,
                'amount': str(amount),
            })

        except SquadPaymentError as e:
            # Mark transaction as failed since Squad rejected it
            wallet.fail_deposit(transaction)
            logger.error(f'Squad initiate failed: {e}')
            return Response(
                {'error': str(e)},
                status=status.HTTP_502_BAD_GATEWAY
            )


class InitiateManualTopupView(APIView):
    """Initiate a wallet top-up via Manual Bank Transfer."""

    def post(self, request):
        import uuid
        amount = request.data.get('amount')
        payment_proof = request.FILES.get('payment_proof')

        if not amount:
            return Response(
                {'error': 'Amount is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
            
        if not payment_proof:
            return Response(
                {'error': 'Payment proof image is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            amount = Decimal(str(amount))
        except Exception:
            return Response(
                {'error': 'Invalid amount'},
                status=status.HTTP_400_BAD_REQUEST
            )

        if amount < MIN_TOPUP_AMOUNT:
            return Response(
                {'error': f'Minimum top-up amount is ₦{MIN_TOPUP_AMOUNT:,.0f}'},
                status=status.HTTP_400_BAD_REQUEST
            )

        if amount > MAX_TOPUP_AMOUNT:
            return Response(
                {'error': f'Maximum top-up amount is ₦{MAX_TOPUP_AMOUNT:,.0f}'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Generate a unique internal reference for the manual transfer
        reference = f'MN|{uuid.uuid4().hex[:12].upper()}'
        wallet = request.user.wallet

        try:
            transaction = wallet.create_pending_deposit(
                amount=amount,
                payment_reference=reference,
                payment_gateway='manual',
                description=f'Wallet deposit via Manual Transfer (₦{amount:,.2f})',
            )
            
            # Attach the uploaded proof
            transaction.payment_proof = payment_proof
            transaction.save(update_fields=['payment_proof'])
            
            return Response({
                'message': 'Manual transfer proof submitted successfully. Pending admin approval.',
                'reference': reference,
                'amount': str(amount),
            })
            
        except Exception as e:
            logger.error(f'Failed to create manual pending transaction: {e}')
            return Response(
                {'error': 'Failed to submit payment proof'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )


class CryptoTopupRateThrottle(UserRateThrottle):
    """Max 10 crypto deposit submissions per user per hour."""
    scope = 'crypto_topup'
    rate = '10/hour'


class InitiateCryptoTopupView(APIView):
    """Initiate a wallet top-up via Crypto (Binance Pay or On-Chain)."""

    throttle_classes = [CryptoTopupRateThrottle]

    # Minimum deposit: $2 USDT equivalent flat amount
    MIN_CRYPTO_AMOUNT = Decimal('2')

    # Allowed on-chain token keys
    VALID_TOKENS = ('usdt_trc20', 'usdt_bep20', 'sol')

    # Reference ID constraints
    MAX_REFERENCE_LEN = 100
    REFERENCE_RE = re.compile(r'^[A-Za-z0-9\-_]+$')

    def post(self, request):
        import uuid
        from io import BytesIO
        from django.db import IntegrityError
        from core.models import SiteSettings

        method = request.data.get('method', '')  # 'binance_pay' or 'on_chain'
        token = request.data.get('token', '')    # e.g. 'usdt_trc20' (on_chain only)
        amount = request.data.get('amount')
        reference_id = request.data.get('reference_id', '').strip()  # Order ID or TXID
        payment_proof = request.FILES.get('payment_proof')

        # ── Validate method ────────────────────────────────────────────────────
        if method not in ('binance_pay', 'on_chain'):
            return Response({'error': 'Invalid method. Use "binance_pay" or "on_chain".'},
                            status=status.HTTP_400_BAD_REQUEST)

        # ── Validate amount ────────────────────────────────────────────────────
        if not amount:
            return Response({'error': 'Amount is required'}, status=status.HTTP_400_BAD_REQUEST)
        try:
            amount = Decimal(str(amount))
        except Exception:
            return Response({'error': 'Invalid amount'}, status=status.HTTP_400_BAD_REQUEST)
        if amount < self.MIN_CRYPTO_AMOUNT:
            return Response(
                {'error': f'Minimum crypto deposit is ${self.MIN_CRYPTO_AMOUNT} USDT'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # ── Validate reference (Order ID / TXID) ───────────────────────────────
        if not reference_id:
            return Response(
                {'error': 'Order ID / Transaction ID is required'},
                status=status.HTTP_400_BAD_REQUEST
            )
        if len(reference_id) > self.MAX_REFERENCE_LEN:
            return Response(
                {'error': f'Reference ID must not exceed {self.MAX_REFERENCE_LEN} characters'},
                status=status.HTTP_400_BAD_REQUEST
            )
        if not self.REFERENCE_RE.match(reference_id):
            return Response(
                {'error': 'Reference ID may only contain letters, numbers, hyphens and underscores'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # ── Method-specific validation ─────────────────────────────────────────
        if method == 'binance_pay':
            gateway = 'binance_pay'
            description = f'Wallet deposit via Binance Pay (${amount:,.2f} USDT)'

        else:  # on_chain
            if token not in self.VALID_TOKENS:
                return Response(
                    {'error': f'Invalid token. Choose from: {", ".join(self.VALID_TOKENS)}'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            if not payment_proof:
                return Response(
                    {'error': 'Payment screenshot is required for on-chain deposits'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            # Image-only validation
            content_type = getattr(payment_proof, 'content_type', '')
            if content_type not in ('image/jpeg', 'image/png'):
                return Response(
                    {'error': 'Only JPG and PNG screenshots are accepted'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            # Size check (5 MB)
            if payment_proof.size > 5 * 1024 * 1024:
                return Response(
                    {'error': 'Screenshot must be smaller than 5 MB'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            # Strip EXIF metadata by re-saving through Pillow.
            # If Pillow cannot open the file, we reject it — do NOT fall back
            # to saving the raw bytes (could allow disguised non-image files).
            try:
                from PIL import Image as PilImage
                img = PilImage.open(payment_proof)
                img.verify()  # Raises if not a valid image format
                # Re-open after verify() (verify() leaves the file in a bad state)
                payment_proof.seek(0)
                img = PilImage.open(payment_proof)
                img = img.convert('RGB')  # strips EXIF, alpha, palette channels
                buffer = BytesIO()
                img.save(buffer, format='JPEG', quality=92)
                buffer.seek(0)
                from django.core.files.uploadedfile import InMemoryUploadedFile
                import sys
                payment_proof = InMemoryUploadedFile(
                    buffer, 'payment_proof',
                    f'proof_{uuid.uuid4().hex[:8]}.jpg',
                    'image/jpeg', sys.getsizeof(buffer), None
                )
            except Exception as e:
                logger.warning(f'Image validation/EXIF strip failed: {e}')
                return Response(
                    {'error': 'The uploaded file could not be verified as a valid image. Please upload a clear JPG or PNG screenshot.'},
                    status=status.HTTP_400_BAD_REQUEST
                )

            TOKEN_LABEL_MAP = {
                'usdt_trc20': 'USDT-TRC20',
                'usdt_bep20': 'USDT-BEP20',
                'sol': 'USDC-SOL',
            }
            token_label = TOKEN_LABEL_MAP.get(token, token.upper().replace('_', '-'))
            gateway = f'on_chain_{token}'
            description = f'Wallet deposit via On-Chain {token_label} (${amount:,.2f})'

        # ── Create pending transaction (reference = TXID/Order ID for uniqueness) ─
        wallet = request.user.wallet
        try:
            tx = wallet.create_pending_deposit(
                amount=amount,
                payment_reference=reference_id,
                payment_gateway=gateway,
                description=description,
            )
        except IntegrityError:
            return Response(
                {'error': 'This Order ID / TXID has already been submitted. If you believe this is an error, contact support.'},
                status=status.HTTP_409_CONFLICT
            )
        except Exception as e:
            logger.error(f'Failed to create crypto pending transaction: {e}')
            return Response(
                {'error': 'Failed to submit deposit. Please try again.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        # Attach proof image for on-chain deposits
        if method == 'on_chain' and payment_proof:
            tx.payment_proof = payment_proof
            tx.save(update_fields=['payment_proof'])

        return Response({
            'message': 'Crypto deposit submitted successfully. Pending admin approval.',
            'reference': reference_id,
            'amount': str(amount),
            'gateway': gateway,
        }, status=status.HTTP_201_CREATED)


class VerifyTopupView(APIView):
    """Verify a wallet top-up payment via Squad."""

    def get(self, request):
        reference = request.query_params.get('reference', '')

        if not reference:
            return Response(
                {'error': 'Reference is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Find the pending transaction
        try:
            transaction = Transaction.objects.get(
                payment_reference=reference,
                wallet__user=request.user,
            )
        except Transaction.DoesNotExist:
            return Response(
                {'error': 'Transaction not found'},
                status=status.HTTP_404_NOT_FOUND
            )

        # If already processed, return current state
        if transaction.status == Transaction.Status.SUCCESS:
            wallet = request.user.wallet
            return Response({
                'status': 'success',
                'message': 'Payment already confirmed',
                'balance': str(wallet.balance),
                'amount': str(transaction.amount),
            })

        if transaction.status == Transaction.Status.FAILED:
            return Response({
                'status': 'failed',
                'message': 'Payment failed',
            })

        # Verify with Squad
        try:
            result = squad_service.verify_payment(reference)

            if result['success']:
                # Verify amount matches (convert to Naira for comparison)
                expected_amount = transaction.amount
                actual_amount = Decimal(str(result['amount_naira']))

                if abs(expected_amount - actual_amount) > Decimal('1'):
                    logger.warning(
                        f'Amount mismatch for {reference}: '
                        f'expected={expected_amount}, actual={actual_amount}'
                    )
                    return Response(
                        {'error': 'Amount mismatch'},
                        status=status.HTTP_400_BAD_REQUEST
                    )

                # Credit wallet (idempotent)
                wallet = request.user.wallet
                new_balance = wallet.confirm_deposit(transaction)

                # Send email notification (imported here to avoid circular imports)
                try:
                    from core.services.email_service import email_service
                    email_service.send_topup_success(
                        request.user, transaction.amount, new_balance
                    )
                except Exception as e:
                    logger.error(f'Failed to send top-up email: {e}')

                return Response({
                    'status': 'success',
                    'message': 'Payment confirmed',
                    'balance': str(new_balance),
                    'amount': str(transaction.amount),
                })
            else:
                wallet = request.user.wallet
                wallet.fail_deposit(transaction)
                return Response({
                    'status': 'failed',
                    'message': 'Payment was not successful',
                })

        except SquadPaymentError as e:
            logger.error(f'Squad verify failed: {e}')
            return Response(
                {'error': f'Verification failed: {str(e)}'},
                status=status.HTTP_502_BAD_GATEWAY
            )


@method_decorator(csrf_exempt, name='dispatch')
class SquadWebhookView(APIView):
    """Handle Squad payment webhooks."""
    permission_classes = [permissions.AllowAny]
    authentication_classes = []  # No JWT auth for webhooks

    def post(self, request):
        # Get the raw body and signature
        raw_body = request.body
        signature = request.META.get('HTTP_X_SQUAD_ENCRYPTED_BODY', '')

        # Validate signature
        from django.conf import settings as django_settings
        secret_key = django_settings.SQUAD_SECRET_KEY

        if secret_key and signature:
            is_valid = squad_service.validate_webhook_signature(
                raw_body, signature, secret_key
            )
            if not is_valid:
                logger.warning('Invalid Squad webhook signature')
                return Response(
                    {'error': 'Invalid signature'},
                    status=status.HTTP_401_UNAUTHORIZED
                )

        # Parse the webhook payload
        try:
            payload = json.loads(raw_body)
        except json.JSONDecodeError:
            return Response(
                {'error': 'Invalid JSON'},
                status=status.HTTP_400_BAD_REQUEST
            )

        event = payload.get('Event', '')
        data = payload.get('Body', payload.get('TransactionRef', payload))

        # Handle successful payment
        if event == 'charge_successful' or payload.get('transaction_status') == 'Success':
            transaction_ref = (
                data.get('transaction_ref') or
                data.get('TransactionRef') or
                payload.get('transaction_ref', '')
            )
            amount_kobo = (
                data.get('amount') or
                data.get('transaction_amount') or
                payload.get('amount', 0)
            )

            if not transaction_ref:
                logger.warning('Webhook missing transaction_ref')
                return Response({'status': 'ok'})

            # Find and credit the transaction
            try:
                transaction = Transaction.objects.select_related(
                    'wallet', 'wallet__user'
                ).get(payment_reference=transaction_ref)

                if transaction.status == Transaction.Status.PENDING:
                    wallet = transaction.wallet
                    new_balance = wallet.confirm_deposit(transaction)

                    # Send email
                    try:
                        from core.services.email_service import email_service
                        email_service.send_topup_success(
                            wallet.user, transaction.amount, new_balance
                        )
                    except Exception as e:
                        logger.error(f'Failed to send top-up email from webhook: {e}')

                    logger.info(
                        f'Webhook credited wallet for {transaction_ref}: '
                        f'amount={transaction.amount}, new_balance={new_balance}'
                    )

            except Transaction.DoesNotExist:
                logger.warning(f'Webhook: transaction not found for ref={transaction_ref}')

        return Response({'status': 'ok'})
