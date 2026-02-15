"""
DRF Serializers for Caryvn API.
"""
from rest_framework import serializers
from django.contrib.auth import get_user_model
from django.contrib.auth.password_validation import validate_password
from .models import (
    Wallet, Transaction, ServiceCategory, Service,
    Order, Ticket, TicketReply, MarkupRule, APILog
)

User = get_user_model()


# === Auth Serializers ===

class RegisterSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=True, validators=[validate_password])
    password2 = serializers.CharField(write_only=True, required=True)

    class Meta:
        model = User
        fields = ('email', 'password', 'password2', 'first_name', 'last_name')

    def validate(self, attrs):
        if attrs['password'] != attrs['password2']:
            raise serializers.ValidationError({"password": "Passwords don't match."})
        return attrs

    def create(self, validated_data):
        validated_data.pop('password2')
        user = User.objects.create_user(**validated_data)
        return user


class LoginSerializer(serializers.Serializer):
    email = serializers.EmailField(required=True)
    password = serializers.CharField(required=True, write_only=True)


class UserSerializer(serializers.ModelSerializer):
    balance = serializers.DecimalField(source='wallet.balance', max_digits=12, decimal_places=4, read_only=True)
    
    class Meta:
        model = User
        fields = ('id', 'email', 'first_name', 'last_name', 'username', 'balance', 
                  'is_verified', 'is_staff', 'date_joined')
        read_only_fields = ('id', 'email', 'is_verified', 'is_staff', 'date_joined')


class UserProfileUpdateSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ('first_name', 'last_name', 'username')


class ChangePasswordSerializer(serializers.Serializer):
    old_password = serializers.CharField(required=True, write_only=True)
    new_password = serializers.CharField(required=True, write_only=True, validators=[validate_password])


# === Wallet Serializers ===

class WalletSerializer(serializers.ModelSerializer):
    class Meta:
        model = Wallet
        fields = ('balance', 'currency', 'updated_at')
        read_only_fields = fields


class TransactionSerializer(serializers.ModelSerializer):
    class Meta:
        model = Transaction
        fields = ('id', 'type', 'amount', 'description', 'balance_after', 'status', 'reference', 'created_at')
        read_only_fields = fields


# === Service Serializers ===

class ServiceCategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = ServiceCategory
        fields = ('id', 'name', 'platform', 'slug')


class ServiceSerializer(serializers.ModelSerializer):
    category = ServiceCategorySerializer(read_only=True)
    
    class Meta:
        model = Service
        fields = ('id', 'provider_id', 'name', 'category', 'category_name',
                  'user_rate', 'min_quantity', 'max_quantity', 'service_type',
                  'has_refill', 'has_cancel', 'average_time', 'description',
                  'is_featured')


class ServiceListSerializer(serializers.ModelSerializer):
    """Lightweight serializer for service lists."""
    class Meta:
        model = Service
        fields = ('id', 'provider_id', 'name', 'category_name', 'user_rate',
                  'min_quantity', 'max_quantity', 'has_refill', 'has_cancel', 'is_featured')


# === Order Serializers ===

class OrderCreateSerializer(serializers.Serializer):
    service_id = serializers.IntegerField(required=True)
    link = serializers.URLField(required=True, max_length=500)
    quantity = serializers.IntegerField(required=True, min_value=1)
    
    def validate(self, attrs):
        try:
            service = Service.objects.get(provider_id=attrs['service_id'], is_active=True)
        except Service.DoesNotExist:
            raise serializers.ValidationError({"service_id": "Service not found or inactive."})
        
        quantity = attrs['quantity']
        if quantity < service.min_quantity:
            raise serializers.ValidationError({
                "quantity": f"Minimum quantity is {service.min_quantity}"
            })
        if quantity > service.max_quantity:
            raise serializers.ValidationError({
                "quantity": f"Maximum quantity is {service.max_quantity}"
            })
        
        attrs['service'] = service
        return attrs


class OrderSerializer(serializers.ModelSerializer):
    service_name = serializers.CharField(source='service.name', read_only=True)
    service_id = serializers.IntegerField(source='service.provider_id', read_only=True)
    
    class Meta:
        model = Order
        fields = ('id', 'service_id', 'service_name', 'link', 'quantity',
                  'charge', 'status', 'start_count', 'remains', 'created_at', 'completed_at')
        read_only_fields = fields


class OrderDetailSerializer(serializers.ModelSerializer):
    service = ServiceSerializer(read_only=True)
    
    class Meta:
        model = Order
        fields = ('id', 'service', 'link', 'quantity', 'charge', 'status',
                  'start_count', 'remains', 'provider_order_id', 'created_at', 'completed_at')
        read_only_fields = fields


# === Ticket Serializers ===

class TicketReplySerializer(serializers.ModelSerializer):
    user_email = serializers.CharField(source='user.email', read_only=True)
    
    class Meta:
        model = TicketReply
        fields = ('id', 'message', 'user_email', 'is_admin', 'created_at')
        read_only_fields = ('id', 'user_email', 'is_admin', 'created_at')


class TicketSerializer(serializers.ModelSerializer):
    replies = TicketReplySerializer(many=True, read_only=True)
    order_id = serializers.UUIDField(write_only=True, required=False, allow_null=True)
    
    class Meta:
        model = Ticket
        fields = ('id', 'subject', 'message', 'status', 'priority', 
                  'order', 'order_id', 'replies', 'created_at', 'updated_at')
        read_only_fields = ('id', 'status', 'replies', 'created_at', 'updated_at', 'order')
    
    def create(self, validated_data):
        order_id = validated_data.pop('order_id', None)
        if order_id:
            validated_data['order_id'] = order_id
        return super().create(validated_data)


class TicketListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Ticket
        fields = ('id', 'subject', 'status', 'priority', 'created_at', 'updated_at')


class TicketReplyCreateSerializer(serializers.Serializer):
    message = serializers.CharField(required=True)


# === Admin Serializers ===

class MarkupRuleSerializer(serializers.ModelSerializer):
    class Meta:
        model = MarkupRule
        fields = '__all__'


class APILogSerializer(serializers.ModelSerializer):
    user_email = serializers.CharField(source='user.email', read_only=True)
    
    class Meta:
        model = APILog
        fields = ('id', 'action', 'request_data', 'response_data', 'response_code',
                  'error', 'duration_ms', 'user_email', 'order', 'created_at')


class AdminOrderSerializer(serializers.ModelSerializer):
    """Order serializer for admin with profit info."""
    user_email = serializers.CharField(source='user.email', read_only=True)
    service_name = serializers.CharField(source='service.name', read_only=True)
    
    class Meta:
        model = Order
        fields = ('id', 'user_email', 'service_name', 'link', 'quantity',
                  'provider_rate', 'user_rate', 'charge', 'profit', 'status',
                  'provider_order_id', 'created_at')


class AdminUserSerializer(serializers.ModelSerializer):
    balance = serializers.DecimalField(source='wallet.balance', max_digits=12, decimal_places=4, read_only=True)
    total_orders = serializers.SerializerMethodField()
    total_spent = serializers.SerializerMethodField()
    
    class Meta:
        model = User
        fields = ('id', 'email', 'first_name', 'last_name', 'balance',
                  'is_active', 'is_staff', 'total_orders', 'total_spent', 'date_joined')
    
    def get_total_orders(self, obj):
        return obj.orders.count()
    
    def get_total_spent(self, obj):
        from django.db.models import Sum
        result = obj.orders.filter(status__in=['completed', 'partial']).aggregate(Sum('charge'))
        return result['charge__sum'] or 0
