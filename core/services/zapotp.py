"""
ZapOTP API Client Service.
Documentation: https://zapotp.com/account/api_docs
Base URL: https://zapotp.com/account/api/v1
Handles upstream requests, caching, and rate limiting.
"""
import logging
import requests
from decimal import Decimal
from typing import Dict, Any, List, Optional
from django.core.cache import cache
from django.utils import timezone
from core.models import OTPProviderSetting

logger = logging.getLogger(__name__)


class ZapOTPError(Exception):
    """Base exception for ZapOTP API communication failures."""
    pass


class ZapOTPClient:
    """
    Client for communicating with ZapOTP API v1.
    Transacts natively in NGN (Nigerian Naira).
    """

    def __init__(self, api_key: Optional[str] = None, base_url: Optional[str] = None):
        settings = OTPProviderSetting.get_settings()
        self.api_key = api_key or settings.api_key
        self.base_url = (base_url or settings.base_url or "https://zapotp.com/account/api/v1").rstrip('/')
        self.timeout = 25  # seconds

    def _headers(self) -> Dict[str, str]:
        if not self.api_key:
            raise ZapOTPError("ZapOTP API Key is not configured in Admin Settings.")
        return {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
            "Accept": "application/json",
            "User-Agent": "Caryvn-Platform/1.0",
        }

    def _request(self, method: str, endpoint: str, params: Optional[Dict[str, Any]] = None, json_data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        url = f"{self.base_url}/{endpoint.lstrip('/')}"
        try:
            response = requests.request(
                method=method,
                url=url,
                headers=self._headers(),
                params=params,
                json=json_data,
                timeout=self.timeout
            )
            response.raise_for_status()
            res_json = response.json()
            
            # ZapOTP standard response format: {"status": "success", "data": ...}
            if res_json.get("status") != "success":
                err_msg = res_json.get("message") or res_json.get("error") or "Unknown ZapOTP API error"
                logger.warning(f"ZapOTP API Error response from {endpoint}: {err_msg}")
                raise ZapOTPError(err_msg)
                
            return res_json
        except requests.exceptions.Timeout:
            logger.error(f"ZapOTP Timeout requesting {url}")
            raise ZapOTPError("ZapOTP provider request timed out. Please try again.")
        except requests.exceptions.HTTPError as e:
            try:
                err_payload = response.json()
                msg = err_payload.get("message") or err_payload.get("error") or str(e)
            except Exception:
                msg = str(e)
            logger.error(f"ZapOTP HTTP Error on {url}: {msg}")
            raise ZapOTPError(f"ZapOTP Error: {msg}")
        except requests.exceptions.RequestException as e:
            logger.error(f"ZapOTP Network Connection Error: {str(e)}")
            raise ZapOTPError("Unable to connect to ZapOTP verification service.")

    def get_balance(self) -> Dict[str, Any]:
        """
        Fetch upstream ZapOTP balance & account info.
        Endpoint: GET /user
        Returns: {"username": str, "balance": Decimal, "currency": "NGN"}
        """
        res = self._request("GET", "user")
        data = res.get("data", {})
        
        balance_val = Decimal(str(data.get("balance", 0.00)))
        
        # Update cached balance in setting model
        try:
            setting = OTPProviderSetting.get_settings()
            setting.cached_balance = balance_val
            setting.last_balance_check = timezone.now()
            setting.save(update_fields=['cached_balance', 'last_balance_check'])
        except Exception as e:
            logger.warning(f"Failed to update cached OTPProviderSetting balance: {e}")

        return {
            "username": data.get("username", ""),
            "balance": balance_val,
            "currency": data.get("currency", "NGN")
        }

    def get_services(self, country: str = "US", provider: str = "global", service: Optional[str] = None) -> List[Dict[str, Any]]:
        """
        Fetch available services and wholesale prices for a given country.
        Endpoint: GET /services?country={ISO}&provider={provider}&service={service}
        Caches in Redis for 10 minutes to avoid hitting rate limits.
        """
        cache_key = f"zapotp_services_{country.upper()}_{provider}_{service or 'all'}"
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        params = {
            "country": country.upper(),
            "provider": provider
        }
        if service:
            params["service"] = service

        res = self._request("GET", "services", params=params)
        data = res.get("data", [])
        
        # Ensure data is a list
        services_list = data if isinstance(data, list) else [data] if data else []
        
        # Cache for 10 minutes (600s)
        cache.set(cache_key, services_list, timeout=600)
        return services_list

    def rent_number(self, country: str, service: str, provider: str = "global", pool: Optional[str] = None) -> Dict[str, Any]:
        """
        Rent a short-term virtual number for single SMS verification.
        Endpoint: POST /rent
        Payload: {
            "action": "rent",
            "service": "whatsapp",
            "country": "US",
            "provider": "global",
            "duration": "generic"
        }
        Returns:
            {"order_id": str, "number": str, "price": Decimal, "expiry": str}
        """
        payload = {
            "action": "rent",
            "service": service,
            "country": country.upper(),
            "provider": provider,
            "duration": "generic"
        }
        if pool:
            payload["pool"] = pool

        res = self._request("POST", "rent", json_data=payload)
        data = res.get("data", {})
        
        if not data.get("number") or not data.get("order_id"):
            raise ZapOTPError("ZapOTP did not return a valid phone number or order ID.")

        raw_price = data.get("price") or data.get("cost") or data.get("amount") or data.get("rate")
        parsed_price = None
        if raw_price is not None:
            try:
                p_dec = Decimal(str(raw_price))
                if p_dec > Decimal('0.00'):
                    parsed_price = p_dec
            except Exception:
                pass

        return {
            "order_id": str(data.get("order_id")),
            "number": str(data.get("number")),
            "price": parsed_price,
            "expiry": data.get("expiry"),
        }

    def rent_long_number(self, service: str, country: str = "US", days: int = 3, provider: str = "usa_long", area: Optional[str] = None) -> Dict[str, Any]:
        """
        Rent a long-term virtual number (3 to 30 days).
        Endpoint: POST /rent
        Payload: {
            "action": "rent",
            "provider": "usa_long",
            "service": "ALL_SERVICES",
            "days": "3",
            "country": "US"
        }
        """
        payload = {
            "action": "rent",
            "provider": provider,
            "service": service or "ALL_SERVICES",
            "days": str(days),
            "country": country.upper()
        }
        if area:
            payload["area"] = str(area)

        res = self._request("POST", "rent", json_data=payload)
        data = res.get("data", {})
        
        if not data.get("number") or not data.get("order_id"):
            raise ZapOTPError("ZapOTP did not return a valid long-term phone number.")

        return {
            "order_id": str(data.get("order_id")),
            "number": str(data.get("number")),
            "price": Decimal(str(data.get("price", 0.00))),
            "expiry": data.get("expiry"),
            "days": days
        }

    def get_sms(self, order_id: str) -> Dict[str, Any]:
        """
        Poll status and incoming SMS for a specific rented order.
        Endpoint: GET /sms?order_id={id}
        Status values: 'PENDING', 'RECEIVED', 'CANCELED', 'FINISHED'
        Returns:
            {
                "order_id": str,
                "sms_code": Optional[str],
                "full_sms": Optional[str],
                "status": str ("PENDING" | "RECEIVED" | "CANCELED" | "FINISHED")
            }
        """
        # Throttle cache to prevent overwhelming ZapOTP during rapid frontend polling
        cache_key = f"zapotp_sms_poll_{order_id}"
        cached_result = cache.get(cache_key)
        if cached_result:
            return cached_result

        params = {"order_id": str(order_id)}
        res = self._request("GET", "sms", params=params)
        data = res.get("data", {})

        status = data.get("status", "PENDING").upper()
        sms_code = data.get("sms_code") or data.get("code") or None
        full_sms = data.get("sms") or data.get("full_sms") or data.get("message") or ""

        result = {
            "order_id": str(order_id),
            "status": status,
            "sms_code": sms_code,
            "full_sms": full_sms,
        }

        # Cache for 2.5 seconds during pending, or 10 minutes if finalized
        cache_ttl = 600 if status in ["RECEIVED", "CANCELED", "FINISHED"] else 3
        cache.set(cache_key, result, timeout=cache_ttl)

        return result

    def cancel_order(self, order_id: str) -> Dict[str, Any]:
        """
        Notify ZapOTP to cancel/release the virtual number early.
        """
        try:
            return self._request("POST", "rent", json_data={"action": "cancel", "order_id": str(order_id)})
        except Exception as e:
            logger.info(f"ZapOTP upstream cancel notification note: {e}")
            return {"status": "success", "note": "Upstream cancellation notification dispatched"}
