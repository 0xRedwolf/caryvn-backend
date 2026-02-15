"""
Email notification service for Caryvn.
Sends transactional emails for orders, top-ups, and ticket replies.
"""
import logging
from decimal import Decimal
from django.conf import settings
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils.html import strip_tags

logger = logging.getLogger(__name__)


class EmailService:
    """Service for sending transactional email notifications."""

    def __init__(self):
        self.from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'Caryvn <noreply@caryvn.com>')

    def _send(self, subject, template_name, context, recipient_email):
        """Send an email using an HTML template. Never raises — logs errors instead."""
        try:
            html_message = render_to_string(f'emails/{template_name}', context)
            plain_message = strip_tags(html_message)

            send_mail(
                subject=subject,
                message=plain_message,
                from_email=self.from_email,
                recipient_list=[recipient_email],
                html_message=html_message,
                fail_silently=False,
            )
            logger.info(f'Email sent: {subject} → {recipient_email}')
            return True

        except Exception as e:
            logger.error(f'Failed to send email "{subject}" to {recipient_email}: {e}')
            return False

    def send_order_confirmation(self, user, order):
        """Send order confirmation email after successful order placement."""
        context = {
            'user': user,
            'order': order,
            'brand_name': 'Caryvn',
        }
        self._send(
            subject=f'Order Confirmed — #{str(order.id)[:8]}',
            template_name='order_confirmation.html',
            context=context,
            recipient_email=user.email,
        )

    def send_topup_success(self, user, amount, new_balance):
        """Send wallet top-up success email."""
        context = {
            'user': user,
            'amount': f'{Decimal(str(amount)):,.2f}',
            'new_balance': f'{Decimal(str(new_balance)):,.2f}',
            'brand_name': 'Caryvn',
        }
        self._send(
            subject=f'Wallet Top-Up Successful — ₦{Decimal(str(amount)):,.2f}',
            template_name='topup_success.html',
            context=context,
            recipient_email=user.email,
        )

    def send_ticket_reply(self, ticket, reply, recipient_user):
        """Send notification when a ticket receives a reply."""
        context = {
            'recipient': recipient_user,
            'ticket': ticket,
            'reply': reply,
            'brand_name': 'Caryvn',
        }
        self._send(
            subject=f'New Reply on Ticket — {ticket.subject}',
            template_name='ticket_reply.html',
            context=context,
            recipient_email=recipient_user.email,
        )


# Singleton instance
email_service = EmailService()
