"""
Email notification service for Caryvn.
Sends transactional emails via Resend HTTP API (bypasses SMTP — works on Railway).
"""
import logging
import urllib.request
import urllib.error
import json
from decimal import Decimal
from django.conf import settings
from django.template.loader import render_to_string
from django.utils.html import strip_tags

logger = logging.getLogger(__name__)


class EmailService:
    """Send transactional emails via Resend's REST API (no SMTP needed)."""

    RESEND_API_URL = 'https://api.resend.com/emails'

    def __init__(self):
        self.api_key = getattr(settings, 'RESEND_API_KEY', '')
        self.from_email = getattr(settings, 'DEFAULT_FROM_EMAIL', 'Caryvn <noreply@caryvn.com>')
        self.frontend_url = getattr(settings, 'FRONTEND_URL', 'http://localhost:3000').rstrip('/')

    def _get_base_context(self):
        """Standard context for all emails."""
        return {
            'brand_name': 'Caryvn',
            'logo_url': f"{self.frontend_url}/logo-full.png",
            'frontend_url': self.frontend_url,
        }

    def _send(self, subject, template_name, context, recipient_email):
        """Send an email via Resend HTTP API. Never raises — logs errors instead."""
        if not self.api_key:
            logger.warning(f'RESEND_API_KEY not set — skipping email "{subject}" to {recipient_email}')
            return False

        try:
            html_message = render_to_string(f'emails/{template_name}', context)
            plain_message = strip_tags(html_message)

            payload = json.dumps({
                'from': self.from_email,
                'to': [recipient_email],
                'subject': subject,
                'html': html_message,
                'text': plain_message,
            }).encode('utf-8')

            req = urllib.request.Request(
                self.RESEND_API_URL,
                data=payload,
                headers={
                    'Authorization': f'Bearer {self.api_key}',
                    'Content-Type': 'application/json',
                },
                method='POST',
            )

            with urllib.request.urlopen(req, timeout=15) as resp:
                resp_body = resp.read().decode('utf-8')
                logger.info(f'Email sent via Resend: "{subject}" → {recipient_email} | {resp_body}')
            return True

        except urllib.error.HTTPError as e:
            body = e.read().decode('utf-8') if e.fp else ''
            logger.error(f'Resend API error sending "{subject}" to {recipient_email}: HTTP {e.code} — {body}')
            return False
        except Exception as e:
            logger.error(f'Failed to send email "{subject}" to {recipient_email}: {e}')
            return False

    def send_order_confirmation(self, user, order):
        """Send order confirmation email after successful order placement."""
        context = self._get_base_context()
        context.update({'user': user, 'order': order})
        self._send(
            subject=f'Order Confirmed — #{str(order.id)[:8]}',
            template_name='order_confirmation.html',
            context=context,
            recipient_email=user.email,
        )

    def send_topup_success(self, user, amount, new_balance):
        """Send wallet top-up success email."""
        context = self._get_base_context()
        context.update({
            'user': user,
            'amount': f'{Decimal(str(amount)):,.2f}',
            'new_balance': f'{Decimal(str(new_balance)):,.2f}',
        })
        self._send(
            subject=f'Wallet Top-Up Successful — ₦{Decimal(str(amount)):,.2f}',
            template_name='topup_success.html',
            context=context,
            recipient_email=user.email,
        )

    def send_ticket_reply(self, ticket, reply, recipient_user):
        """Send notification when a ticket receives a reply."""
        context = self._get_base_context()
        context.update({'recipient': recipient_user, 'ticket': ticket, 'reply': reply})
        self._send(
            subject=f'New Reply on Ticket — {ticket.subject}',
            template_name='ticket_reply.html',
            context=context,
            recipient_email=recipient_user.email,
        )

    def send_password_reset(self, user, reset_url):
        """Send password reset link email."""
        context = self._get_base_context()
        context.update({'user': user, 'reset_url': reset_url})
        self._send(
            subject='Reset Your Password — Caryvn',
            template_name='password_reset.html',
            context=context,
            recipient_email=user.email,
        )


# Singleton instance
email_service = EmailService()
