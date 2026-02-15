"""
SMM Provider API integration service.
Handles all communication with external SMM Panel API v2.
"""
import time
import logging
import requests
from decimal import Decimal
from typing import Optional, List, Dict, Any
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)

# Cache keys
SERVICES_CACHE_KEY = 'smm_provider_services'
BALANCE_CACHE_KEY = 'smm_provider_balance'


class SMMProviderError(Exception):
    """Custom exception for SMM provider errors."""
    pass


class SMMProvider:
    """
    SMM Panel API v2 Client.
    
    All methods handle:
    - Request/response logging
    - Error handling with retries
    - Response parsing
    """
    
    def __init__(self):
        self.api_url = settings.SMM_PROVIDER_URL
        self.api_key = settings.SMM_PROVIDER_KEY
        self.timeout = 30  # seconds
        self.max_retries = 3
    
    def _make_request(self, action: str, data: Dict[str, Any] = None, 
                      user=None, order=None) -> Dict[str, Any]:
        """
        Make a POST request to the SMM provider API.
        
        Args:
            action: API action (services, balance, add, status)
            data: Additional data to send
            user: User object for logging
            order: Order object for logging
        
        Returns:
            Parsed JSON response
        """
        from core.models import APILog
        
        start_time = time.time()
        request_data = {
            'key': self.api_key,
            'action': action,
            **(data or {})
        }
        
        # Remove key from logged data for security
        logged_request = {k: v for k, v in request_data.items() if k != 'key'}
        logged_request['key'] = '***HIDDEN***'
        
        response_data = {}
        response_code = None
        error_msg = ''
        
        for attempt in range(self.max_retries):
            try:
                response = requests.post(
                    self.api_url,
                    data=request_data,
                    timeout=self.timeout
                )
                response_code = response.status_code
                
                # Try to parse JSON
                try:
                    response_data = response.json()
                except ValueError:
                    response_data = {'raw': response.text[:500]}
                
                # Check for API errors in response
                if isinstance(response_data, dict) and 'error' in response_data:
                    error_msg = response_data['error']
                    logger.warning(f"SMM Provider error: {error_msg}")
                
                break  # Success, exit retry loop
                
            except requests.exceptions.Timeout:
                error_msg = f"Request timeout (attempt {attempt + 1}/{self.max_retries})"
                logger.warning(error_msg)
                if attempt < self.max_retries - 1:
                    time.sleep(1)  # Wait before retry
                    
            except requests.exceptions.RequestException as e:
                error_msg = f"Request failed: {str(e)}"
                logger.error(error_msg)
                if attempt < self.max_retries - 1:
                    time.sleep(1)
        
        # Calculate duration
        duration_ms = int((time.time() - start_time) * 1000)
        
        # Log the API call
        try:
            APILog.objects.create(
                action=action,
                request_data=logged_request,
                response_data=response_data if isinstance(response_data, dict) else {'data': str(response_data)[:1000]},
                response_code=response_code,
                error=error_msg,
                duration_ms=duration_ms,
                user=user,
                order=order
            )
        except Exception as log_error:
            logger.error(f"Failed to log API call: {log_error}")
        
        if error_msg and 'error' not in (response_data if isinstance(response_data, dict) else {}):
            raise SMMProviderError(error_msg)
        
        return response_data
    
    def get_services(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """
        Fetch available services from provider.
        Results are cached for SERVICE_CACHE_TTL seconds.
        
        Returns:
            List of service dictionaries
        """
        if not force_refresh:
            cached = cache.get(SERVICES_CACHE_KEY)
            if cached:
                logger.debug("Returning cached services")
                return cached
        
        # Demo mode - return mock data if no provider configured
        if not self.api_url or not self.api_key or self.api_key == 'demo-key':
            return self._get_demo_services()
        
        response = self._make_request('services')
        
        # Parse response
        if isinstance(response, list):
            services = response
        elif isinstance(response, dict) and 'error' in response:
            logger.error(f"Provider error getting services: {response['error']}")
            # Return cached or demo on error
            cached = cache.get(SERVICES_CACHE_KEY)
            return cached if cached else self._get_demo_services()
        else:
            services = []
        
        # Cache the result
        cache.set(SERVICES_CACHE_KEY, services, settings.SERVICE_CACHE_TTL)
        
        return services
    
    def get_balance(self, user=None) -> Dict[str, Any]:
        """
        Fetch provider account balance.
        
        Returns:
            Dict with 'balance' and 'currency'
        """
        if not self.api_url or not self.api_key or self.api_key == 'demo-key':
            return {'balance': '999.99', 'currency': 'NGN'}
        
        response = self._make_request('balance', user=user)
        
        if isinstance(response, dict) and 'error' not in response:
            return response
        
        return {'balance': '0', 'currency': 'NGN', 'error': response.get('error', 'Unknown error')}
    
    def create_order(self, service_id: int, link: str, quantity: int, 
                     comments: str = None, user=None, order=None) -> Dict[str, Any]:
        """
        Place a new order with the provider.
        
        Args:
            service_id: Provider service ID
            link: Target URL
            quantity: Order quantity
            comments: Optional custom comments (newline separated)
            user: User placing the order
            order: Order object for logging
        
        Returns:
            Dict with 'order' (order ID) on success
        """
        if not self.api_url or not self.api_key or self.api_key == 'demo-key':
            # Demo mode - return mock order ID
            import random
            return {'order': random.randint(10000, 99999)}
        
        order_data = {
            'service': service_id,
            'link': link,
            'quantity': quantity
        }
        
        # Add comments if provided (for custom comment services)
        if comments:
            order_data['comments'] = comments
        
        response = self._make_request(
            'add',
            data=order_data,
            user=user,
            order=order
        )
        
        return response
    
    def get_order_status(self, order_id: str, user=None, order=None) -> Dict[str, Any]:
        """
        Get status of an order from provider.
        
        Args:
            order_id: Provider order ID
            user: User requesting status
            order: Order object for logging
        
        Returns:
            Dict with status, charge, start_count, remains, currency
        """
        if not self.api_url or not self.api_key or self.api_key == 'demo-key':
            # Demo mode - return mock status
            import random
            statuses = ['Pending', 'In progress', 'Completed', 'Processing']
            return {
                'status': random.choice(statuses),
                'charge': '0.50',
                'start_count': str(random.randint(100, 1000)),
                'remains': str(random.randint(0, 500)),
                'currency': 'NGN'
            }
        
        response = self._make_request(
            'status',
            data={'order': order_id},
            user=user,
            order=order
        )
        
        return response
    
    def _get_demo_services(self) -> List[Dict[str, Any]]:
        """Return demo services for development/testing."""
        return [
            {
                "service": 1,
                "name": "Instagram Followers [Real HQ] [Max 50K]",
                "type": "Default",
                "category": "Instagram Followers",
                "rate": "0.85",
                "min": "10",
                "max": "50000",
                "refill": True,
                "cancel": False
            },
            {
                "service": 2,
                "name": "Instagram Likes [Instant] [Max 20K]",
                "type": "Default",
                "category": "Instagram Likes",
                "rate": "0.20",
                "min": "10",
                "max": "20000",
                "refill": False,
                "cancel": True
            },
            {
                "service": 3,
                "name": "TikTok Views [Instant Start]",
                "type": "Default",
                "category": "TikTok Views",
                "rate": "0.01",
                "min": "1000",
                "max": "10000000",
                "refill": False,
                "cancel": False
            },
            {
                "service": 4,
                "name": "TikTok Followers [Real] [Refill 30D]",
                "type": "Default",
                "category": "TikTok Followers",
                "rate": "1.50",
                "min": "50",
                "max": "100000",
                "refill": True,
                "cancel": False
            },
            {
                "service": 5,
                "name": "YouTube Subscribers [Non-Drop]",
                "type": "Default",
                "category": "YouTube Subscribers",
                "rate": "15.50",
                "min": "50",
                "max": "5000",
                "refill": True,
                "cancel": False
            },
            {
                "service": 6,
                "name": "YouTube Views [High Retention]",
                "type": "Default",
                "category": "YouTube Views",
                "rate": "2.50",
                "min": "500",
                "max": "1000000",
                "refill": False,
                "cancel": True
            },
            {
                "service": 7,
                "name": "Facebook Page Likes [Refill 30D]",
                "type": "Default",
                "category": "Facebook Page Likes",
                "rate": "2.40",
                "min": "100",
                "max": "50000",
                "refill": True,
                "cancel": False
            },
            {
                "service": 8,
                "name": "X/Twitter Followers [Real]",
                "type": "Default",
                "category": "Twitter Followers",
                "rate": "3.00",
                "min": "100",
                "max": "25000",
                "refill": True,
                "cancel": False
            },
            {
                "service": 9,
                "name": "X/Twitter Likes [Fast]",
                "type": "Default",
                "category": "Twitter Likes",
                "rate": "0.80",
                "min": "10",
                "max": "10000",
                "refill": False,
                "cancel": True
            },
            {
                "service": 10,
                "name": "Telegram Channel Members [Non-Drop]",
                "type": "Default",
                "category": "Telegram Members",
                "rate": "1.20",
                "min": "100",
                "max": "100000",
                "refill": True,
                "cancel": False
            }
        ]


# Singleton instance
smm_provider = SMMProvider()
