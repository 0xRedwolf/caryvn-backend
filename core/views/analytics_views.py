"""
Analytics views for Caryvn admin dashboard.
Provides aggregated data for revenue, user growth, popular services, and order stats.
"""
from datetime import timedelta
from decimal import Decimal
from django.db.models import Sum, Count, Avg, Q, F
from django.db.models.functions import TruncDate
from django.utils import timezone
from rest_framework import permissions, status
from rest_framework.response import Response
from rest_framework.views import APIView
from django.contrib.auth import get_user_model

from core.models import Order, Transaction, Service

User = get_user_model()


class IsAdminUser(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user and request.user.is_staff


class AdminAnalyticsView(APIView):
    """Admin analytics endpoint with aggregated dashboard data."""
    permission_classes = [permissions.IsAuthenticated, IsAdminUser]

    def get(self, request):
        now = timezone.now()

        # Date range — default 30 days, override via ?days=N
        try:
            days = int(request.query_params.get('days', 30))
            if days not in (7, 14, 30, 90, 365):
                days = 30
        except (TypeError, ValueError):
            days = 30

        window_start = now - timedelta(days=days)
        seven_days_ago = now - timedelta(days=7)

        # --- Revenue data (selected window, daily breakdown) ---
        revenue_daily = (
            Order.objects
            .filter(created_at__gte=window_start)
            .exclude(status__in=[Order.Status.CANCELED, Order.Status.REFUNDED, Order.Status.FAILED])
            .annotate(date=TruncDate('created_at'))
            .values('date')
            .annotate(
                revenue=Sum('charge'),
                profit=Sum('profit'),
                count=Count('id'),
            )
            .order_by('date')
        )

        revenue_data = [
            {
                'date': item['date'].isoformat(),
                'revenue': float(item['revenue'] or 0),
                'profit': float(item['profit'] or 0),
                'orders': item['count'],
            }
            for item in revenue_daily
        ]

        # --- User growth (selected window, daily breakdown) ---
        user_growth = (
            User.objects
            .filter(date_joined__gte=window_start)
            .annotate(date=TruncDate('date_joined'))
            .values('date')
            .annotate(count=Count('id'))
            .order_by('date')
        )

        user_data = [
            {
                'date': item['date'].isoformat(),
                'users': item['count'],
            }
            for item in user_growth
        ]

        # --- Popular services (top 10 by order count in window) ---
        popular_services = (
            Order.objects
            .filter(created_at__gte=window_start)
            .values('service__name', 'service__category_name')
            .annotate(
                order_count=Count('id'),
                total_revenue=Sum('charge'),
                total_profit=Sum('profit'),
            )
            .order_by('-order_count')[:10]
        )

        services_data = [
            {
                'name': item['service__name'] or 'Unknown',
                'platform': item['service__category_name'] or '',
                'orders': item['order_count'],
                'revenue': float(item['total_revenue'] or 0),
                'profit': float(item['total_profit'] or 0),
            }
            for item in popular_services
        ]

        # --- Order stats (scoped to window) ---
        windowed_orders = Order.objects.filter(created_at__gte=window_start)
        all_orders = Order.objects.all()  # kept for all-time totals
        total_orders = windowed_orders.count()

        order_status_breakdown = (
            windowed_orders
            .values('status')
            .annotate(count=Count('id'))
        )

        status_data = {
            item['status']: item['count']
            for item in order_status_breakdown
        }

        completed_count = status_data.get('completed', 0) + status_data.get('partial', 0)
        completion_rate = round((completed_count / total_orders * 100), 1) if total_orders > 0 else 0

        avg_order_value = windowed_orders.exclude(
            status__in=['canceled', 'cancelled', 'refunded', 'failed']
        ).aggregate(avg=Avg('charge'))['avg'] or 0

        # --- Summary cards (windowed) ---
        total_revenue = windowed_orders.exclude(
            status__in=['canceled', 'cancelled', 'refunded', 'failed']
        ).aggregate(total=Sum('charge'))['total'] or Decimal('0')

        total_profit = windowed_orders.exclude(
            status__in=['canceled', 'cancelled', 'refunded', 'failed']
        ).aggregate(total=Sum('profit'))['total'] or Decimal('0')

        total_users = User.objects.count()
        new_users_7d = User.objects.filter(date_joined__gte=seven_days_ago).count()

        # --- Web vs API order split (windowed) ---
        web_orders = windowed_orders.filter(source='web').count()
        api_orders = windowed_orders.filter(source='api').count()

        web_revenue = (
            windowed_orders
            .filter(source='web')
            .exclude(status__in=['canceled', 'refunded', 'failed'])
            .aggregate(total=Sum('charge'))['total'] or Decimal('0')
        )
        api_revenue = (
            windowed_orders
            .filter(source='api')
            .exclude(status__in=['canceled', 'refunded', 'failed'])
            .aggregate(total=Sum('charge'))['total'] or Decimal('0')
        )
        
        # Revenue current window vs previous equal window for trend
        prev_window_start = window_start - timedelta(days=days)
        revenue_current = (
            Order.objects
            .filter(created_at__gte=window_start)
            .exclude(status__in=['canceled', 'cancelled', 'refunded', 'failed'])
            .aggregate(total=Sum('charge'))['total'] or Decimal('0')
        )
        revenue_prev = (
            Order.objects
            .filter(created_at__gte=prev_window_start, created_at__lt=window_start)
            .exclude(status__in=['canceled', 'cancelled', 'refunded', 'failed'])
            .aggregate(total=Sum('charge'))['total'] or Decimal('0')
        )

        revenue_trend = 0
        if revenue_prev > 0:
            revenue_trend = round(float((revenue_current - revenue_prev) / revenue_prev * 100), 1)
        elif revenue_current > 0:
            revenue_trend = 100.0

        # Active orders (processing or in-progress) — always real-time, not windowed
        active_orders = all_orders.filter(
            status__in=[Order.Status.PROCESSING, Order.Status.PENDING]
        ).count()

        # --- Wallet stats ---
        total_deposits = (
            Transaction.objects
            .filter(type='deposit', status='success')
            .aggregate(total=Sum('amount'))['total'] or Decimal('0')
        )

        # --- Revenue & Profit by Provider ---
        revenue_by_provider_query = (
            all_orders.exclude(status__in=[Order.Status.CANCELED, Order.Status.REFUNDED, Order.Status.FAILED])
            .values('provider__name')
            .annotate(
                total_revenue=Sum('charge'),
                total_profit=Sum('profit'),
                order_count=Count('id')
            )
            .order_by('-total_revenue')
        )
        
        revenue_by_provider = [
            {
                'provider': item['provider__name'] or 'Unknown',
                'revenue': float(item['total_revenue'] or 0),
                'profit': float(item['total_profit'] or 0),
                'orders': item['order_count'],
            }
            for item in revenue_by_provider_query
        ]

        # --- Payment Gateway Performance Funnel (windowed) ---
        gateway_txs = (
            Transaction.objects
            .filter(type=Transaction.Type.DEPOSIT, created_at__gte=window_start)
            .values('payment_gateway')
            .annotate(
                total_initiated=Count('id'),
                total_success=Count('id', filter=Q(status=Transaction.Status.SUCCESS)),
                total_failed=Count('id', filter=Q(status=Transaction.Status.FAILED)),
                total_pending=Count('id', filter=Q(status=Transaction.Status.PENDING)),
                completed_volume=Sum('amount', filter=Q(status=Transaction.Status.SUCCESS)),
                total_volume=Sum('amount')
            )
            .order_by('-total_volume')
        )

        gateway_names = {
            'squad': 'Squad (Card & Virtual Bank)',
            'nexapay': 'NexaPay (Dedicated Virtual Account)',
            'manual': 'Manual Bank Transfer',
            'binance_pay': 'Binance Pay Direct',
            'on_chain_usdt_trc20': 'USDT (TRC-20)',
            'on_chain_usdt_bep20': 'USDT (BEP-20)',
            'on_chain_sol': 'Solana (SOL)',
        }

        gateway_performance = []
        for gw in gateway_txs:
            code = gw['payment_gateway'] or 'unknown'
            init_count = gw['total_initiated']
            succ_count = gw['total_success']
            conv_rate = round((succ_count / init_count * 100), 1) if init_count > 0 else 0
            gateway_performance.append({
                'gateway': code,
                'name': gateway_names.get(code, code.replace('_', ' ').title()),
                'initiated': init_count,
                'completed': succ_count,
                'failed': gw['total_failed'],
                'pending': gw['total_pending'],
                'volume': float(gw['completed_volume'] or 0),
                'total_volume': float(gw['total_volume'] or 0),
                'conversion_rate': conv_rate,
            })

        # --- VIP Customer Leaderboard (Top 10 Spenders in window) ---
        top_customers_query = (
            windowed_orders.exclude(status__in=['canceled', 'cancelled', 'refunded', 'failed'])
            .values('user__id', 'user__email', 'user__first_name', 'user__username')
            .annotate(
                order_count=Count('id'),
                total_spend=Sum('charge'),
                total_profit=Sum('profit')
            )
            .order_by('-total_spend')[:10]
        )

        top_customers = [
            {
                'id': str(c['user__id']),
                'email': c['user__email'],
                'name': c['user__first_name'] or c['user__username'] or c['user__email'].split('@')[0],
                'orders': c['order_count'],
                'total_spend': float(c['total_spend'] or 0),
                'total_profit': float(c['total_profit'] or 0),
            }
            for c in top_customers_query
        ]

        # --- Platform Margins & Volume breakdown ---
        platform_margins_query = (
            windowed_orders.exclude(status__in=['canceled', 'cancelled', 'refunded', 'failed'])
            .values('service__category__platform')
            .annotate(
                revenue=Sum('charge'),
                profit=Sum('profit'),
                orders=Count('id')
            )
            .order_by('-revenue')
        )

        platform_margins = []
        for p in platform_margins_query:
            pname = p['service__category__platform']
            if not pname:
                continue
            rev = float(p['revenue'] or 0)
            prof = float(p['profit'] or 0)
            margin = round((prof / rev * 100), 1) if rev > 0 else 0
            platform_margins.append({
                'platform': pname,
                'revenue': rev,
                'profit': prof,
                'orders': p['orders'],
                'margin_rate': margin,
            })

        # --- High Refund Rate Services (Quality Telemetry) ---
        service_refund_query = (
            windowed_orders
            .values('service__name', 'service__category_name')
            .annotate(
                total_count=Count('id'),
                refund_count=Count('id', filter=Q(status__in=['canceled', 'cancelled', 'refunded'])),
            )
            .filter(total_count__gte=2)
            .order_by('-refund_count')[:6]
        )

        service_refund_rates = []
        for s in service_refund_query:
            tot = s['total_count']
            ref = s['refund_count']
            rate = round((ref / tot * 100), 1) if tot > 0 else 0
            service_refund_rates.append({
                'service_name': s['service__name'] or 'Unknown',
                'category': s['service__category_name'] or '',
                'total_orders': tot,
                'refunded_orders': ref,
                'refund_rate': rate,
            })

        # --- Provider Spend Velocity & Runway Forecast ---
        from core.models import Provider
        provider_runway = []
        for prov in Provider.objects.filter(is_active=True):
            prov_orders_7d = Order.objects.filter(
                provider=prov,
                created_at__gte=seven_days_ago
            ).exclude(status__in=['canceled', 'cancelled', 'refunded', 'failed'])

            prov_orders_count = prov_orders_7d.count()
            prov_spend_7d = prov_orders_7d.aggregate(total=Sum('charge'))['total'] or Decimal('0')
            daily_burn = float(prov_spend_7d) / 7.0

            provider_runway.append({
                'provider_id': prov.id,
                'name': prov.name,
                'currency': prov.currency,
                'orders_7d': prov_orders_count,
                'spend_7d_ngn': float(prov_spend_7d),
                'daily_burn_ngn': round(daily_burn, 2),
            })

        return Response({
            'days': days,  # Echo back selected range so frontend can confirm
            'summary': {
                'total_revenue': float(total_revenue),
                'total_profit': float(total_profit),
                'total_users': total_users,
                'total_orders': total_orders,
                'active_orders': active_orders,
                'new_users_7d': new_users_7d,
                'revenue_trend': revenue_trend,
                'completion_rate': completion_rate,
                'avg_order_value': round(float(avg_order_value), 2),
                'total_deposits': float(total_deposits),
                # Order source breakdown
                'web_orders': web_orders,
                'api_orders': api_orders,
                'web_revenue': float(web_revenue),
                'api_revenue': float(api_revenue),
            },
            'revenue_chart': revenue_data,
            'user_growth_chart': user_data,
            'popular_services': services_data,
            'order_status': status_data,
            'revenue_by_provider': revenue_by_provider,
            'source_breakdown': [
                {'source': 'Web', 'orders': web_orders, 'revenue': float(web_revenue)},
                {'source': 'API', 'orders': api_orders, 'revenue': float(api_revenue)},
            ],
            'gateway_performance': gateway_performance,
            'top_customers': top_customers,
            'platform_margins': platform_margins,
            'service_refund_rates': service_refund_rates,
            'provider_runway': provider_runway,
        })
