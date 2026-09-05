"""
User Session tracking and device analytics service.
Parses user agent data to display active device sessions and support remote revocation.
"""
import logging
from django.utils import timezone
from ..models import UserSession

logger = logging.getLogger(__name__)


def parse_device_info(ua_string: str) -> dict:
    """
    Parse a User-Agent string to extract device_type, browser, and OS
    without heavy external dependencies.
    """
    ua = ua_string or ''
    ua_lower = ua.lower()

    # Determine Device Type
    if 'ipad' in ua_lower or 'tablet' in ua_lower:
        device_type = 'Tablet'
    elif 'mobi' in ua_lower or 'iphone' in ua_lower or 'android' in ua_lower:
        device_type = 'Mobile'
    else:
        device_type = 'Desktop'

    # Determine Operating System
    if 'windows nt 10.0' in ua_lower or 'windows nt 11.0' in ua_lower or 'windows nt' in ua_lower:
        os = 'Windows'
    elif 'macintosh' in ua_lower or 'mac os x' in ua_lower:
        os = 'macOS'
    elif 'iphone' in ua_lower or 'ipad' in ua_lower or 'ios' in ua_lower:
        os = 'iOS'
    elif 'android' in ua_lower:
        os = 'Android'
    elif 'linux' in ua_lower:
        os = 'Linux'
    elif 'cros' in ua_lower:
        os = 'ChromeOS'
    else:
        os = 'Unknown OS'

    # Determine Browser
    if 'edg/' in ua_lower or 'edge/' in ua_lower:
        browser = 'Microsoft Edge'
    elif 'opr/' in ua_lower or 'opera' in ua_lower:
        browser = 'Opera'
    elif 'chrome/' in ua_lower and 'safari/' in ua_lower and 'edg/' not in ua_lower:
        browser = 'Google Chrome'
    elif 'safari/' in ua_lower and 'chrome/' not in ua_lower:
        browser = 'Apple Safari'
    elif 'firefox/' in ua_lower:
        browser = 'Mozilla Firefox'
    elif 'samsungbrowser' in ua_lower:
        browser = 'Samsung Internet'
    else:
        browser = 'Web Browser'

    return {
        'device_type': device_type,
        'browser': browser,
        'os': os,
    }


def get_client_ip(request) -> str:
    """Extract client IP handling proxies / Cloudflare headers."""
    x_forwarded_for = request.META.get('HTTP_X_FORWARDED_FOR')
    if x_forwarded_for:
        ip = x_forwarded_for.split(',')[0].strip()
    else:
        ip = request.META.get('REMOTE_ADDR')
    return ip or '127.0.0.1'


def get_client_location(request) -> str:
    """Extract location from Cloudflare / CDN headers if present."""
    country = request.META.get('HTTP_CF_IPCOUNTRY') or request.META.get('HTTP_X_COUNTRY_CODE')
    city = request.META.get('HTTP_CF_IPCITY') or request.META.get('HTTP_X_CITY')
    if city and country:
        return f"{city}, {country}"
    if country:
        return country
    return "Active Network"


def register_user_session(user, request, session_key: str) -> UserSession:
    """Register or refresh an active user session upon authentication."""
    try:
        ip = get_client_ip(request)
        ua = request.META.get('HTTP_USER_AGENT', '')[:500]
        parsed = parse_device_info(ua)
        location = get_client_location(request)

        session, _ = UserSession.objects.update_or_create(
            session_key=session_key,
            defaults={
                'user': user,
                'ip_address': ip,
                'user_agent': ua,
                'device_type': parsed['device_type'],
                'browser': parsed['browser'],
                'os': parsed['os'],
                'location': location,
                'is_active': True,
                'last_active_at': timezone.now(),
            }
        )
        return session
    except Exception as e:
        logger.error(f"Failed to register user session for {user.email}: {e}")
        return None


def touch_user_session(session_key: str):
    """Update last_active_at timestamp for an existing session."""
    try:
        UserSession.objects.filter(session_key=session_key, is_active=True).update(
            last_active_at=timezone.now()
        )
    except Exception as e:
        logger.debug(f"Failed to touch user session: {e}")
