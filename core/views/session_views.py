"""
Session views allowing users to inspect active logged-in devices and revoke sessions remotely.
"""
import logging
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import status, permissions
from ..models import UserSession
from ..services.session_service import get_client_ip

logger = logging.getLogger(__name__)


class UserSessionListView(APIView):
    """
    List active device sessions for the authenticated user,
    or revoke all other sessions.
    """
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        current_ip = get_client_ip(request)
        ua = request.META.get('HTTP_USER_AGENT', '')

        # Query all active sessions for this user
        sessions = UserSession.objects.filter(user=request.user, is_active=True).order_by('-last_active_at')
        
        results = []
        found_current = False

        for sess in sessions:
            # Check if this session matches current request
            is_current = False
            if not found_current and (sess.ip_address == current_ip or sess.user_agent == ua):
                is_current = True
                found_current = True

            results.append({
                'id': str(sess.id),
                'device_type': sess.device_type,
                'browser': sess.browser,
                'os': sess.os,
                'ip_address': sess.ip_address,
                'location': sess.location,
                'last_active_at': sess.last_active_at.isoformat() if sess.last_active_at else None,
                'created_at': sess.created_at.isoformat() if sess.created_at else None,
                'is_current': is_current,
            })

        # If no session matched exact IP/UA, mark the first one as current
        if results and not found_current:
            results[0]['is_current'] = True

        return Response({
            'sessions': results,
            'total_active': len(results),
        })

    def post(self, request):
        """
        Revoke all sessions other than the current device session.
        """
        current_session_id = request.data.get('current_session_id')
        current_ip = get_client_ip(request)

        qs = UserSession.objects.filter(user=request.user, is_active=True)
        if current_session_id:
            qs = qs.exclude(id=current_session_id)
        else:
            # Fallback: preserve the most recently updated session matching current IP
            preserve = qs.filter(ip_address=current_ip).first()
            if preserve:
                qs = qs.exclude(id=preserve.id)

        revoked_count = qs.update(is_active=False)
        return Response({
            'message': f'Successfully logged out of {revoked_count} other session(s).',
            'revoked_count': revoked_count
        })


class UserSessionDetailView(APIView):
    """
    Revoke a single specific session.
    """
    permission_classes = [permissions.IsAuthenticated]

    def delete(self, request, session_id):
        try:
            session = UserSession.objects.get(id=session_id, user=request.user)
            session.is_active = False
            session.save(update_fields=['is_active'])
            return Response({'message': 'Session revoked successfully.'})
        except UserSession.DoesNotExist:
            return Response({'error': 'Session not found.'}, status=status.HTTP_404_NOT_FOUND)
