"""
Password reset views for Caryvn.
Handles forgot-password request (sends email with token) and reset confirmation.
"""
import logging
from django.conf import settings
from django.contrib.auth import get_user_model
from django.contrib.auth.tokens import default_token_generator
from django.core.mail import send_mail
from django.template.loader import render_to_string
from django.utils.encoding import force_bytes, force_str
from django.utils.html import strip_tags
from django.utils.http import urlsafe_base64_encode, urlsafe_base64_decode
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView

User = get_user_model()
logger = logging.getLogger(__name__)

FRONTEND_URL = getattr(settings, 'FRONTEND_URL', 'http://localhost:3000')


class PasswordResetRequestView(APIView):
    """Send a password reset email with a one-time token link."""
    permission_classes = [permissions.AllowAny]
    throttle_scope = 'auth'

    def post(self, request):
        email = request.data.get('email', '').strip().lower()
        if not email:
            return Response(
                {'error': 'Email is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Always return success to prevent email enumeration
        try:
            user = User.objects.get(email=email)
        except User.DoesNotExist:
            return Response({'message': 'If that email exists, a reset link has been sent.'})

        # Generate token and uid
        token = default_token_generator.make_token(user)
        uid = urlsafe_base64_encode(force_bytes(user.pk))
        reset_url = f"{FRONTEND_URL}/reset-password?uid={uid}&token={token}"

        # Send email
        try:
            from core.services.email_service import email_service
            email_service.send_password_reset(user, reset_url)
            logger.info(f'Password reset email sent to {user.email}')
        except Exception as e:
            logger.error(f'Failed to send password reset email to {user.email}: {e}')
            return Response(
                {'error': 'Failed to send email. Please try again later.'},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR
            )

        return Response({'message': 'If that email exists, a reset link has been sent.'})


class PasswordResetConfirmView(APIView):
    """Verify the token and set a new password."""
    permission_classes = [permissions.AllowAny]

    def post(self, request):
        uid = request.data.get('uid', '')
        token = request.data.get('token', '')
        new_password = request.data.get('new_password', '')

        if not uid or not token or not new_password:
            return Response(
                {'error': 'uid, token, and new_password are required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        if len(new_password) < 8:
            return Response(
                {'error': 'Password must be at least 8 characters'},
                status=status.HTTP_400_BAD_REQUEST
            )

        try:
            user_id = force_str(urlsafe_base64_decode(uid))
            user = User.objects.get(pk=user_id)
        except (TypeError, ValueError, OverflowError, User.DoesNotExist):
            return Response(
                {'error': 'Invalid reset link'},
                status=status.HTTP_400_BAD_REQUEST
            )

        if not default_token_generator.check_token(user, token):
            return Response(
                {'error': 'Reset link has expired or is invalid'},
                status=status.HTTP_400_BAD_REQUEST
            )

        user.set_password(new_password)
        user.save()
        logger.info(f'Password reset successful for {user.email}')

        return Response({'message': 'Password has been reset successfully.'})
