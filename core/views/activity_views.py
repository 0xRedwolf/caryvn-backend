"""
Activity tracking views for user page visits and actions.
"""
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from django.contrib.auth import get_user_model

from core.models import UserActivity

User = get_user_model()


class IsAdminUser(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user and request.user.is_staff


def get_client_ip(request):
    """Extract client IP from request, handling proxies."""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        return x_forwarded_for.split(',')[0].strip()
    return request.META.get('REMOTE_ADDR')


class LogActivityView(APIView):
    """Log a page visit or action for the authenticated user."""
    permission_classes = [permissions.IsAuthenticated]

    def post(self, request):
        page = request.data.get('page', '')
        action = request.data.get('action', UserActivity.Action.PAGE_VISIT)
        metadata = request.data.get('metadata', {})

        if not page:
            return Response(
                {'error': 'page is required'},
                status=status.HTTP_400_BAD_REQUEST
            )

        # Validate action
        valid_actions = [choice[0] for choice in UserActivity.Action.choices]
        if action not in valid_actions:
            action = UserActivity.Action.PAGE_VISIT

        UserActivity.objects.create(
            user=request.user,
            action=action,
            page=page,
            metadata=metadata if isinstance(metadata, dict) else {},
            ip_address=get_client_ip(request),
            user_agent=request.META.get('HTTP_USER_AGENT', '')[:500],
        )

        return Response({'status': 'ok'}, status=status.HTTP_201_CREATED)


class AdminUserActivityView(APIView):
    """Admin endpoint to fetch a user's recent activity."""
    permission_classes = [permissions.IsAuthenticated, IsAdminUser]

    def get(self, request, user_id):
        try:
            user = User.objects.get(pk=user_id)
        except User.DoesNotExist:
            return Response(
                {'error': 'User not found'},
                status=status.HTTP_404_NOT_FOUND
            )

        limit = min(int(request.query_params.get('limit', 100)), 500)
        activities = UserActivity.objects.filter(user=user)[:limit]

        data = [
            {
                'id': str(a.id),
                'action': a.action,
                'page': a.page,
                'metadata': a.metadata,
                'ip_address': a.ip_address,
                'user_agent': a.user_agent,
                'created_at': a.created_at.isoformat(),
            }
            for a in activities
        ]

        return Response({
            'user_email': user.email,
            'activities': data,
        })
