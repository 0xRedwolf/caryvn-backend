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

    @property
    def api_key(self):
        return getattr(settings, 'RESEND_API_KEY', '')

    @property
    def from_email(self):
        return getattr(settings, 'DEFAULT_FROM_EMAIL', 'Caryvn <noreply@caryvn.com>')

    @property
    def frontend_url(self):
        return getattr(settings, 'FRONTEND_URL', 'http://localhost:3000').rstrip('/')

    def _get_base_context(self):
        """Standard context for all emails with high-res public CDN logos and official URLs."""
        custom_logo_url = getattr(settings, 'CLOUDINARY_LOGO_URL', '')
        custom_logo_dark = getattr(settings, 'CLOUDINARY_LOGO_DARK_URL', '')

        # Use explicitly configured Cloudinary URL if provided; otherwise use live production asset
        # (Email clients like Gmail require publicly accessible HTTPS URLs — localhost or missing assets break)
        logo_url = custom_logo_url or "https://www.caryvn.com/logo-full.png"
        logo_dark_url = custom_logo_dark or logo_url

        return {
            'brand_name': 'Caryvn',
            'logo_url': logo_url,
            'logo_dark_url': logo_dark_url,
            'frontend_url': self.frontend_url,
            'support_email': getattr(settings, 'SUPPORT_EMAIL', 'support@caryvn.com'),
            'dashboard_url': f"{self.frontend_url}/dashboard",
            'orders_url': f"{self.frontend_url}/dashboard/orders",
            'wallet_url': f"{self.frontend_url}/dashboard/wallet",
            'tickets_url': f"{self.frontend_url}/dashboard/tickets",
        }

    def _send(self, subject, template_name, context, recipient_email):
        """Send an email via Resend HTTP API or fallback to Django's configured email backend."""
        # If no valid Resend API key is configured, fall back to Django's backend (e.g. console output locally)
        if not self.api_key or self.api_key == 're_your_actual_api_key_here':
            try:
                from django.core.mail import EmailMultiAlternatives
                html_message = render_to_string(f'emails/{template_name}', context)
                plain_message = strip_tags(html_message)
                msg = EmailMultiAlternatives(
                    subject=subject,
                    body=plain_message,
                    from_email=self.from_email,
                    to=[recipient_email],
                )
                msg.attach_alternative(html_message, 'text/html')
                msg.send(fail_silently=False)
                logger.info(f'Email delivered via Django EMAIL_BACKEND: "{subject}" → {recipient_email}')
                return True
            except Exception as e:
                logger.warning(f'RESEND_API_KEY not set and Django EMAIL_BACKEND fallback failed: {e}')
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
                    'User-Agent': 'Caryvn/1.0',
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

    def send_order_status_email(self, order, status_display, refund_amount=None):
        """Send notification when an order reaches a milestone: Completed, Partial, or Canceled with refund."""
        context = self._get_base_context()
        user = order.user
        context.update({
            'user': user,
            'order': order,
            'status_display': status_display,
            'refund_amount': f'{Decimal(str(refund_amount)):,.2f}' if refund_amount else None,
            'is_completed': order.status == 'completed',
            'is_partial': order.status == 'partial',
            'is_canceled': order.status in ('canceled', 'cancelled', 'refunded'),
        })
        self._send(
            subject=f'Order #{str(order.id)[:8]} Status: {status_display}',
            template_name='order_status_update.html',
            context=context,
            recipient_email=user.email,
        )


# Singleton instance
email_service = EmailService()
