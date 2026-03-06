"""
API Views for Caryvn.
"""
import logging
from decimal import Decimal

from rest_framework import viewsets, status, permissions
from rest_framework.decorators import action
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.throttling import ScopedRateThrottle
from rest_framework_simplejwt.tokens import RefreshToken
from rest_framework_simplejwt.views import TokenObtainPairView
from django.contrib.auth import authenticate, get_user_model
from django.db import transaction, models
from django.utils import timezone
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import csrf_exempt

from ..models import (
    Wallet, Transaction, Service, Order, Ticket, TicketReply, 
    MarkupRule, APILog, SiteSettings, Provider
)
from ..serializers import (
    RegisterSerializer, LoginSerializer, UserSerializer, UserProfileUpdateSerializer,
    ChangePasswordSerializer, WalletSerializer, TransactionSerializer,
    ServiceSerializer, ServiceListSerializer, OrderCreateSerializer,
    OrderSerializer, OrderDetailSerializer, TicketSerializer, TicketListSerializer,
    TicketReplySerializer, TicketReplyCreateSerializer, MarkupRuleSerializer,
    APILogSerializer, AdminOrderSerializer, AdminUserSerializer
)
from ..services.smm_provider import SMMProviderError, get_provider_client
from ..services.pricing import pricing_service, PricingService
from ..services.email_service import email_service
from ..utils import sync_active_orders

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
                wallet.transactions.all()[:10], many=True, context={'request': request}
            ).data
        })


class TransactionListView(APIView):
    """List user transactions with pagination."""
    
    def get(self, request):
        transactions = request.user.wallet.transactions.filter(hidden_by_user=False)
        # Simple pagination
        limit = int(request.query_params.get('limit', 20))
        offset = int(request.query_params.get('offset', 0))
        return Response({
            'transactions': TransactionSerializer(
                transactions[offset:offset+limit], many=True, context={'request': request}
            ).data,
            'total': transactions.count()
        })


class HideTransactionView(APIView):
    """Hide a transaction from user's view (soft delete)."""
    
    def post(self, request, transaction_id):
        try:
            transaction = request.user.wallet.transactions.get(id=transaction_id)
        except Transaction.DoesNotExist:
            return Response({'error': 'Transaction not found'}, status=404)
        transaction.hidden_by_user = True
        transaction.save()
        return Response({'message': 'Transaction hidden'})


# === Service Views ===

class ServiceListView(APIView):
    """List available services."""
    permission_classes = [permissions.AllowAny]
    
    def get(self, request):
        # Get services from database (synced via admin endpoint)
        # Only include services from active providers
        services = Service.objects.filter(
            provider__is_active=True
        ).select_related('provider')
        
        # Admin can see all services including inactive
        include_inactive = request.query_params.get('include_inactive', 'false').lower() == 'true'
        if not (include_inactive and request.user.is_authenticated and request.user.is_staff):
            # For normal users: show active services, plus inactive ones from providers that allow it
            # BUT always exclude services that the provider no longer offers (provider_is_active=False)
            from django.db.models import Q
            services = services.filter(provider_is_active=True).filter(
                Q(is_active=True) | Q(provider__show_inactive_services=True)
            )
        
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
            service = Service.objects.get(id=service_id, is_active=True)
            return Response(ServiceSerializer(service).data)
        except Service.DoesNotExist:
            return Response(
                {'error': 'Service not found'},
                status=status.HTTP_404_NOT_FOUND
            )


# === Order Views ===

class OrderCreateView(APIView):
    """Create a new order."""
    throttle_classes = [ScopedRateThrottle]
    throttle_scope = 'orders'
    
    @transaction.atomic
    def post(self, request):
        from django.core.cache import cache
        serializer = OrderCreateSerializer(data=request.data)
        if not serializer.is_valid():
            return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
        
        service = serializer.validated_data['service']
        quantity = serializer.validated_data['quantity']
        link = serializer.validated_data['link']
        comments = request.data.get('comments', '').strip() or None
        
        # --- PREVENT DUPLICATE PROCESSING / SPAM ---
        # 1) Concurrency Lock (Cache Check)
        lock_key = f"order_lock_{request.user.id}_{service.id}_{link}"
        if not cache.add(lock_key, 'locked', timeout=60):
            return Response(
                {'error': 'You are clicking too fast. Please wait a moment.'},
                status=status.HTTP_429_TOO_MANY_REQUESTS
            )

        # 2) DB Active Order Duplicate Check
        active_statuses = [Order.Status.PENDING, Order.Status.PROCESSING, Order.Status.IN_PROGRESS]
        has_active_order = Order.objects.filter(
            user=request.user,
            service=service,
            link=link,
            status__in=active_statuses
        ).exists()

        if has_active_order:
            # Drop the lock instantly if they hit the database duplicate rule so they don't get 'clicking too fast' on a different subsequent request
            cache.delete(lock_key)
            return Response(
                {'error': 'You already have an active order for this exact link. Please wait until it completes to avoid sending duplicates to the provider.'},
                status=status.HTTP_400_BAD_REQUEST
            )
        # --- END OF DUPLICATE CHECKS ---
        
        # Calculate charge
        charge = service.calculate_price(quantity)
        
        # Check balance (fresh read from DB, not cached)
        wallet = Wallet.objects.get(user=request.user)
        if wallet.balance < charge:
            return Response(
                {'error': 'Insufficient balance', 'required': str(charge), 'available': str(wallet.balance)},
                status=status.HTTP_400_BAD_REQUEST
            )
        
        # Create order (include provider reference)
        order = Order.objects.create(
            user=request.user,
            service=service,
            provider=service.provider,
            link=link,
            quantity=quantity,
            provider_rate=service.provider_rate,
            provider_rate_ngn=service.provider_rate_ngn, # NEW: Save the converted rate
            user_rate=service.user_rate,
            charge=charge,
            status=Order.Status.PENDING
        )
        
        # Calculate and store profit
        order.calculate_profit()
        order.save()
        
        # Deduct from wallet (uses select_for_update internally for safety)
        wallet.charge(charge, f'Order #{str(order.id)[:8]} - {service.name}')
        
        # Submit to provider (route to correct provider)
        provider_error = None
        try:
            if not service.provider:
                provider_error = 'No provider configured for this service'
            else:
                client = get_provider_client(service.provider)
                result = client.create_order(
                    service_id=service.external_id,
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
        

        # Send order confirmation email (fire-and-forget — never blocks the response)
        try:
            email_service.send_order_confirmation(request.user, order)
        except Exception as e:
            logger.warning(f'Order confirmation email failed (non-critical): {e}')

        return Response({
            'order': OrderSerializer(order).data,
            'message': 'Order placed successfully'
        }, status=status.HTTP_201_CREATED)


class OrderListView(APIView):
    """List user orders."""
    
    def get(self, request):
        orders = request.user.orders.filter(hidden_by_user=False)
        
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


class HideOrderView(APIView):
    """Hide an order from user's view (soft delete)."""
    
    def post(self, request, order_id):
        try:
            order = request.user.orders.get(id=order_id)
        except Order.DoesNotExist:
            return Response({'error': 'Order not found'}, status=404)
        order.hidden_by_user = True
        order.save()
        return Response({'message': 'Order hidden'})


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
        if order.provider_order_id and order.provider and order.status in [
            Order.Status.PENDING, Order.Status.PROCESSING, Order.Status.IN_PROGRESS
        ]:
            try:
                client = get_provider_client(order.provider)
                status_result = client.get_order_status(
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


class OrderRefillView(APIView):
    """User endpoint to request a refill for an eligible order."""
    
    def post(self, request, order_id):
        try:
            order = request.user.orders.get(id=order_id)
        except Order.DoesNotExist:
            return Response({'error': 'Order not found'}, status=status.HTTP_404_NOT_FOUND)
            
        if not order.service or not order.service.has_refill:
            return Response({'error': 'This service does not support refills.'}, status=status.HTTP_400_BAD_REQUEST)
            
        if order.status != Order.Status.COMPLETED:
            return Response({'error': 'Order must be completed to request a refill.'}, status=status.HTTP_400_BAD_REQUEST)
            
        try:
            if not order.provider:
                return Response({'error': 'No provider configured for this order.'}, status=status.HTTP_400_BAD_REQUEST)
            client = get_provider_client(order.provider)
            result = client.create_refill(
                order_id=order.provider_order_id,
                user=request.user,
                order=order
            )
            
            if 'refill' in result:
                return Response({'message': 'Refill requested successfully.', 'refill_id': result['refill']})
            elif 'error' in result:
                return Response({'error': str(result['error'])}, status=status.HTTP_400_BAD_REQUEST)
            else:
                return Response({'error': 'Failed to request refill from provider.'}, status=status.HTTP_400_BAD_REQUEST)
                
        except SMMProviderError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)


class OrderCheckProviderBalanceView(APIView):
    """Silently check if the provider has enough balance to fulfill an order."""
    permission_classes = [permissions.IsAdminUser]
    def post(self, request):
        from decimal import Decimal
        try:
            service_id = request.data.get('service_id')
            quantity = request.data.get('quantity')
            
            if not service_id or quantity is None:
                return Response({'error': 'Missing service_id or quantity'}, status=status.HTTP_400_BAD_REQUEST)
                
            try:
                service = Service.objects.get(id=service_id, is_active=True)
            except Service.DoesNotExist:
                return Response({'error': 'Service not found or inactive'}, status=status.HTTP_404_NOT_FOUND)
            
            if not service.provider:
                return Response({'can_fulfill': False, 'message': 'No provider configured'})
                
            qty = Decimal(str(quantity))
            provider_cost = (qty / Decimal('1000')) * service.provider_rate
            
            client = get_provider_client(service.provider)
            balance_data = client.get_balance()
            if 'error' in balance_data and balance_data['error'] != 'Unknown error':
                return Response({'can_fulfill': False, 'message': 'Provider API Error'})
                
            available_balance = Decimal(str(balance_data.get('balance', '0')))
            
            can_fulfill = available_balance >= provider_cost
            
            return Response({'can_fulfill': can_fulfill})
            
        except Exception as e:
            logger.error(f"Error checking provider balance: {e}")
            return Response({'can_fulfill': False, 'message': 'Internal Server Error'})


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
                    # User replied → notify admin
                    try:
                        admin_email = User.objects.filter(is_staff=True).values_list('email', flat=True).first()
                        if admin_email:
                            email_service.send_email(
                                to=admin_email,
                                subject=f'[Support] New reply on ticket #{ticket.id}',
                                body=(
                                    f'User {request.user.email} replied to ticket: {ticket.subject}\n\n'
                                    f'Message:\n{reply.message}\n\n'
                                    f'Ticket ID: {ticket.id}'
                                ),
                            )
                    except Exception as email_err:
                        logger.warning(f'Could not notify admin of ticket reply: {email_err}')
            except Exception as e:
                logger.error(f'Failed to send ticket reply email: {e}')
            
            return Response(TicketReplySerializer(reply).data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


# === Admin Views ===

class IsAdminUser(permissions.BasePermission):
    def has_permission(self, request, view):
        return request.user and request.user.is_staff


class AdminTicketListView(APIView):
    """Admin endpoint to list all support tickets."""
    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        tickets = Ticket.objects.all().order_by(
            # Open/Pending first, then Answered, then Closed
            models.Case(
                models.When(status='pending', then=0),
                models.When(status='open', then=1),
                models.When(status='answered', then=2),
                models.When(status='closed', then=3),
                default=4,
                output_field=models.IntegerField()
            ),
            '-updated_at'
        )
        return Response({
            'tickets': TicketSerializer(tickets, many=True).data,
            'total': tickets.count()
        })


class AdminPendingTicketsCountView(APIView):
    """Returns the count of currently pending/open support tickets."""
    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        count = Ticket.objects.filter(status__in=['pending', 'open']).count()
        return Response({'count': count})


class AdminTicketDetailView(APIView):
    """Admin endpoint to view, reply to, or close a specific ticket."""
    permission_classes = [permissions.IsAdminUser]

    def get(self, request, ticket_id):
        try:
            ticket = Ticket.objects.get(id=ticket_id)
            return Response(TicketSerializer(ticket).data)
        except Ticket.DoesNotExist:
            return Response({'error': 'Ticket not found'}, status=status.HTTP_404_NOT_FOUND)

    def post(self, request, ticket_id):
        try:
            ticket = Ticket.objects.get(id=ticket_id)
        except Ticket.DoesNotExist:
            return Response({'error': 'Ticket not found'}, status=status.HTTP_404_NOT_FOUND)

        action = request.data.get('action')

        if action == 'close':
            ticket.status = Ticket.Status.CLOSED
            ticket.save()
            return Response(TicketSerializer(ticket).data)

        serializer = TicketReplyCreateSerializer(data=request.data)
        if serializer.is_valid():
            reply = TicketReply.objects.create(
                ticket=ticket,
                user=request.user,
                message=serializer.validated_data['message'],
                is_admin=True
            )
            ticket.status = Ticket.Status.ANSWERED
            ticket.save()

            # Email notification
            try:
                email_service.send_ticket_reply(ticket, reply, ticket.user)
            except Exception as e:
                logger.error(f'Failed to send admin ticket reply email: {e}')

            return Response(TicketReplySerializer(reply).data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class AdminDashboardView(APIView):
    """Admin dashboard stats."""
    permission_classes = [permissions.IsAdminUser]
    
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
        
        # Provider balances (all active providers) — cached for 2 minutes
        from django.core.cache import cache
        provider_balances = {}
        for prov in Provider.objects.filter(is_active=True):
            cache_key = f'provider_balance_{prov.slug}'
            cached = cache.get(cache_key)
            if cached is not None:
                provider_balances[prov.slug] = cached
            else:
                try:
                    client = get_provider_client(prov)
                    bal = client.get_balance()
                    entry = {
                        'name': prov.name,
                        'balance': bal.get('balance', 'N/A'),
                        'currency': prov.currency,
                    }
                except Exception:
                    entry = {
                        'name': prov.name,
                        'balance': 'N/A',
                        'currency': prov.currency,
                    }
                cache.set(cache_key, entry, 120)
                provider_balances[prov.slug] = entry
        
        # Keep legacy field for backwards compat
        first_balance = next(iter(provider_balances.values()), {}).get('balance', 'N/A')
        
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
            'provider_balance': first_balance,
            'provider_balances': provider_balances,
        })


class AdminUserListView(APIView):
    """Admin user management."""
    permission_classes = [permissions.IsAdminUser]
    
    def get(self, request):
        users = User.objects.all()
        search = request.query_params.get('search')
        if search:
            from django.db.models import Q
            users = users.filter(
                Q(email__icontains=search) | Q(username__icontains=search)
            )
        
        limit = int(request.query_params.get('limit', 20))
        offset = int(request.query_params.get('offset', 0))
        
        return Response({
            'users': AdminUserSerializer(users[offset:offset+limit], many=True).data,
            'total': users.count()
        })


class AdminOrderListView(APIView):
    """Admin order management."""
    permission_classes = [permissions.IsAdminUser]
    
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
    permission_classes = [permissions.IsAdminUser]
    
    def get(self, request):
        rules = MarkupRule.objects.all()
        return Response(MarkupRuleSerializer(rules, many=True).data)
    
    def post(self, request):
        serializer = MarkupRuleSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            # Recalculate prices for existing services
            updated_count = PricingService.recalculate_all_service_prices()
            response_data = serializer.data
            response_data['_services_updated'] = updated_count
            return Response(response_data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    def patch(self, request, rule_id):
        try:
            rule = MarkupRule.objects.get(id=rule_id)
        except MarkupRule.DoesNotExist:
            return Response({'error': 'Rule not found'}, status=status.HTTP_404_NOT_FOUND)
        
        serializer = MarkupRuleSerializer(rule, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            # Recalculate prices for existing services
            updated_count = PricingService.recalculate_all_service_prices()
            response_data = serializer.data
            response_data['_services_updated'] = updated_count
            return Response(response_data)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)
    
    def delete(self, request, rule_id):
        try:
            rule = MarkupRule.objects.get(id=rule_id)
            rule.delete()
            # Recalculate prices for existing services
            PricingService.recalculate_all_service_prices()
            return Response(status=status.HTTP_204_NO_CONTENT)
        except MarkupRule.DoesNotExist:
            return Response({'error': 'Rule not found'}, status=status.HTTP_404_NOT_FOUND)


class AdminAPILogView(APIView):
    """Admin API logs viewer."""
    permission_classes = [permissions.IsAdminUser]
    
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



class AdminServiceCategoryNamesView(APIView):
    """Return distinct category_name values for use in markup rule dropdown."""
    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        names = (
            Service.objects.exclude(category_name='')
            .values_list('category_name', flat=True)
            .distinct()
            .order_by('category_name')
        )
        return Response({'categories': sorted(set(names))})


class AdminSyncServicesView(APIView):
    """Force sync services from a specific provider."""
    permission_classes = [permissions.IsAdminUser]
    
    def post(self, request):
        provider_slug = request.data.get('provider_slug')
        if not provider_slug:
            return Response({'error': 'provider_slug is required'}, status=status.HTTP_400_BAD_REQUEST)
        
        try:
            provider = Provider.objects.get(slug=provider_slug)
        except Provider.DoesNotExist:
            return Response({'error': 'Provider not found'}, status=status.HTTP_404_NOT_FOUND)
        
        try:
            client = get_provider_client(provider)
            services = client.get_services(force_refresh=True)
            count = pricing_service.sync_service_prices(services, provider=provider)
            return Response({
                'message': f'Successfully synced {count} services from {provider.name}',
                'count': count,
                'provider': provider.name,
            })
        except SMMProviderError as e:
            return Response(
                {'error': str(e)},
                status=status.HTTP_503_SERVICE_UNAVAILABLE
            )


class AdminSyncOrdersView(APIView):
    """Force sync active orders. Optionally scoped to a provider."""
    permission_classes = [permissions.IsAdminUser]
    
    def post(self, request):
        provider_slug = request.data.get('provider_slug')  # Optional
        try:
            result = sync_active_orders(provider_slug=provider_slug)
            return Response(result)
        except Exception as e:
            return Response({'error': str(e)}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class AdminOrderCancelRefundView(APIView):
    """Cancel orders and refund wallet balance."""
    permission_classes = [permissions.IsAdminUser]
    
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
    permission_classes = [permissions.IsAdminUser]
    
    def post(self, request):
        order_ids = request.data.get('order_ids', [])
        if not order_ids:
            return Response({'error': 'No order IDs provided'}, status=status.HTTP_400_BAD_REQUEST)
        
        results = {'retried': 0, 'failed': 0, 'errors': []}
        
        for oid in order_ids:
            try:
                order = Order.objects.select_related('service', 'provider').get(id=oid)
                if order.provider_order_id or order.status not in ('pending', 'failed'):
                    results['errors'].append(f'Order #{str(order.id)[:8]}: already has provider ID or not in retryable state')
                    results['failed'] += 1
                    continue
                
                if not order.provider:
                    results['errors'].append(f'Order #{str(order.id)[:8]}: no provider configured')
                    results['failed'] += 1
                    continue
                
                client = get_provider_client(order.provider)
                result = client.create_order(
                    service_id=order.service.external_id,
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
    permission_classes = [permissions.IsAdminUser]
    
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
                order = Order.objects.select_related('provider').get(id=oid)
                if not order.provider_order_id:
                    results['skipped'] += 1
                    continue
                
                if not order.provider:
                    results['skipped'] += 1
                    continue
                
                client = get_provider_client(order.provider)
                result = client.get_order_status(
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
    permission_classes = [permissions.IsAdminUser]
    
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


class AdminUserAdjustBalanceView(APIView):
    """Manually adjust a user's wallet balance."""
    permission_classes = [permissions.IsAdminUser]

    def post(self, request, user_id):
        try:
            user = User.objects.get(id=user_id)
            action = request.data.get('action') # 'credit' or 'deduct'
            amount = request.data.get('amount')
            
            if not amount or not action:
                return Response({'error': 'Action and amount are required'}, status=status.HTTP_400_BAD_REQUEST)
                
            try:
                amount = Decimal(str(amount))
                if amount <= 0:
                    raise ValueError
            except (ValueError, TypeError, Decimal.InvalidOperation):
                return Response({'error': 'Invalid amount'}, status=status.HTTP_400_BAD_REQUEST)

            if action == 'credit':
                user.wallet.refund(amount, description=f'Manual admin credit')
            elif action == 'deduct':
                try:
                    user.wallet.charge(amount, description=f'Manual admin deduction')
                except ValueError as e:
                    return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)
            else:
                return Response({'error': 'Invalid action'}, status=status.HTTP_400_BAD_REQUEST)

            return Response({
                'message': f'Successfully {action}ed ₦{amount}',
                'new_balance': str(user.wallet.balance)
            })
        except User.DoesNotExist:
            return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)


class AdminUserTransactionsView(APIView):
    """View a specific user's transactions."""
    permission_classes = [permissions.IsAdminUser]
    
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
            'transactions': TransactionSerializer(transactions, many=True, context={'request': request}).data,
        })


class AdminPendingDepositsView(APIView):
    """Admin endpoint to fetch pending manual deposits with proofs."""
    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        from ..serializers import TransactionSerializer
        # filter by all manual/crypto gateways
        transactions = Transaction.objects.select_related('wallet__user').filter(
            status=Transaction.Status.PENDING,
        ).exclude(
            payment_gateway='squad'
        ).exclude(
            payment_gateway=''
        ).order_by('created_at')

        data = []
        for tx in transactions:
            item = TransactionSerializer(tx, context={'request': request}).data
            item['user_email'] = tx.wallet.user.email
            item['payment_reference'] = tx.payment_reference
            data.append(item)

        return Response(data)


class AdminPendingDepositsCountView(APIView):
    """Admin endpoint to fetch just the count of pending manual deposits."""
    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        count = Transaction.objects.filter(
            status=Transaction.Status.PENDING,
        ).exclude(
            payment_gateway='squad'
        ).exclude(
            payment_gateway=''
        ).count()

        return Response({'count': count})


class AdminVerifyTransactionView(APIView):
    """Admin verifies a pending transaction and credits the user."""
    permission_classes = [permissions.IsAdminUser]
    
    def post(self, request, transaction_id):
        import os
        try:
            transaction = Transaction.objects.get(id=transaction_id)
        except Transaction.DoesNotExist:
            return Response({'error': 'Transaction not found'}, status=status.HTTP_404_NOT_FOUND)
            
        if transaction.status != Transaction.Status.PENDING:
            return Response({'error': f'Transaction is already {transaction.status}'}, status=status.HTTP_400_BAD_REQUEST)
            
        # Non-Squad gateways (manual bank, binance_pay, on_chain_*): approve directly
        is_squad = (transaction.payment_gateway == 'squad' and bool(transaction.payment_reference))
        if not is_squad:
            is_crypto = transaction.payment_gateway in (
                'binance_pay', 'on_chain_usdt_trc20', 'on_chain_usdt_bep20', 'on_chain_sol'
            )

            # For crypto deposits, admin must supply the naira credit amount
            if is_crypto:
                credit_amount_raw = request.data.get('credit_amount')
                if not credit_amount_raw:
                    return Response(
                        {'error': 'credit_amount (naira) is required to approve a crypto deposit'},
                        status=status.HTTP_400_BAD_REQUEST
                    )
                try:
                    credit_amount = Decimal(str(credit_amount_raw))
                    if credit_amount <= 0:
                        raise ValueError
                except Exception:
                    return Response(
                        {'error': 'credit_amount must be a positive number'},
                        status=status.HTTP_400_BAD_REQUEST
                    )

                # Store original USD amount for the response, then overwrite with naira
                original_usd = transaction.amount
                transaction.amount = credit_amount
                transaction.description = (
                    f'{transaction.description} → ₦{credit_amount:,.2f} credited'
                )
                transaction.save(update_fields=['amount', 'description'])
            else:
                original_usd = None
                credit_amount = transaction.amount

            wallet = transaction.wallet
            new_balance = wallet.confirm_deposit(transaction)

            # ── Send top-up success email ──────────────────────────────────
            # For crypto: transaction.amount is already overwritten with naira credit.
            # For bank/manual: transaction.amount is the original naira amount.
            # Either way, credit_amount holds the naira value to display.
            try:
                email_service.send_topup_success(
                    user=wallet.user,
                    amount=credit_amount,
                    new_balance=new_balance,
                )
            except Exception as e:
                logger.warning(f'Topup success email failed (non-critical): {e}')

            # Clear the proof field (it's stored as base64 in the DB, no file to delete)
            if transaction.payment_proof:
                try:
                    transaction.payment_proof = ''
                    transaction.save(update_fields=['payment_proof'])
                except Exception as e:
                    logger.error(f'Failed to clear proof field: {e}')

            response_data = {
                'message': 'Deposit approved and credited successfully',
                'new_balance': str(new_balance),
                'credited_amount': str(credit_amount),
            }
            if is_crypto and original_usd:
                rate = credit_amount / original_usd if original_usd else None
                response_data['rate_used'] = str(rate.quantize(Decimal('0.01'))) if rate else None

            return Response(response_data)

        try:
            from ..services.squad import squad_service, SquadPaymentError
            result = squad_service.verify_payment(transaction.payment_reference)
            if result['success']:
                wallet = transaction.wallet
                new_balance = wallet.confirm_deposit(transaction)
                try:
                    email_service.send_topup_success(
                        user=wallet.user,
                        amount=transaction.amount,
                        new_balance=new_balance,
                    )
                except Exception as e:
                    logger.warning(f'Topup success email failed (non-critical): {e}')
                return Response({'message': 'Verified with Squad and credited successfully', 'new_balance': str(new_balance)})
            else:
                return Response({'error': 'Squad verification failed (not successful)'}, status=status.HTTP_400_BAD_REQUEST)
        except Exception as e:
            return Response({'error': f'Verification failed: {str(e)}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class AdminFailTransactionView(APIView):
    """Admin manually marks a pending transaction as failed."""
    permission_classes = [permissions.IsAdminUser]
    
    def post(self, request, transaction_id):
        import os
        try:
            transaction = Transaction.objects.get(id=transaction_id)
        except Transaction.DoesNotExist:
            return Response({'error': 'Transaction not found'}, status=status.HTTP_404_NOT_FOUND)
            
        if transaction.status != Transaction.Status.PENDING:
            return Response({'error': f'Transaction is already {transaction.status}'}, status=status.HTTP_400_BAD_REQUEST)
            
        wallet = transaction.wallet
        wallet.fail_deposit(transaction)
        
        # Clear the proof field on rejection (base64 stored in DB, no file to delete)
        if transaction.payment_proof:
            try:
                transaction.payment_proof = ''
                transaction.save(update_fields=['payment_proof'])
            except Exception as e:
                logger.error(f'Failed to clear proof field: {e}')
        
        return Response({'message': 'Transaction marked as failed and proof deleted'})


class AdminAllTransactionsView(APIView):
    """Admin view to list all transactions across all users, with filtering."""
    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        qs = Transaction.objects.select_related('wallet__user').order_by('-created_at')

        # Filters
        gateway = request.query_params.get('gateway')
        if gateway:
            qs = qs.filter(payment_gateway=gateway)

        tx_status = request.query_params.get('status')
        if tx_status:
            qs = qs.filter(status=tx_status)

        search = request.query_params.get('search', '').strip()
        if search:
            from django.db.models import Q
            qs = qs.filter(
                Q(wallet__user__email__icontains=search) |
                Q(wallet__user__username__icontains=search) |
                Q(payment_reference__icontains=search)
            )

        # Pagination
        limit = min(int(request.query_params.get('limit', 50)), 200)
        offset = int(request.query_params.get('offset', 0))
        total = qs.count()
        qs = qs[offset:offset + limit]

        data = []
        for tx in qs:
            user = tx.wallet.user
            data.append({
                'id': str(tx.id),
                'user_email': user.email,
                'user_username': user.username,
                'type': tx.type,
                'amount': str(tx.amount),
                'description': tx.description,
                'status': tx.status,
                'payment_gateway': tx.payment_gateway,
                'payment_reference': tx.payment_reference,
                'has_proof': bool(tx.payment_proof),
                'created_at': tx.created_at.isoformat(),
            })

        return Response({'transactions': data, 'total': total, 'limit': limit, 'offset': offset})



class AdminDeleteLogView(APIView):
    """Delete an individual API log entry."""
    permission_classes = [permissions.IsAdminUser]

    def delete(self, request, log_id):
        try:
            log = APILog.objects.get(id=log_id)
            log.delete()
            return Response({'message': 'Log deleted'}, status=status.HTTP_200_OK)
        except APILog.DoesNotExist:
            return Response({'error': 'Log not found'}, status=status.HTTP_404_NOT_FOUND)


class AdminOrderMarkCompletedView(APIView):
    """Manually mark an order as completed."""
    permission_classes = [permissions.IsAdminUser]

    def post(self, request, order_id):
        try:
            order = Order.objects.get(id=order_id)
            if order.status not in [Order.Status.PENDING, Order.Status.PROCESSING]:
                return Response({'error': f'Cannot mark {order.status} order as completed'}, status=status.HTTP_400_BAD_REQUEST)
            
            order.status = Order.Status.COMPLETED
            order.completed_at = timezone.now()
            order.save()
            return Response({'message': 'Order marked as completed'})
        except Order.DoesNotExist:
            return Response({'error': 'Order not found'}, status=status.HTTP_404_NOT_FOUND)


class AdminOrderRefillView(APIView):
    """Admin endpoint to request a refill for a user's order."""
    permission_classes = [permissions.IsAdminUser]
    
    def post(self, request, order_id):
        try:
            order = Order.objects.get(id=order_id)
        except Order.DoesNotExist:
            return Response({'error': 'Order not found'}, status=status.HTTP_404_NOT_FOUND)
            
        if not order.service or not order.service.has_refill:
            return Response({'error': 'This service does not support refills.'}, status=status.HTTP_400_BAD_REQUEST)
            
        if order.status != Order.Status.COMPLETED:
            return Response({'error': 'Order must be completed to request a refill.'}, status=status.HTTP_400_BAD_REQUEST)
            
        try:
            if not order.provider:
                return Response({'error': 'No provider configured for this order.'}, status=status.HTTP_400_BAD_REQUEST)
            client = get_provider_client(order.provider)
            result = client.create_refill(
                order_id=order.provider_order_id,
                user=request.user,
                order=order
            )
            
            if 'refill' in result:
                return Response({'message': 'Refill requested successfully.', 'refill_id': result['refill']})
            elif 'error' in result:
                return Response({'error': str(result['error'])}, status=status.HTTP_400_BAD_REQUEST)
            else:
                return Response({'error': 'Failed to request refill from provider.'}, status=status.HTTP_400_BAD_REQUEST)
                
        except SMMProviderError as e:
            return Response({'error': str(e)}, status=status.HTTP_400_BAD_REQUEST)


class AdminDeleteOrderView(APIView):
    """Permanently delete an order."""
    permission_classes = [permissions.IsAdminUser]

    def delete(self, request, order_id):
        try:
            order = Order.objects.get(id=order_id)
            order.delete()
            return Response({'message': 'Order deleted'}, status=status.HTTP_200_OK)
        except Order.DoesNotExist:
            return Response({'error': 'Order not found'}, status=status.HTTP_404_NOT_FOUND)


class AdminDeleteUserView(APIView):
    """Permanently delete a user and all related data."""
    permission_classes = [permissions.IsAdminUser]

    def delete(self, request, user_id):
        try:
            user = User.objects.get(id=user_id)
            if user == request.user:
                return Response(
                    {'error': 'Cannot delete yourself'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            if user.is_superuser:
                return Response(
                    {'error': 'Cannot delete a superuser'},
                    status=status.HTTP_400_BAD_REQUEST
                )
            email = user.email
            user.delete()
            return Response({'message': f'User {email} permanently deleted'})
        except User.DoesNotExist:
            return Response({'error': 'User not found'}, status=status.HTTP_404_NOT_FOUND)


class AdminToggleServiceActiveView(APIView):
    """Toggle a service's is_active status."""
    permission_classes = [permissions.IsAdminUser]

    def post(self, request, service_id):
        try:
            service = Service.objects.get(id=service_id)
            service.is_active = not service.is_active
            service.save(update_fields=['is_active'])
            return Response({
                'message': f'Service {"activated" if service.is_active else "deactivated"}',
                'is_active': service.is_active,
            })
        except Service.DoesNotExist:
            return Response({'error': 'Service not found'}, status=status.HTTP_404_NOT_FOUND)


class AdminBulkToggleServiceActiveView(APIView):
    """Bulk toggle multiple services' is_active status."""
    permission_classes = [permissions.IsAdminUser]

    def post(self, request):
        service_ids = request.data.get('service_ids', [])
        is_active = request.data.get('is_active', False)
        
        if not isinstance(service_ids, list):
            return Response({'error': 'service_ids must be a list'}, status=status.HTTP_400_BAD_REQUEST)
            
        updated_count = Service.objects.filter(id__in=service_ids).update(is_active=is_active)
        return Response({
            'message': f'{"Activated" if is_active else "Deactivated"} {updated_count} services',
            'updated_count': updated_count
        })


@method_decorator(csrf_exempt, name='dispatch')
class SiteSettingsView(APIView):
    """Get or update site-wide settings."""
    permission_classes = [permissions.IsAuthenticated]

    def get(self, request):
        settings = SiteSettings.load()
        return Response({
            'show_inactive_services': settings.show_inactive_services,
            'manual_bank_name': settings.manual_bank_name,
            'manual_account_name': settings.manual_account_name,
            'manual_account_number': settings.manual_account_number,
            # Crypto settings
            'binance_pay_id': settings.binance_pay_id,
            'binance_pay_qr': settings.binance_pay_qr or None,
            'crypto_usdt_trc20': settings.crypto_usdt_trc20,
            'crypto_usdt_bep20': settings.crypto_usdt_bep20,
            'crypto_sol': settings.crypto_sol,
        })
        
    def post(self, request):
        # Only admins can update settings
        if not request.user.is_staff:
            return Response(
                {"detail": "You do not have permission to perform this action."},
                status=status.HTTP_403_FORBIDDEN
            )
            
        settings = SiteSettings.load()

        # Update bank settings if provided
        if 'manual_bank_name' in request.data:
            settings.manual_bank_name = request.data['manual_bank_name']
        if 'manual_account_name' in request.data:
            settings.manual_account_name = request.data['manual_account_name']
        if 'manual_account_number' in request.data:
            settings.manual_account_number = request.data['manual_account_number']

        # Update crypto settings if provided
        if 'binance_pay_id' in request.data:
            settings.binance_pay_id = request.data['binance_pay_id']
        if 'crypto_usdt_trc20' in request.data:
            settings.crypto_usdt_trc20 = request.data['crypto_usdt_trc20']
        if 'crypto_usdt_bep20' in request.data:
            settings.crypto_usdt_bep20 = request.data['crypto_usdt_bep20']
        if 'crypto_sol' in request.data:
            settings.crypto_sol = request.data['crypto_sol']

        # Handle QR image upload — convert to base64 data URI so it survives Railway redeploys
        if 'binance_pay_qr' in request.FILES:
            import base64
            qr_file = request.FILES['binance_pay_qr']
            qr_bytes = qr_file.read()
            mime = qr_file.content_type or 'image/png'
            b64 = base64.b64encode(qr_bytes).decode('utf-8')
            settings.binance_pay_qr = f'data:{mime};base64,{b64}'

        settings.save()
        return Response({
            'message': 'Site settings updated successfully',
            'show_inactive_services': settings.show_inactive_services,
            'manual_bank_name': settings.manual_bank_name,
            'manual_account_name': settings.manual_account_name,
            'manual_account_number': settings.manual_account_number,
            'binance_pay_id': settings.binance_pay_id,
            'binance_pay_qr': settings.binance_pay_qr or None,
            'crypto_usdt_trc20': settings.crypto_usdt_trc20,
            'crypto_usdt_bep20': settings.crypto_usdt_bep20,
            'crypto_sol': settings.crypto_sol,
        })


class AdminToggleShowInactiveView(APIView):
    """Toggle the show_inactive_services per provider."""
    permission_classes = [permissions.IsAdminUser]

    def post(self, request, provider_slug=None):
        if provider_slug:
            # Per-provider toggle
            try:
                provider = Provider.objects.get(slug=provider_slug)
            except Provider.DoesNotExist:
                return Response({'error': 'Provider not found'}, status=status.HTTP_404_NOT_FOUND)
            provider.show_inactive_services = not provider.show_inactive_services
            provider.save(update_fields=['show_inactive_services'])
            return Response({
                'provider': provider.slug,
                'show_inactive_services': provider.show_inactive_services,
                'message': f'Inactive services from {provider.name} are now {"visible" if provider.show_inactive_services else "hidden"} to users',
            })
        else:
            # Legacy: toggle global SiteSettings
            settings = SiteSettings.load()
            settings.show_inactive_services = not settings.show_inactive_services
            settings.save()
            return Response({
                'show_inactive_services': settings.show_inactive_services,
                'message': f'Inactive services are now {"visible" if settings.show_inactive_services else "hidden"} to users',
            })


class AdminProviderListView(APIView):
    """List all providers with their details."""
    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        providers = Provider.objects.all()
        data = []
        for p in providers:
            data.append({
                'id': p.pk,
                'name': p.name,
                'slug': p.slug,
                'api_url': p.api_url,
                'currency': p.currency,
                'exchange_rate': str(p.exchange_rate),
                'is_active': p.is_active,
                'show_inactive_services': p.show_inactive_services,
                'sort_order': p.sort_order,
                'service_count': p.services.count(),
                'active_service_count': p.services.filter(is_active=True).count(),
            })
        return Response({'providers': data})
        
    def post(self, request):
        name = request.data.get('name')
        api_url = request.data.get('api_url')
        api_key = request.data.get('api_key')
        currency = request.data.get('currency', 'USD')
        exchange_rate = request.data.get('exchange_rate', 1.0)
        
        if not all([name, api_url, api_key]):
            return Response({'error': 'Name, API URL, and API Key are required'}, status=status.HTTP_400_BAD_REQUEST)
            
        from django.utils.text import slugify
        base_slug = slugify(name)
        slug = base_slug
        counter = 1
        while Provider.objects.filter(slug=slug).exists():
            slug = f"{base_slug}-{counter}"
            counter += 1
            
        provider = Provider.objects.create(
            name=name,
            slug=slug,
            api_url=api_url,
            api_key=api_key,
            currency=currency,
            exchange_rate=exchange_rate,
            is_active=True
        )
        
        return Response({
            'id': provider.pk,
            'name': provider.name,
            'slug': provider.slug,
            'api_url': provider.api_url,
            'currency': provider.currency,
            'exchange_rate': str(provider.exchange_rate),
            'is_active': provider.is_active,
            'show_inactive_services': provider.show_inactive_services,
            'sort_order': provider.sort_order,
            'service_count': 0,
            'active_service_count': 0,
        }, status=status.HTTP_201_CREATED)


class AdminUpdateProviderView(APIView):
    """Update provider settings (exchange rate, active status, etc)."""
    permission_classes = [permissions.IsAdminUser]

    def patch(self, request, provider_slug):
        try:
            provider = Provider.objects.get(slug=provider_slug)
        except Provider.DoesNotExist:
            return Response({'error': 'Provider not found'}, status=status.HTTP_404_NOT_FOUND)
        
        updated_fields = []
        
        if 'exchange_rate' in request.data:
            provider.exchange_rate = Decimal(str(request.data['exchange_rate']))
            updated_fields.append('exchange_rate')
        
        if 'is_active' in request.data:
            provider.is_active = bool(request.data['is_active'])
            updated_fields.append('is_active')
        
        if 'api_url' in request.data:
            provider.api_url = request.data['api_url']
            updated_fields.append('api_url')
        
        if 'api_key' in request.data:
            provider.api_key = request.data['api_key']
            updated_fields.append('api_key')
        
        if 'name' in request.data:
            provider.name = request.data['name']
            updated_fields.append('name')
        
        if updated_fields:
            updated_fields.append('updated_at')
            provider.save(update_fields=updated_fields)
        
        return Response({
            'message': f'Provider {provider.name} updated',
            'provider': {
                'name': provider.name,
                'slug': provider.slug,
                'currency': provider.currency,
                'exchange_rate': str(provider.exchange_rate),
                'is_active': provider.is_active,
            }
        })
