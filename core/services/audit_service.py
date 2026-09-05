"""
Audit logging service for tracking high-impact administrative and system operations.
"""
import logging
from ..models import AdminAuditLog
from .session_service import get_client_ip

logger = logging.getLogger(__name__)


def log_admin_action(
    actor,
    action: str,
    target_model: str,
    target_id: str,
    description: str = '',
    changes: dict = None,
    request=None
) -> AdminAuditLog:
    """
    Log an administrative action into the immutable AdminAuditLog.
    """
    try:
        ip = get_client_ip(request) if request else None
        log_entry = AdminAuditLog.objects.create(
            actor=actor if actor and getattr(actor, 'is_authenticated', False) else None,
            action=action,
            target_model=target_model,
            target_id=str(target_id),
            description=description,
            changes=changes or {},
            ip_address=ip,
        )
        logger.info(f"AdminAuditLog created: [{action}] on {target_model} #{target_id} by {getattr(actor, 'email', 'System')}")
        return log_entry
    except Exception as e:
        logger.error(f"Failed to record AdminAuditLog: {e}", exc_info=True)
        return None
