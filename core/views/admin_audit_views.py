"""
Admin views to inspect tamper-proof audit logs and operational history.
"""
import logging
from rest_framework.views import APIView
from rest_framework.response import Response
from rest_framework import permissions
from django.core.paginator import Paginator
from django.db.models import Q
from ..models import AdminAuditLog

logger = logging.getLogger(__name__)


class AdminAuditLogListView(APIView):
    """
    Paginated, filterable endpoint for administrative audit logs.
    Only accessible to admin staff.
    """
    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        qs = AdminAuditLog.objects.select_related('actor').all()

        # Action filter
        action = request.query_params.get('action')
        if action:
            qs = qs.filter(action=action)

        # Target model filter
        target_model = request.query_params.get('target_model')
        if target_model:
            qs = qs.filter(target_model__iexact=target_model)

        # Search query across description, target_id, or actor email
        search = request.query_params.get('search')
        if search:
            qs = qs.filter(
                Q(description__icontains=search) |
                Q(target_id__icontains=search) |
                Q(actor__email__icontains=search) |
                Q(actor__username__icontains=search)
            )

        page_number = int(request.query_params.get('page', 1))
        page_size = min(int(request.query_params.get('page_size', 25)), 100)

        paginator = Paginator(qs, page_size)
        page = paginator.get_page(page_number)

        results = []
        for item in page.object_list:
            results.append({
                'id': str(item.id),
                'action': item.action,
                'action_display': item.get_action_display(),
                'target_model': item.target_model,
                'target_id': item.target_id,
                'description': item.description,
                'changes': item.changes,
                'ip_address': item.ip_address,
                'actor': {
                    'id': str(item.actor.id) if item.actor else None,
                    'email': item.actor.email if item.actor else 'System Automated',
                    'first_name': item.actor.first_name if item.actor else '',
                } if item.actor else None,
                'created_at': item.created_at.isoformat(),
            })

        return Response({
            'logs': results,
            'total_count': paginator.count,
            'total_pages': paginator.num_pages,
            'current_page': page.number,
            'page_size': page_size,
        })
