"""
Django Admin configuration for Caryvn.
"""
from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from .models import (
    User, Wallet, Transaction, ServiceCategory, Service,
    MarkupRule, Order, Ticket, TicketReply, APILog
)


@admin.register(User)
class UserAdmin(BaseUserAdmin):
    list_display = ('email', 'username', 'first_name', 'last_name', 'is_active', 'is_staff', 'date_joined')
    list_filter = ('is_active', 'is_staff', 'is_superuser', 'date_joined')
    search_fields = ('email', 'username', 'first_name', 'last_name')
    ordering = ('-date_joined',)
    
    fieldsets = (
        (None, {'fields': ('email', 'password')}),
        ('Personal Info', {'fields': ('first_name', 'last_name', 'username')}),
        ('API', {'fields': ('api_key',)}),
        ('Permissions', {'fields': ('is_active', 'is_staff', 'is_superuser', 'is_verified', 'groups', 'user_permissions')}),
        ('Important dates', {'fields': ('last_login', 'date_joined')}),
    )
    
    add_fieldsets = (
        (None, {
            'classes': ('wide',),
            'fields': ('email', 'password1', 'password2'),
        }),
    )


class WalletInline(admin.TabularInline):
    model = Wallet
    extra = 0
    readonly_fields = ('balance', 'currency', 'created_at', 'updated_at')


@admin.register(Wallet)
class WalletAdmin(admin.ModelAdmin):
    list_display = ('user', 'balance', 'currency', 'updated_at')
    search_fields = ('user__email',)
    readonly_fields = ('created_at', 'updated_at')


@admin.register(Transaction)
class TransactionAdmin(admin.ModelAdmin):
    list_display = ('wallet', 'type', 'amount', 'balance_after', 'created_at')
    list_filter = ('type', 'created_at')
    search_fields = ('wallet__user__email', 'description', 'reference')
    readonly_fields = ('created_at',)


@admin.register(ServiceCategory)
class ServiceCategoryAdmin(admin.ModelAdmin):
    list_display = ('name', 'platform', 'sort_order', 'is_active')
    list_filter = ('platform', 'is_active')
    search_fields = ('name',)
    prepopulated_fields = {'slug': ('name',)}


@admin.register(Service)
class ServiceAdmin(admin.ModelAdmin):
    list_display = ('provider_id', 'name', 'category_name', 'provider_rate', 'user_rate', 'is_active', 'is_featured')
    list_filter = ('is_active', 'is_featured', 'has_refill', 'has_cancel', 'category_name')
    search_fields = ('name', 'provider_id')
    readonly_fields = ('last_synced', 'created_at')


@admin.register(MarkupRule)
class MarkupRuleAdmin(admin.ModelAdmin):
    list_display = ('name', 'level', 'percentage', 'fixed_addition', 'priority', 'is_active')
    list_filter = ('level', 'is_active')
    search_fields = ('name',)


@admin.register(Order)
class OrderAdmin(admin.ModelAdmin):
    list_display = ('id_short', 'user', 'service', 'quantity', 'charge', 'profit', 'status', 'provider_order_id', 'created_at')
    list_filter = ('status', 'created_at')
    search_fields = ('user__email', 'link', 'provider_order_id')
    readonly_fields = ('created_at', 'completed_at', 'status_updated_at', 'error_info')
    actions = ['cancel_and_refund', 'retry_with_provider', 'check_provider_status']
    
    fieldsets = (
        ('Order Info', {'fields': ('user', 'service', 'link', 'quantity')}),
        ('Provider', {'fields': ('provider_order_id', 'start_count', 'remains', 'error_info')}),
        ('Pricing', {'fields': ('provider_rate', 'user_rate', 'charge', 'profit', 'currency')}),
        ('Status', {'fields': ('status', 'status_updated_at', 'completed_at')}),
        ('Timestamps', {'fields': ('created_at',)}),
    )
    
    def id_short(self, obj):
        return str(obj.id)[:8]
    id_short.short_description = 'ID'
    
    def error_info(self, obj):
        if not obj.provider_order_id and obj.status in ('pending', 'failed'):
            return '⚠️ No provider order ID — order was never submitted or provider rejected it.'
        return '✅ OK'
    error_info.short_description = 'Provider Status'
    
    @admin.action(description='🔄 Cancel selected orders & refund wallet')
    def cancel_and_refund(self, request, queryset):
        refunded = 0
        skipped = 0
        for order in queryset:
            if order.status in ('completed', 'canceled', 'refunded'):
                skipped += 1
                continue
            try:
                wallet = order.user.wallet
                wallet.refund(order.charge, f'Admin refund: Order #{str(order.id)[:8]}')
                order.status = Order.Status.CANCELED
                order.save()
                refunded += 1
            except Exception as e:
                self.message_user(request, f'Failed to refund order {str(order.id)[:8]}: {e}', level='error')
        self.message_user(request, f'✅ Refunded {refunded} order(s), skipped {skipped} (already completed/canceled).')
    
    @admin.action(description='🔁 Retry failed orders with provider')
    def retry_with_provider(self, request, queryset):
        from .services.smm_provider import smm_provider, SMMProviderError
        retried = 0
        failed = 0
        for order in queryset.filter(provider_order_id='', status__in=('pending', 'failed')):
            try:
                result = smm_provider.create_order(
                    service_id=order.service.provider_id,
                    link=order.link,
                    quantity=order.quantity,
                    user=order.user,
                    order=order,
                )
                if 'order' in result:
                    order.provider_order_id = str(result['order'])
                    order.status = Order.Status.PROCESSING
                    order.save()
                    retried += 1
                else:
                    self.message_user(request, f'❌ Order #{str(order.id)[:8]}: {result.get("error", "Unknown error")}', level='error')
                    failed += 1
            except SMMProviderError as e:
                self.message_user(request, f'❌ Order #{str(order.id)[:8]}: {e}', level='error')
                failed += 1
        self.message_user(request, f'✅ Retried {retried} order(s), {failed} failed.')
    
    @admin.action(description='📊 Check order status from provider')
    def check_provider_status(self, request, queryset):
        from .services.smm_provider import smm_provider, SMMProviderError
        updated = 0
        for order in queryset.exclude(provider_order_id=''):
            try:
                result = smm_provider.get_order_status(order.provider_order_id, user=order.user, order=order)
                if 'status' in result:
                    provider_status = result['status'].lower()
                    status_map = {
                        'pending': Order.Status.PENDING,
                        'processing': Order.Status.PROCESSING,
                        'in progress': Order.Status.IN_PROGRESS,
                        'completed': Order.Status.COMPLETED,
                        'partial': Order.Status.PARTIAL,
                        'canceled': Order.Status.CANCELED,
                        'cancelled': Order.Status.CANCELED,
                        'refunded': Order.Status.REFUNDED,
                    }
                    new_status = status_map.get(provider_status)
                    if new_status and order.status != new_status:
                        order.status = new_status
                        if 'remains' in result:
                            order.remains = int(result['remains'])
                        if 'start_count' in result:
                            order.start_count = int(result['start_count'])
                        order.save()
                        updated += 1
            except SMMProviderError as e:
                self.message_user(request, f'❌ Order #{str(order.id)[:8]}: {e}', level='error')
        self.message_user(request, f'✅ Updated {updated} order(s) from provider.')


@admin.register(Ticket)
class TicketAdmin(admin.ModelAdmin):
    list_display = ('subject', 'user', 'status', 'priority', 'created_at')
    list_filter = ('status', 'priority', 'created_at')
    search_fields = ('subject', 'user__email', 'message')


@admin.register(TicketReply)
class TicketReplyAdmin(admin.ModelAdmin):
    list_display = ('ticket', 'user', 'is_admin', 'created_at')
    list_filter = ('is_admin', 'created_at')


@admin.register(APILog)
class APILogAdmin(admin.ModelAdmin):
    list_display = ('action', 'response_code', 'duration_ms', 'user', 'created_at')
    list_filter = ('action', 'response_code', 'created_at')
    search_fields = ('user__email',)
    readonly_fields = ('created_at',)
