"""
SMM Provider API integration service.
Handles all communication with external SMM Panel API v2.
Supports multiple providers via get_provider_client() factory.
"""
import time
import logging
import requests
from decimal import Decimal
from typing import Optional, List, Dict, Any
from django.conf import settings
from django.core.cache import cache

logger = logging.getLogger(__name__)


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
    
    def __init__(self, api_url: str = '', api_key: str = '', provider_slug: str = 'default', provider_id: int = None):
        """
        Initialize provider client.
        
        Args:
            api_url: The provider API endpoint URL
            api_key: The provider API key
            provider_slug: Slug for cache key namespacing
            provider_id: Database ID of the Provider model (for API logging)
        """
        self.api_url = api_url
        self.api_key = api_key
        self.provider_slug = provider_slug
        self.provider_id = provider_id
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
            log_kwargs = dict(
                action=action,
                request_data=logged_request,
                response_data=response_data if isinstance(response_data, dict) else {'data': str(response_data)[:1000]},
                response_code=response_code,
                error=error_msg,
                duration_ms=duration_ms,
                user=user,
                order=order,
            )
            # Attach provider FK if we have an ID
            if self.provider_id:
                from core.models import Provider
                try:
                    log_kwargs['provider_id'] = self.provider_id
                except Exception:
                    pass
            APILog.objects.create(**log_kwargs)
        except Exception as log_error:
            logger.error(f"Failed to log API call: {log_error}")
        
        if error_msg and 'error' not in (response_data if isinstance(response_data, dict) else {}):
            raise SMMProviderError(error_msg)
        
        return response_data
    
    def get_services(self, force_refresh: bool = False) -> List[Dict[str, Any]]:
        """
        Fetch available services from provider.
        Results are cached per provider for SERVICE_CACHE_TTL seconds.
        
        Returns:
            List of service dictionaries
        """
        cache_key = f'smm_provider_services_{self.provider_slug}'
        
        if not force_refresh:
            cached = cache.get(cache_key)
            if cached:
                logger.debug(f"Returning cached services for {self.provider_slug}")
                return cached
        
        # Demo mode - return mock data if no provider configured
        if not self.api_url or not self.api_key or self.api_key == 'demo-key':
            return self._get_demo_services()
        
        response = self._make_request('services')
        
        # Parse response
        if isinstance(response, list):
            services = response
        elif isinstance(response, dict):
            # NEW LOGIC: Handle providers that return a dictionary with a stringified list in 'data'
            if 'error' in response:
                logger.error(f"Provider error getting services: {response['error']}")
                cached = cache.get(cache_key)
                return cached if cached else self._get_demo_services()
            elif 'data' in response and isinstance(response['data'], str):
                import json
                try:
                    # Sometimes the data string uses single quotes instead of double quotes
                    data_str = response['data'].replace("'", '"')
                    # Remove boolean literals that cause JSON errors and replace them
                    data_str = data_str.replace("False", "false").replace("True", "true").replace("None", "null")
                    services = json.loads(data_str)
                except json.JSONDecodeError as e:
                    logger.error(f"Failed to parse inner data string: {e}")
                    services = []
            else:
                 services = []
        else:
            services = []
        
        # Cache the result
        cache.set(cache_key, services, settings.SERVICE_CACHE_TTL)
        
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
            service_id: Provider service ID (external_id)
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
    
    def create_refill(self, order_id: str, user=None, order=None) -> Dict[str, Any]:
        """
        Request a refill for an order from the provider.
        
        Args:
            order_id: Provider order ID
            user: User requesting the refill
            order: Order object for logging
            
        Returns:
            Dict with 'refill' (refill ID) on success
        """
        if not self.api_url or not self.api_key or self.api_key == 'demo-key':
            # Demo mode - return mock refill
            import random
            return {'refill': str(random.randint(1000, 9999))}
            
        response = self._make_request(
            'refill',
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
        ]


def get_provider_client(provider) -> SMMProvider:
    """
    Factory function: create an SMMProvider client from a Provider model instance.
    
    Args:
        provider: A core.models.Provider instance
    
    Returns:
        Configured SMMProvider client
    """
    return SMMProvider(
        api_url=provider.api_url,
        api_key=provider.api_key,
        provider_slug=provider.slug,
        provider_id=provider.pk,
    )
