"""
Dynamic pricing/markup service for Caryvn.
"""
from decimal import Decimal
from typing import Optional
from django.db.models import Q
from core.models import Service, MarkupRule, ServiceCategory


class PricingService:
    """
    Service for calculating prices with dynamic markup.
    
    Markup is applied in order of priority:
    1. Service-specific markup (highest priority)
    2. Category-specific markup
    3. Platform-wide markup
    4. Global markup (lowest priority)
    """
    
    @staticmethod
    def calculate_user_rate(provider_rate: Decimal, service: Optional[Service] = None,
                            category_name: str = '', platform: str = '') -> Decimal:
        """
        Calculate the user-facing rate with markup applied.
        
        Args:
            provider_rate: Base rate (per 1000) — should already be in NGN
            service: Service object (optional)
            category_name: Category name for category-level markup
            platform: Platform name for platform-level markup
        
        Returns:
            User rate with markup applied
        """
        final_rate = Decimal(str(provider_rate))
        
        # Get applicable markup rules
        rules = MarkupRule.objects.filter(is_active=True).order_by('-priority')
        
        # Find category if service provided
        category = None
        if service and service.category:
            category = service.category
        
        # Determine platform from category_name if not provided
        if not platform and category_name:
            platform = PricingService._detect_platform(category_name)
        
        for rule in rules:
            applies = False
            
            if rule.level == MarkupRule.Level.SERVICE and service:
                if rule.service_id == service.id:
                    applies = True
                    
            elif rule.level == MarkupRule.Level.CATEGORY:
                if category and rule.category_id == category.id:
                    applies = True
                elif category_name and rule.category_name and rule.category_name.lower() == category_name.lower():
                    applies = True
                    
            elif rule.level == MarkupRule.Level.PLATFORM:
                if platform and rule.platform.lower() == platform.lower():
                    applies = True
                    
            elif rule.level == MarkupRule.Level.GLOBAL:
                applies = True
                
            if applies:
                # The highest priority matching rule wins completely.
                if rule.percentage > 0:
                    final_rate = final_rate * (1 + rule.percentage / 100)
                if rule.fixed_addition > 0:
                    final_rate = final_rate + rule.fixed_addition
                
                # Exit immediately since rules are ordered by priority descending
                return final_rate.quantize(Decimal('0.0001'))
        
        return final_rate.quantize(Decimal('0.0001'))
    
    @staticmethod
    def _detect_platform(category_name: str) -> str:
        """Detect platform from category name."""
        category_lower = category_name.lower()
        
        platforms = ['instagram', 'tiktok', 'youtube', 'facebook', 'twitter', 
                     'telegram', 'snapchat', 'linkedin', 'threads', 'spotify']
        
        for platform in platforms:
            if platform in category_lower:
                return platform.capitalize()
        
        return ''
    
    @staticmethod
    def sync_service_prices(services_data: list, provider=None) -> int:
        """
        Sync services from provider and apply markup.
        Scoped to a specific provider so different providers don't interfere.
        
        Args:
            services_data: List of service dicts from provider
            provider: Provider model instance (required for multi-provider)
        
        Returns:
            Number of services synced
        """
        count = 0
        synced_external_ids = []
        
        # Determine exchange rate (1.0 for NGN providers, custom for USD etc.)
        exchange_rate = Decimal('1.00')
        if provider and provider.exchange_rate:
            exchange_rate = provider.exchange_rate
        
        for svc in services_data:
            external_id = svc.get('service')
            if not external_id:
                continue
            
            synced_external_ids.append(external_id)
            provider_rate_raw = Decimal(str(svc.get('rate', '0')))
            category_name = svc.get('category', '')
            
            # Convert provider rate to NGN
            provider_rate_ngn = (provider_rate_raw * exchange_rate).quantize(Decimal('0.0001'))
            
            # Calculate user rate with markup (applied to NGN rate)
            user_rate = PricingService.calculate_user_rate(
                provider_rate=provider_rate_ngn,
                category_name=category_name
            )
            
            # Build lookup kwargs — scope by provider if available
            lookup = {'external_id': external_id}
            if provider:
                lookup['provider'] = provider
            
            # Prepare fields from provider
            update_fields = {
                'name': svc.get('name', ''),
                'category_name': category_name,
                'provider_rate': provider_rate_raw,  # Original rate in provider's currency
                'provider_rate_ngn': provider_rate_ngn,  # Converted to NGN
                'user_rate': user_rate,
                'min_quantity': int(svc.get('min', 10)),
                'max_quantity': int(svc.get('max', 10000)),
                'service_type': svc.get('type', 'Default'),
                'has_refill': svc.get('refill', False),
                'has_cancel': svc.get('cancel', False),
                'provider_is_active': True,  # The provider returned it, so it is active on their end
            }
            
            try:
                # If it exists, update it but DO NOT touch is_active 
                # to preserve admin's manual curation.
                service_obj = Service.objects.get(**lookup)
                for k, v in update_fields.items():
                    setattr(service_obj, k, v)
                service_obj.save()
            except Service.DoesNotExist:
                # If it's a new service, create it disabled by default
                # so the admin can review and manually activate it.
                creation_kwargs = {**lookup, **update_fields, 'is_active': False}
                if provider:
                    creation_kwargs['provider'] = provider
                Service.objects.create(**creation_kwargs)
            
            count += 1
        
        # Auto-deactivate services the provider no longer offers (scoped to this provider only)
        if synced_external_ids:
            stale_qs = Service.objects.exclude(external_id__in=synced_external_ids).filter(provider_is_active=True)
            if provider:
                stale_qs = stale_qs.filter(provider=provider)
            stale_count = stale_qs.count()
            
            # If a service is no longer offered by the provider, mark it as dead upstream 
            # AND force the local `is_active` to False so users don't buy dead services.
            stale_qs.update(provider_is_active=False, is_active=False)
            
            if stale_count > 0:
                import logging
                provider_name = provider.name if provider else 'default'
                logging.getLogger(__name__).info(
                    f'Auto-deactivated {stale_count} services no longer offered by {provider_name}'
                )
        
        return count
    
    @staticmethod
    def calculate_order_profit(provider_rate: Decimal, user_rate: Decimal, 
                               quantity: int) -> Decimal:
        """
        Calculate profit for an order.
        
        Args:
            provider_rate: Rate per 1000 from provider
            user_rate: Rate per 1000 charged to user
            quantity: Order quantity
        
        Returns:
            Profit amount
        """
        provider_cost = (provider_rate / 1000) * quantity
        user_charge = (user_rate / 1000) * quantity
        return (user_charge - provider_cost).quantize(Decimal('0.0001'))

    @staticmethod
    def recalculate_all_service_prices() -> int:
        """
        Recalculate user_rate for all existing services using bulk_update.
        Called when markup rules change.
        
        Returns:
            Number of services whose prices were updated
        """
        services = list(Service.objects.select_related('provider').all())
        to_update = []
        
        for svc in services:
            # Use NGN rate if available, fallback to raw rate (legacy/edge cases)
            base_rate = svc.provider_rate_ngn if svc.provider_rate_ngn is not None else svc.provider_rate
            
            new_user_rate = PricingService.calculate_user_rate(
                provider_rate=base_rate,
                service=svc,
                category_name=svc.category_name
            )
            
            if svc.user_rate != new_user_rate:
                svc.user_rate = new_user_rate
                to_update.append(svc)
        
        if to_update:
            Service.objects.bulk_update(to_update, ['user_rate'])
                
        return len(to_update)


# Singleton instance
pricing_service = PricingService()
