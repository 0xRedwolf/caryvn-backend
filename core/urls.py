"""
URL configuration for core app.
"""
from django.urls import path
from .views import (
    # Auth
    RegisterView, LoginView, LogoutView, UserProfileView,
    ChangePasswordView, GenerateAPIKeyView,
    # Wallet
    WalletView, TransactionListView,
    # Services
    ServiceListView, ServiceDetailView,
    # Orders
    OrderCreateView, OrderListView, OrderDetailView,
    # Tickets
    TicketListCreateView, TicketDetailView,
    # Admin
    AdminDashboardView, AdminUserListView, AdminOrderListView,
    AdminMarkupRuleView, AdminAPILogView, AdminSyncServicesView,
    AdminOrderCancelRefundView, AdminOrderRetryView, AdminOrderCheckStatusView,
    AdminUserToggleActiveView, AdminUserTransactionsView, AdminSyncOrdersView,
)
from .views.payment_views import (
    InitiateTopupView, VerifyTopupView, SquadWebhookView,
)
from .views.analytics_views import AdminAnalyticsView
from .views.activity_views import LogActivityView, AdminUserActivityView
from .views.auth_views import PasswordResetRequestView, PasswordResetConfirmView

urlpatterns = [
    # Auth endpoints
    path('auth/register/', RegisterView.as_view(), name='register'),
    path('auth/login/', LoginView.as_view(), name='login'),
    path('auth/logout/', LogoutView.as_view(), name='logout'),
    path('auth/profile/', UserProfileView.as_view(), name='profile'),
    path('auth/change-password/', ChangePasswordView.as_view(), name='change-password'),
    path('auth/api-key/', GenerateAPIKeyView.as_view(), name='generate-api-key'),
    path('auth/password-reset/', PasswordResetRequestView.as_view(), name='password-reset-request'),
    path('auth/password-reset/confirm/', PasswordResetConfirmView.as_view(), name='password-reset-confirm'),
    
    # Wallet endpoints
    path('wallet/', WalletView.as_view(), name='wallet'),
    path('wallet/transactions/', TransactionListView.as_view(), name='transactions'),
    path('wallet/topup/initiate/', InitiateTopupView.as_view(), name='topup-initiate'),
    path('wallet/topup/verify/', VerifyTopupView.as_view(), name='topup-verify'),
    
    # Payment webhooks (no auth — validated by signature)
    path('payments/squad/webhook/', SquadWebhookView.as_view(), name='squad-webhook'),
    
    # Service endpoints
    path('services/', ServiceListView.as_view(), name='services'),
    path('services/<int:service_id>/', ServiceDetailView.as_view(), name='service-detail'),
    
    # Order endpoints
    path('orders/', OrderListView.as_view(), name='orders'),
    path('orders/create/', OrderCreateView.as_view(), name='order-create'),
    path('orders/<uuid:order_id>/', OrderDetailView.as_view(), name='order-detail'),
    
    # Ticket endpoints
    path('tickets/', TicketListCreateView.as_view(), name='tickets'),
    path('tickets/<uuid:ticket_id>/', TicketDetailView.as_view(), name='ticket-detail'),
    
    # Admin endpoints
    path('admin/dashboard/', AdminDashboardView.as_view(), name='admin-dashboard'),
    path('admin/users/', AdminUserListView.as_view(), name='admin-users'),
    path('admin/orders/', AdminOrderListView.as_view(), name='admin-orders'),
    path('admin/markup-rules/', AdminMarkupRuleView.as_view(), name='admin-markup-rules'),
    path('admin/markup-rules/<int:rule_id>/', AdminMarkupRuleView.as_view(), name='admin-markup-rule-detail'),
    path('admin/logs/', AdminAPILogView.as_view(), name='admin-logs'),
    path('admin/sync-services/', AdminSyncServicesView.as_view(), name='admin-sync-services'),
    path('admin/sync-orders/', AdminSyncOrdersView.as_view(), name='admin-sync-orders'),
    path('admin/analytics/', AdminAnalyticsView.as_view(), name='admin-analytics'),
    
    # Admin action endpoints
    path('admin/orders/cancel-refund/', AdminOrderCancelRefundView.as_view(), name='admin-order-cancel-refund'),
    path('admin/orders/retry/', AdminOrderRetryView.as_view(), name='admin-order-retry'),
    path('admin/orders/check-status/', AdminOrderCheckStatusView.as_view(), name='admin-order-check-status'),
    path('admin/users/<uuid:user_id>/toggle-active/', AdminUserToggleActiveView.as_view(), name='admin-user-toggle-active'),
    path('admin/users/<uuid:user_id>/transactions/', AdminUserTransactionsView.as_view(), name='admin-user-transactions'),
    path('admin/users/<uuid:user_id>/activity/', AdminUserActivityView.as_view(), name='admin-user-activity'),
    
    # Activity tracking
    path('activity/', LogActivityView.as_view(), name='log-activity'),
]

