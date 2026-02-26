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
            provider_rate: Base rate from provider (per 1000)
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
        
        applied_percentage = Decimal('0')
        applied_fixed = Decimal('0')
        
        for rule in rules:
            if rule.level == MarkupRule.Level.SERVICE and service:
                if rule.service_id == service.id:
                    # Service-specific takes precedence - use directly
                    if rule.fixed_addition > 0:
                        return provider_rate + rule.fixed_addition
                    applied_percentage = max(applied_percentage, rule.percentage)
                    
            elif rule.level == MarkupRule.Level.CATEGORY:
                if category and rule.category_id == category.id:
                    applied_percentage = max(applied_percentage, rule.percentage)
                    applied_fixed = max(applied_fixed, rule.fixed_addition)
                    
            elif rule.level == MarkupRule.Level.PLATFORM:
                if platform and rule.platform.lower() == platform.lower():
                    applied_percentage = max(applied_percentage, rule.percentage)
                    applied_fixed = max(applied_fixed, rule.fixed_addition)
                    
            elif rule.level == MarkupRule.Level.GLOBAL:
                # Global applies if nothing else matched
                if applied_percentage == 0:
                    applied_percentage = rule.percentage
                if applied_fixed == 0:
                    applied_fixed = rule.fixed_addition
        
        # Apply markup
        if applied_percentage > 0:
            final_rate = final_rate * (1 + applied_percentage / 100)
        if applied_fixed > 0:
            final_rate = final_rate + applied_fixed
        
        # Round to 4 decimal places
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
    def sync_service_prices(services_data: list) -> int:
        """
        Sync services from provider and apply markup.
        
        Args:
            services_data: List of service dicts from provider
        
        Returns:
            Number of services synced
        """
        count = 0
        synced_provider_ids = []
        
        for svc in services_data:
            provider_id = svc.get('service')
            if not provider_id:
                continue
            
            synced_provider_ids.append(provider_id)
            provider_rate = Decimal(str(svc.get('rate', '0')))
            category_name = svc.get('category', '')
            
            # Calculate user rate with markup
            user_rate = PricingService.calculate_user_rate(
                provider_rate=provider_rate,
                category_name=category_name
            )
            
            # Update or create service
            defaults = {
                'name': svc.get('name', ''),
                'category_name': category_name,
                'provider_rate': provider_rate,
                'user_rate': user_rate,
                'min_quantity': int(svc.get('min', 10)),
                'max_quantity': int(svc.get('max', 10000)),
                'service_type': svc.get('type', 'Default'),
                'has_refill': svc.get('refill', False),
                'has_cancel': svc.get('cancel', False),
                'is_active': True,  # Provider returned it, so it's available
            }
            
            Service.objects.update_or_create(
                provider_id=provider_id,
                defaults=defaults
            )
            
            count += 1
        
        # Auto-deactivate services the provider no longer offers
        if synced_provider_ids:
            stale = Service.objects.exclude(provider_id__in=synced_provider_ids).filter(is_active=True)
            stale_count = stale.count()
            stale.update(is_active=False)
            if stale_count > 0:
                import logging
                logging.getLogger(__name__).info(f'Auto-deactivated {stale_count} services no longer offered by provider')
        
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


# Singleton instance
pricing_service = PricingService()
