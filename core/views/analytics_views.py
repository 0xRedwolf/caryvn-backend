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
        thirty_days_ago = now - timedelta(days=30)
        seven_days_ago = now - timedelta(days=7)

        # --- Revenue data (last 30 days, daily breakdown) ---
        revenue_daily = (
            Order.objects
            .filter(created_at__gte=thirty_days_ago)
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

        # --- User growth (last 30 days, daily breakdown) ---
        user_growth = (
            User.objects
            .filter(date_joined__gte=thirty_days_ago)
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

        # --- Popular services (top 10 by order count) ---
        popular_services = (
            Order.objects
            .filter(created_at__gte=thirty_days_ago)
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

        # --- Order stats ---
        all_orders = Order.objects.all()
        total_orders = all_orders.count()
        
        order_status_breakdown = (
            all_orders
            .values('status')
            .annotate(count=Count('id'))
        )
        
        status_data = {
            item['status']: item['count']
            for item in order_status_breakdown
        }

        completed_count = status_data.get('completed', 0) + status_data.get('partial', 0)
        completion_rate = round((completed_count / total_orders * 100), 1) if total_orders > 0 else 0

        avg_order_value = all_orders.exclude(
            status__in=['canceled', 'cancelled', 'refunded', 'failed']
        ).aggregate(avg=Avg('charge'))['avg'] or 0

        # --- Summary cards ---
        total_revenue = all_orders.exclude(
            status__in=['canceled', 'cancelled', 'refunded', 'failed']
        ).aggregate(total=Sum('charge'))['total'] or Decimal('0')
        
        total_profit = all_orders.exclude(
            status__in=['canceled', 'cancelled', 'refunded', 'failed']
        ).aggregate(total=Sum('profit'))['total'] or Decimal('0')

        total_users = User.objects.count()
        new_users_7d = User.objects.filter(date_joined__gte=seven_days_ago).count()
        
        # Revenue last 7 days vs previous 7 days for trend
        revenue_7d = (
            Order.objects
            .filter(created_at__gte=seven_days_ago)
            .exclude(status__in=['canceled', 'cancelled', 'refunded', 'failed'])
            .aggregate(total=Sum('charge'))['total'] or Decimal('0')
        )
        revenue_prev_7d = (
            Order.objects
            .filter(created_at__gte=seven_days_ago - timedelta(days=7), created_at__lt=seven_days_ago)
            .exclude(status__in=['canceled', 'cancelled', 'refunded', 'failed'])
            .aggregate(total=Sum('charge'))['total'] or Decimal('0')
        )
        
        revenue_trend = 0
        if revenue_prev_7d > 0:
            revenue_trend = round(float((revenue_7d - revenue_prev_7d) / revenue_prev_7d * 100), 1)

        # Active orders (processing or in-progress)
        active_orders = all_orders.filter(
            status__in=[Order.Status.PROCESSING, Order.Status.PENDING]
        ).count()

        # --- Wallet stats ---
        total_deposits = (
            Transaction.objects
            .filter(type='deposit', status='success')
            .aggregate(total=Sum('amount'))['total'] or Decimal('0')
        )

        return Response({
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
            },
            'revenue_chart': revenue_data,
            'user_growth_chart': user_data,
            'popular_services': services_data,
            'order_status': status_data,
        })
