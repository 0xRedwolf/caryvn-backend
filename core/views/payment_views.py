"""
Payment views for Caryvn.
Handles Squad payment initiation, verification, and webhook processing.
"""
import json
import logging
from decimal import Decimal
from django.views.decorators.csrf import csrf_exempt
from django.utils.decorators import method_decorator
from rest_framework import status, permissions
from rest_framework.response import Response
from rest_framework.views import APIView
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
