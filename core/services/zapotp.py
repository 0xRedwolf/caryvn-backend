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
        import re

        # Throttle cache to prevent overwhelming ZapOTP during rapid frontend polling
        cache_key = f"zapotp_sms_poll_{order_id}"
        cached_result = cache.get(cache_key)
        if cached_result:
            return cached_result

        params = {"order_id": str(order_id)}
        res = self._request("GET", "sms", params=params)
        data = res.get("data", {})

        status = str(data.get("status", "PENDING")).upper().strip()
        sms_code = data.get("sms_code") or data.get("code") or None
        if sms_code is not None:
            sms_code = str(sms_code).strip()

        full_sms = str(data.get("sms") or data.get("full_sms") or data.get("message") or "").strip()

        # If sms_code is not provided explicitly but full_sms text has numbers, extract OTP
        if not sms_code and full_sms:
            match = re.search(r'\b(\d{3}[-\s]?\d{3}|\d{4,8})\b', full_sms)
            if match:
                sms_code = match.group(1).replace(' ', '')

        # Standardize finished/delivered states: if ZapOTP marks FINISHED or SUCCESS, treat as RECEIVED
        if status in ["FINISHED", "SUCCESS", "COMPLETED"]:
            status = "RECEIVED"

        result = {
            "order_id": str(order_id),
            "status": status,
            "sms_code": sms_code,
            "full_sms": full_sms,
        }

        # Cache for 10 minutes only if finalized with code or canceled, otherwise 3 seconds
        cache_ttl = 600 if ((status == "RECEIVED" and (sms_code or full_sms)) or status in ["CANCELED", "CANCELLED"]) else 3
        cache.set(cache_key, result, timeout=cache_ttl)

        return result

    def cancel_order(self, order_id: str) -> Dict[str, Any]:
        order_str = str(order_id).strip()
        cache.delete(f"zapotp_sms_poll_{order_str}")
        last_err = None

        # Primary documented endpoint: POST /cancel
        try:
            res = self._request("POST", "cancel", json_data={"order_id": order_str})
            if res.get("status") == "success":
                logger.info(f"ZapOTP order {order_str} canceled successfully via /cancel: {res}")
                return res
        except ZapOTPError as e:
            # If ZapOTP responded with business error (e.g. SMS already received), don't waste time on fallback
            if "404" not in str(e):
                logger.warning(f"ZapOTP cancel rejected by upstream business logic: {e}")
                raise e
            last_err = e
            logger.warning(f"ZapOTP POST /cancel endpoint 404, trying /rent fallback...")
        except Exception as e:
            last_err = e
            logger.warning(f"ZapOTP POST /cancel failed for order {order_str}: {e}, trying /rent fallback...")

        # Fallback documented endpoint: POST /rent with action=cancel
        try:
            res = self._request("POST", "rent", json_data={"action": "cancel", "order_id": order_str})
            if res.get("status") == "success":
                logger.info(f"ZapOTP order {order_str} canceled successfully via /rent fallback: {res}")
                return res
        except Exception as e:
            last_err = e
            logger.warning(f"ZapOTP POST /rent fallback failed for order {order_str}: {e}")

        logger.error(f"ZapOTP cancellation failed for order {order_str}: {last_err}")
        raise ZapOTPError(f"Failed to cancel order on ZapOTP: {last_err}")
