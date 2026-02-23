"""
API Views for Caryvn.
"""
from decimal import Decimal
from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.throttling import ScopedRateThrottle
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView
from django.contrib.auth import authenticate, get_user_model
from django.db import transaction
from django.utils import timezone

from ..models import (
    Wallet, Transaction, Service, Order, Ticket, TicketReply, 
    MarkupRule, APILog
)
from ..serializers import (
    RegisterSerializer, LoginSerializer, UserSerializer, UserProfileUpdateSerializer,
    ChangePasswordSerializer, WalletSerializer, TransactionSerializer,
    ServiceSerializer, ServiceListSerializer, OrderCreateSerializer,
    OrderSerializer, OrderDetailSerializer, TicketSerializer, TicketListSerializer,
    TicketReplySerializer, TicketReplyCreateSerializer, MarkupRuleSerializer,
    APILogSerializer, AdminOrderSerializer, AdminUserSerializer
)
from ..services.smm_provider import smm_provider, SMMProviderError
from ..services.pricing import pricing_service
from ..services.email_service import email_service
from ..utils import sync_active_orders
import logging

logger = logging.getLogger(__name__)

User = get_user_model()


# === Auth Views ===

class RegisterView(APIView):
    """User registration endpoint."""
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'auth'
    
    def post(self, request):
        serializer = RegisterSerializer(data=request.data)
        if serializer.is_valid():
            user = serializer.save()
            refresh = RefreshToken.for_user(user)
            return Response({
                'user': UserSerializer(user).data,
                'tokens': {
                    'refresh': str(refresh),
                    'access': str(refresh.access_token),
                }
            }, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class LoginView(APIView):
    """User login endpoint."""
    permission_classes = [permissions.AllowAny]
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'auth'
    
    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        if serializer.is_valid():
            login_data = serializer.validated_data['login']
            password = serializer.validated_data['password']
            
            # Try to authenticate with email first, then username
            user = authenticate(email=login_data, password=password)
            if not user:
                # Fallback to username if email authentication fails
                try:
                    user_obj = User.objects.get(username__iexact=login_data)
                    user = authenticate(email=user_obj.email, password=password)
                except User.DoesNotExist:
                    user = None

            if user and user.is_active:
                refresh = RefreshToken.for_user(user)
                user.last_login = timezone.now()
                user.save(update_fields=['last_login'])
                return Response({
                    'user': UserSerializer(user).data,
                    'tokens': {
                        'refresh': str(refresh),
                        'access': str(refresh.access_token),
                    }
                })
            return Response(
                {'error': 'Invalid credentials or inactive account'},
                status=status.HTTP_401_UNAUTHORIZED
            )
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)



class LogoutView(APIView):
    """User logout - blacklist refresh token."""
    
    def post(self, request):
        try:
            refresh_token = request.data.get('refresh')
            if refresh_token:
                token = RefreshToken(refresh_token)
                token.blacklist()
            return Response({'message': 'Successfully logged out'})
        except Exception:
            return Response({'message': 'Logged out'})


class UserProfileView(APIView):
    """User profile management."""
    
    def get(self, request):
        """Get current user profile."""
        return Response(UserSerializer(request.user).data)
    
    def patch(self, request):
        """Update user profile."""
        serializer = UserProfileUpdateSerializer(
            request.user, data=request.data, partial=True
        )
        if serializer.is_valid():
            serializer.save()
            return Response(UserSerializer(request.user).data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class ChangePasswordView(APIView):
    """Change user password."""
    
    def post(self, request):
        serializer = ChangePasswordSerializer(data=request.data)
        if serializer.is_valid():
            if not request.user.check_password(serializer.validated_data['old_password']):
                return Response(
                    {'old_password': 'Incorrect password'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            request.user.set_password(serializer.validated_data['new_password'])
            request.user.save()
            return Response({'message': 'Password changed successfully'})
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class GenerateAPIKeyView(APIView):
    """Generate or regenerate user API key."""
    
    def post(self, request):
        api_key = request.user.generate_api_key()
        return Response({'api_key': api_key})


# === Wallet Views ===

class WalletView(APIView):
    """Get wallet info and transactions."""
    
    def get(self, request):
        wallet = request.user.wallet
        return Response({
            'wallet': WalletSerializer(wallet).data,
            'recent_transactions': TransactionSerializer(
                wallet.transactions.all()[:10], many=True
            ).data
        })


class TransactionListView(APIView):
    """List user transactions with pagination."""
    
    def get(self, request):
        transactions = request.user.wallet.transactions.all()
        # Simple pagination
        limit = int(request.query_params.get('limit', 20))
        offset = int(request.query_params.get('offset', 0))
        return Response({
            'transactions': TransactionSerializer(
                transactions[offset:offset+limit], many=True
            ).data,
            'total': transactions.count()
        })


# === Service Views ===

class ServiceListView(APIView):
    """List available services."""
    permission_classes = [permissions.AllowAny]
    
    def get(self, request):
        # Get services from database (synced via admin endpoint)
        services = Service.objects.filter(is_active=True)
        
        # Filters
        platform = request.query_params.get('platform')
        category = request.query_params.get('category')
        search = request.query_params.get('search')
        featured = request.query_params.get('featured')
        
        if platform:
            services = services.filter(category_name__icontains=platform)
        if category:
            services = services.filter(category_name__icontains=category)
        if search:
            services = services.filter(name__icontains=search)
        if featured:
            services = services.filter(is_featured=True)
        
        return Response({
            'services': ServiceListSerializer(services, many=True).data,
            'count': services.count()
        })


class ServiceDetailView(APIView):
    """Get single service details."""
    permission_classes = [permissions.AllowAny]
    
    def get(self, request, service_id):
        try:
            service = Service.objects.get(provider_id=service_id, is_active=True)
            return Response(ServiceSerializer(service).data)
        except Service.DoesNotExist:
            return Response(
                {'error': 'Service not found'},
                status=status.HTTP_404_NOT_FOUND
            )


# === Order Views ===

class OrderCreateView(APIView):
    """Create a new order."""
    
    @transaction.atomic
    def post(self, request):
        serializer = OrderCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        service = serializer.validated_data['service']
        quantity = serializer.validated_data['quantity']
        link = serializer.validated_data['link']
        comments = request.data.get('comments', '').strip() or None
        
        # Calculate charge
        charge = service.calculate_price(quantity)
        
        # Check balance
        wallet = request.user.wallet
        if wallet.balance < charge:
            return Response(
                {'error': 'Insufficient balance', 'required': str(charge), 'available': str(wallet.balance)},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Create order
        order = Order.objects.create(
            user=request.user,
            service=service,
            link=link,
            quantity=quantity,
            provider_rate=service.provider_rate,
            user_rate=service.user_rate,
            charge=charge,
            status=Order.Status.PENDING
        )
        
        # Calculate and store profit
        order.calculate_profit()
        order.save()
        
        # Deduct from wallet
        wallet.charge(charge, f'Order #{str(order.id)[:8]} - {service.name}')
        
        # Submit to provider
        provider_error = None
        try:
            result = smm_provider.create_order(
                service_id=service.provider_id,
                link=link,
                quantity=quantity,
                comments=comments,
                user=request.user,
                order=order
            )
            
            if 'order' in result:
                order.provider_order_id = str(result['order'])
                order.status = Order.Status.PROCESSING
                order.save()
            elif 'error' in result:
                provider_error = str(result['error'])
        except SMMProviderError as e:
            provider_error = str(e)
        
        # If provider failed, refund the user automatically
        if provider_error:
            wallet.refund(charge, f'Refund - provider failed: Order #{str(order.id)[:8]}')
            order.status = Order.Status.FAILED
            order.save()
            logger.error(f'Order {order.id} failed, auto-refunded ₦{charge}: {provider_error}')
            return Response({
                'order': OrderSerializer(order).data,
                'message': 'Order could not be placed with provider. Your wallet has been refunded.',
                'refunded': True,
            }, status=status.HTTP_201_CREATED)
        
        # Send order confirmation email
        try:
            email_service.send_order_confirmation(request.user, order)
        except Exception as e:
            logger.error(f'Failed to send order confirmation email: {e}')
        
        return Response({
            'order': OrderSerializer(order).data,
            'message': 'Order placed successfully'
        }, status=status.HTTP_201_CREATED)


class OrderListView(APIView):
    """List user orders."""
    
    def get(self, request):
        orders = request.user.orders.all()
        
        # Filters
        status_filter = request.query_params.get('status')
        if status_filter:
            orders = orders.filter(status=status_filter)
        
        # Pagination
        limit = int(request.query_params.get('limit', 20))
        offset = int(request.query_params.get('offset', 0))
        
        return Response({
            'orders': OrderSerializer(orders[offset:offset+limit], many=True).data,
            'total': orders.count()
        })


class OrderDetailView(APIView):
    """Get single order details with status refresh."""
    
    def get(self, request, order_id):
        try:
            order = request.user.orders.get(id=order_id)
        except Order.DoesNotExist:
            return Response(
                {'error': 'Order not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        # Refresh status from provider if order is active
        if order.provider_order_id and order.status in [
            Order.Status.PENDING, Order.Status.PROCESSING, Order.Status.IN_PROGRESS
        ]:
            try:
                status_result = smm_provider.get_order_status(
                    order.provider_order_id,
                    user=request.user,
                    order=order
                )
                if 'status' in status_result:
                    self._update_order_status(order, status_result)
            except SMMProviderError:
                pass
        
        return Response(OrderDetailSerializer(order).data)
    
    def _update_order_status(self, order, status_result):
        """Update order from provider status response."""
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
        
        provider_status = status_result.get('status', '').lower()
        if provider_status in status_map:
            order.status = status_map[provider_status]
        
        if 'start_count' in status_result:
            order.start_count = int(status_result['start_count']) if status_result['start_count'] else None
        if 'remains' in status_result:
            order.remains = int(status_result['remains']) if status_result['remains'] else None
        
        if order.status == Order.Status.COMPLETED:
            order.completed_at = timezone.now()
        
        order.save()


# === Ticket Views ===

class TicketListCreateView(APIView):
    """List and create support tickets."""
    
    def get(self, request):
        tickets = request.user.tickets.all()
        return Response({
            'tickets': TicketListSerializer(tickets, many=True).data,
            'total': tickets.count()
        })
    
    def post(self, request):
        serializer = TicketSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save(user=request.user)
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class TicketDetailView(APIView):
    """Get ticket details and add replies."""
    
    def get(self, request, ticket_id):
        try:
            ticket = request.user.tickets.get(id=ticket_id)
            return Response(TicketSerializer(ticket).data)
        except Ticket.DoesNotExist:
            return Response(
                {'error': 'Ticket not found'},
                status=status.HTTP_404_NOT_FOUND
            )
    
    def post(self, request, ticket_id):
        """Add reply to ticket."""
        try:
            ticket = request.user.tickets.get(id=ticket_id)
        except Ticket.DoesNotExist:
            return Response(
                {'error': 'Ticket not found'},
                status=status.HTTP_404_NOT_FOUND
            )
        
        serializer = TicketReplyCreateSerializer(data=request.data)
        if serializer.is_valid():
            reply = TicketReply.objects.create(
                ticket=ticket,
                user=request.user,
                message=serializer.validated_data['message'],
                is_admin=request.user.is_staff
            )
            if not request.user.is_staff:
                ticket.status = Ticket.Status.PENDING
                ticket.save()
            
            # Send email notification to the other party
            try:
                if request.user.is_staff:
                    # Admin replied → notify the ticket owner
                    email_service.send_ticket_reply(ticket, reply, ticket.user)
                else:
                    # User replied → could notify admin, but skip for now
                    pass
            except Exception as e:
                logger.error(f'Failed to send ticket reply email: {e}')
            
            return Response(TicketReplySerializer(reply).data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# === Admin Views ===

class IsAdminUser(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user and request.user.is_staff


class AdminDashboardView(APIView):
    """Admin dashboard stats."""
    permission_classes = [IsAdminUser]
    
    def get(self, request):
        from django.db.models import Sum, Count
        from django.db.models.functions import TruncDate
        from datetime import timedelta
        
        today = timezone.now().date()
        last_30_days = today - timedelta(days=30)
        
        # Stats
        total_users = User.objects.count()
        active_users_today = User.objects.filter(last_login__date=today).count()
        
        # Order stats - exclude failed/canceled/refunded for financials
        valid_orders = Order.objects.exclude(
            status__in=['canceled', 'cancelled', 'refunded', 'failed']
        )
        
        total_orders = Order.objects.count()
        total_revenue = valid_orders.aggregate(Sum('charge'))['charge__sum'] or 0
        total_profit = valid_orders.aggregate(Sum('profit'))['profit__sum'] or 0
        
        pending_orders = Order.objects.filter(
            status__in=['pending', 'processing', 'in_progress']
        ).count()
        
        # Today's stats
        today_orders = Order.objects.filter(created_at__date=today).count()
        today_metrics = valid_orders.filter(created_at__date=today).aggregate(
            revenue=Sum('charge'),
            profit=Sum('profit')
        )
        
        pending_tickets = Ticket.objects.filter(
            status__in=['open', 'pending']
        ).count()
        
        # Provider balance
        try:
            provider_balance = smm_provider.get_balance()
        except:
            provider_balance = {'balance': 'N/A'}
        
        return Response({
            'total_users': total_users,
            'active_users_today': active_users_today,
            'total_orders': total_orders,
            'pending_orders': pending_orders,
            'total_revenue': str(total_revenue),
            'total_profit': str(total_profit),
            'today_orders': today_orders,
            'today_revenue': str(today_metrics['revenue'] or 0),
            'today_profit': str(today_metrics['profit'] or 0),
            'pending_tickets': pending_tickets,
            'provider_balance': provider_balance.get('balance', 'N/A')
        })


class AdminUserListView(APIView):
    """Admin user management."""
    permission_classes = [IsAdminUser]
    
    def get(self, request):
        users = User.objects.all()
        search = request.query_params.get('search')
        if search:
            users = users.filter(email__icontains=search)
        
        limit = int(request.query_params.get('limit', 20))
        offset = int(request.query_params.get('offset', 0))
        
        return Response({
            'users': AdminUserSerializer(users[offset:offset+limit], many=True).data,
            'total': users.count()
        })


class AdminOrderListView(APIView):
    """Admin order management."""
    permission_classes = [IsAdminUser]
    
    def get(self, request):
        orders = Order.objects.all()
        
        # Filters
        status_filter = request.query_params.get('status')
        user_filter = request.query_params.get('user')
        search = request.query_params.get('search')
        
        if status_filter:
            orders = orders.filter(status=status_filter)
        if user_filter:
            orders = orders.filter(user__email__icontains=user_filter)
        if search:
            orders = orders.filter(link__icontains=search)
        
        limit = int(request.query_params.get('limit', 20))
        offset = int(request.query_params.get('offset', 0))
        
        return Response({
            'orders': AdminOrderSerializer(orders[offset:offset+limit], many=True).data,
            'total': orders.count()
        })


class AdminMarkupRuleView(APIView):
    """Admin markup rule management."""
    permission_classes = [IsAdminUser]
    
    def get(self, request):
        rules = MarkupRule.objects.all()
        return Response(MarkupRuleSerializer(rules, many=True).data)
    
    def post(self, request):
        serializer = MarkupRuleSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    def patch(self, request, rule_id):
        try:
            rule = MarkupRule.objects.get(id=rule_id)
        except MarkupRule.DoesNotExist:
            return Response({'error': 'Rule not found'}, status=status.HTTP_404_NOT_FOUND)
        
        serializer = MarkupRuleSerializer(rule, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    def delete(self, request, rule_id):
        try:
            rule = MarkupRule.objects.get(id=rule_id)
            rule.delete()
            return Response(status=status.HTTP_204_NO_CONTENT)
        except MarkupRule.DoesNotExist:
            return Response({'error': 'Rule not found'}, status=status.HTTP_404_NOT_FOUND)


class AdminAPILogView(APIView):
    """Admin API logs viewer."""
    permission_classes = [IsAdminUser]
    
    def get(self, request):
        logs = APILog.objects.all()
        
        action_filter = request.query_params.get('action')
        if action_filter:
            logs = logs.filter(action=action_filter)
        
        limit = int(request.query_params.get('limit', 50))
        offset = int(request.query_params.get('offset', 0))
        
        return Response({
            'logs': APILogSerializer(logs[offset:offset+limit], many=True).data,
            'total': logs.count()
        })


class AdminSyncServicesView(APIView):
    """Force sync services from provider."""
    permission_classes = [IsAdminUser]
    
    def post(self, request):
        try:
            services = smm_provider.get_services(force_refresh=True)
            count = pricing_service.sync_service_prices(services)
            return Response({
                'message': f'Successfully synced {count} services',
                'count': count
            })
        except SMMProviderError as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_503_SERVICE_UNAVAILABLE
            )


class AdminSyncOrdersView(APIView):
    """Force sync active orders from provider."""
    permission_classes = [IsAdminUser]
    
    def post(self, request):
        try:
            result = sync_active_orders()
            return Response(result)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class AdminOrderCancelRefundView(APIView):
    """Cancel orders and refund wallet balance."""
    permission_classes = [IsAdminUser]
    
    def post(self, request):
        order_ids = request.data.get('order_ids', [])
        if not order_ids:
            return Response({'error': 'No order IDs provided'}, status=status.HTTP_400_BAD_REQUEST)
        
        results = {'refunded': 0, 'skipped': 0, 'errors': []}
        
        for oid in order_ids:
            try:
                order = Order.objects.get(id=oid)
                if order.status in ('completed', 'canceled', 'refunded'):
                    results['skipped'] += 1
                    continue
                wallet = order.user.wallet
                wallet.refund(order.charge, f'Admin refund: Order #{str(order.id)[:8]}')
                order.status = Order.Status.CANCELED
                order.save()
                results['refunded'] += 1
            except Order.DoesNotExist:
                results['errors'].append(f'Order {oid} not found')
            except Exception as e:
                results['errors'].append(f'Order {str(oid)[:8]}: {str(e)}')
        
        return Response(results)


class AdminOrderRetryView(APIView):
    """Retry failed orders with the SMM provider."""
    permission_classes = [IsAdminUser]
    
    def post(self, request):
        order_ids = request.data.get('order_ids', [])
        if not order_ids:
            return Response({'error': 'No order IDs provided'}, status=status.HTTP_400_BAD_REQUEST)
        
        results = {'retried': 0, 'failed': 0, 'errors': []}
        
        for oid in order_ids:
            try:
                order = Order.objects.get(id=oid)
                if order.provider_order_id or order.status not in ('pending', 'failed'):
                    results['errors'].append(f'Order #{str(order.id)[:8]}: already has provider ID or not in retryable state')
                    results['failed'] += 1
                    continue
                
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
                    results['retried'] += 1
                else:
                    results['errors'].append(f'Order #{str(order.id)[:8]}: {result.get("error", "Unknown")}')
                    results['failed'] += 1
            except Order.DoesNotExist:
                results['errors'].append(f'Order {oid} not found')
                results['failed'] += 1
            except SMMProviderError as e:
                results['errors'].append(f'Order #{str(oid)[:8]}: {str(e)}')
                results['failed'] += 1
        
        return Response(results)


class AdminOrderCheckStatusView(APIView):
    """Check order status from provider."""
    permission_classes = [IsAdminUser]
    
    def post(self, request):
        order_ids = request.data.get('order_ids', [])
        if not order_ids:
            return Response({'error': 'No order IDs provided'}, status=status.HTTP_400_BAD_REQUEST)
        
        results = {'updated': 0, 'skipped': 0, 'errors': []}
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
        
        for oid in order_ids:
            try:
                order = Order.objects.get(id=oid)
                if not order.provider_order_id:
                    results['skipped'] += 1
                    continue
                
                result = smm_provider.get_order_status(
                    order.provider_order_id, user=order.user, order=order
                )
                if 'status' in result:
                    provider_status = result['status'].lower()
                    new_status = status_map.get(provider_status)
                    if new_status and order.status != new_status:
                        order.status = new_status
                        if 'remains' in result:
                            order.remains = int(result['remains'])
                        if 'start_count' in result:
                            order.start_count = int(result['start_count'])
                        order.save()
                        results['updated'] += 1
                    else:
                        results['skipped'] += 1
                else:
                    results['skipped'] += 1
            except Order.DoesNotExist:
                results['errors'].append(f'Order {oid} not found')
            except SMMProviderError as e:
                results['errors'].append(f'Order #{str(oid)[:8]}: {str(e)}')
        
        return Response(results)


class AdminUserToggleActiveView(APIView):
    """Toggle user active/inactive status."""
    permission_classes = [IsAdminUser]
    
    def post(self, request, user_id):
        try:
            user = User.objects.get(id=user_id)
            if user == request.user:
                return Response({'error': 'Cannot deactivate yourself'}, status=status.HTTP_400_BAD_REQUEST)
            user.is_active = not user.is_active
            user.save(update_fields=['is_active'])
            return Response({
                'message': f'User {"activated" if user.is_active else "deactivated"}',
                'is_active': user.is_active,
            })
        except User.DoesNotExist:
            return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)


class AdminUserTransactionsView(APIView):
    """View a specific user's transactions."""
    permission_classes = [IsAdminUser]
    
    def get(self, request, user_id):
        try:
            user = User.objects.get(id=user_id)
        except User.DoesNotExist:
            return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)
        
        from ..serializers import TransactionSerializer
        transactions = user.wallet.transactions.all()[:50]
        return Response({
            'user_email': user.email,
            'balance': str(user.wallet.balance),
            'transactions': TransactionSerializer(transactions, many=True).data,
        })

