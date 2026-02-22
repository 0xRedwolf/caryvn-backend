"""
Database models for Caryvn SMM Reseller Platform.
"""
import uuid
import secrets
from decimal import Decimal
from django.db import models
from django.contrib.auth.models import AbstractBaseUser, BaseUserManager, PermissionsMixin
from django.utils import timezone


class UserManager(BaseUserManager):
    """Custom user manager for email-based authentication."""
    
    def create_user(self, email, password=None, **extra_fields):
        if not email:
            raise ValueError('Users must have an email address')
        email = self.normalize_email(email)
        user = self.model(email=email, **extra_fields)
        user.set_password(password)
        user.save(using=self._db)
        return user
    
    def create_superuser(self, email, password=None, **extra_fields):
        extra_fields.setdefault('is_staff', True)
        extra_fields.setdefault('is_superuser', True)
        extra_fields.setdefault('is_active', True)
        return self.create_user(email, password, **extra_fields)


class User(AbstractBaseUser, PermissionsMixin):
    """Custom User model with email authentication."""
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    email = models.EmailField(unique=True, max_length=255)
    username = models.CharField(max_length=150, blank=True)
    first_name = models.CharField(max_length=150, blank=True)
    last_name = models.CharField(max_length=150, blank=True)
    
    # API Key for programmatic access
    api_key = models.CharField(max_length=64, unique=True, blank=True, null=True)
    
    # Status fields
    is_active = models.BooleanField(default=True)
    is_staff = models.BooleanField(default=False)
    is_verified = models.BooleanField(default=False)
    
    # Timestamps
    date_joined = models.DateTimeField(default=timezone.now)
    last_login = models.DateTimeField(null=True, blank=True)
    
    objects = UserManager()
    
    USERNAME_FIELD = 'email'
    REQUIRED_FIELDS = []
    
    class Meta:
        verbose_name = 'User'
        verbose_name_plural = 'Users'
        ordering = ['-date_joined']
    
    def __str__(self):
        return self.email
    
    def get_full_name(self):
        return f"{self.first_name} {self.last_name}".strip() or self.email
    
    def generate_api_key(self):
        """Generate a new API key for the user."""
        self.api_key = secrets.token_hex(32)
        self.save(update_fields=['api_key'])
        return self.api_key


class Wallet(models.Model):
    """User wallet for balance tracking."""
    
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='wallet')
    balance = models.DecimalField(max_digits=12, decimal_places=4, default=Decimal('0.0000'))
    currency = models.CharField(max_length=3, default='NGN')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        verbose_name = 'Wallet'
        verbose_name_plural = 'Wallets'
    
    def __str__(self):
        return f"{self.user.email} - {self.currency} {self.balance}"
    
    def create_pending_deposit(self, amount, payment_reference, payment_gateway='squad', description='Wallet top-up'):
        """Create a pending deposit transaction (Phase 1 of payment flow)."""
        amount = Decimal(str(amount))
        return Transaction.objects.create(
            wallet=self,
            type=Transaction.Type.DEPOSIT,
            amount=amount,
            description=description,
            balance_after=self.balance,  # Not yet credited
            status=Transaction.Status.PENDING,
            payment_reference=payment_reference,
            payment_gateway=payment_gateway,
        )
    
    def confirm_deposit(self, transaction):
        """Confirm a pending deposit and credit wallet (Phase 2 of payment flow). Idempotent."""
        from django.db import transaction as db_transaction

        with db_transaction.atomic():
            # Lock the wallet row to prevent concurrent double-crediting
            wallet = Wallet.objects.select_for_update().get(pk=self.pk)

            # Re-fetch transaction inside the lock to check status
            tx = Transaction.objects.select_for_update().get(pk=transaction.pk)
            if tx.status != Transaction.Status.PENDING:
                return wallet.balance  # Already processed, skip

            wallet.balance += tx.amount
            wallet.save(update_fields=['balance', 'updated_at'])

            tx.status = Transaction.Status.SUCCESS
            tx.balance_after = wallet.balance
            tx.save(update_fields=['status', 'balance_after'])

            # Update self to reflect new balance
            self.balance = wallet.balance
            return wallet.balance
    
    def fail_deposit(self, transaction):
        """Mark a pending deposit as failed."""
        if transaction.status != Transaction.Status.PENDING:
            return
        transaction.status = Transaction.Status.FAILED
        transaction.save(update_fields=['status'])
    
    def deposit(self, amount, description='Deposit'):
        """Add funds to wallet and create transaction (direct deposit, no payment gateway)."""
        amount = Decimal(str(amount))
        self.balance += amount
        self.save(update_fields=['balance', 'updated_at'])
        Transaction.objects.create(
            wallet=self,
            type=Transaction.Type.DEPOSIT,
            amount=amount,
            description=description,
            balance_after=self.balance,
            status=Transaction.Status.SUCCESS,
        )
        return self.balance
    
    def charge(self, amount, description='Order charge'):
        """Deduct funds from wallet and create transaction."""
        amount = Decimal(str(amount))
        if self.balance < amount:
            raise ValueError('Insufficient balance')
        self.balance -= amount
        self.save(update_fields=['balance', 'updated_at'])
        Transaction.objects.create(
            wallet=self,
            type=Transaction.Type.CHARGE,
            amount=-amount,
            description=description,
            balance_after=self.balance,
            status=Transaction.Status.SUCCESS,
        )
        return self.balance
    
    def refund(self, amount, description='Refund'):
        """Refund funds to wallet."""
        amount = Decimal(str(amount))
        self.balance += amount
        self.save(update_fields=['balance', 'updated_at'])
        Transaction.objects.create(
            wallet=self,
            type=Transaction.Type.REFUND,
            amount=amount,
            description=description,
            balance_after=self.balance,
            status=Transaction.Status.SUCCESS,
        )
        return self.balance


class Transaction(models.Model):
    """Wallet transaction history."""
    
    class Type(models.TextChoices):
        DEPOSIT = 'deposit', 'Deposit'
        CHARGE = 'charge', 'Order Charge'
        REFUND = 'refund', 'Refund'
        BONUS = 'bonus', 'Bonus'
    
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        SUCCESS = 'success', 'Success'
        FAILED = 'failed', 'Failed'
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    wallet = models.ForeignKey(Wallet, on_delete=models.CASCADE, related_name='transactions')
    type = models.CharField(max_length=20, choices=Type.choices)
    amount = models.DecimalField(max_digits=12, decimal_places=4)
    description = models.CharField(max_length=255)
    balance_after = models.DecimalField(max_digits=12, decimal_places=4)
    reference = models.CharField(max_length=100, blank=True)  # Legacy field
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.SUCCESS)
    payment_reference = models.CharField(max_length=100, blank=True, null=True, unique=True)
    payment_gateway = models.CharField(max_length=20, blank=True)  # 'squad', etc.
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Transaction'
        verbose_name_plural = 'Transactions'
    
    def __str__(self):
        return f"{self.type} - {self.amount} ({self.wallet.user.email})"


class ServiceCategory(models.Model):
    """Service category for organizing services."""
    
    name = models.CharField(max_length=100, unique=True)
    platform = models.CharField(max_length=50)  # Instagram, TikTok, YouTube, etc.
    slug = models.SlugField(unique=True)
    sort_order = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    
    class Meta:
        verbose_name = 'Service Category'
        verbose_name_plural = 'Service Categories'
        ordering = ['sort_order', 'name']
    
    def __str__(self):
        return f"{self.platform} - {self.name}"


class Service(models.Model):
    """SMM service (cached from provider)."""
    
    provider_id = models.IntegerField(unique=True)  # ID from SMM provider
    name = models.CharField(max_length=255)
    category = models.ForeignKey(ServiceCategory, on_delete=models.SET_NULL, null=True, blank=True, related_name='services')
    category_name = models.CharField(max_length=100, blank=True)  # Raw category from provider
    
    # Pricing
    provider_rate = models.DecimalField(max_digits=10, decimal_places=4)  # Cost per 1000
    user_rate = models.DecimalField(max_digits=10, decimal_places=4)  # Price per 1000 (with markup)
    
    # Limits
    min_quantity = models.IntegerField(default=10)
    max_quantity = models.IntegerField(default=10000)
    
    # Type & Features
    service_type = models.CharField(max_length=50, default='Default')
    has_refill = models.BooleanField(default=False)
    has_cancel = models.BooleanField(default=False)
    average_time = models.CharField(max_length=100, blank=True)  # e.g., "1-2 hours"
    description = models.TextField(blank=True)
    
    # Status
    is_active = models.BooleanField(default=True)
    is_featured = models.BooleanField(default=False)
    
    # Timestamps
    last_synced = models.DateTimeField(auto_now=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['category_name', 'name']
        verbose_name = 'Service'
        verbose_name_plural = 'Services'
    
    def __str__(self):
        return f"[{self.provider_id}] {self.name}"
    
    def calculate_price(self, quantity):
        """Calculate price for given quantity."""
        return (self.user_rate / 1000) * Decimal(str(quantity))


class MarkupRule(models.Model):
    """Pricing markup rules."""
    
    class Level(models.TextChoices):
        GLOBAL = 'global', 'Global (all services)'
        PLATFORM = 'platform', 'Platform-wide'
        CATEGORY = 'category', 'Category-specific'
        SERVICE = 'service', 'Service-specific'
    
    name = models.CharField(max_length=100)
    level = models.CharField(max_length=20, choices=Level.choices)
    
    # Target (based on level)
    platform = models.CharField(max_length=50, blank=True)  # For platform level
    category = models.ForeignKey(ServiceCategory, on_delete=models.CASCADE, null=True, blank=True)
    service = models.ForeignKey(Service, on_delete=models.CASCADE, null=True, blank=True)
    
    # Markup type
    percentage = models.DecimalField(max_digits=5, decimal_places=2, default=Decimal('0'))  # e.g., 20.00 = 20%
    fixed_addition = models.DecimalField(max_digits=10, decimal_places=4, default=Decimal('0'))  # Add to rate
    
    # Priority (higher = more priority)
    priority = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-priority', 'level']
        verbose_name = 'Markup Rule'
        verbose_name_plural = 'Markup Rules'
    
    def __str__(self):
        return f"{self.name} ({self.level})"


class Order(models.Model):
    """Customer order."""
    
    class Status(models.TextChoices):
        PENDING = 'pending', 'Pending'
        PROCESSING = 'processing', 'Processing'
        IN_PROGRESS = 'in_progress', 'In Progress'
        COMPLETED = 'completed', 'Completed'
        PARTIAL = 'partial', 'Partial'
        CANCELED = 'canceled', 'Canceled'
        REFUNDED = 'refunded', 'Refunded'
        FAILED = 'failed', 'Failed'
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='orders')
    service = models.ForeignKey(Service, on_delete=models.SET_NULL, null=True, related_name='orders')
    
    # Provider order info
    provider_order_id = models.CharField(max_length=100, blank=True)  # ID from SMM provider
    
    # Order details
    link = models.URLField(max_length=500)
    quantity = models.IntegerField()
    start_count = models.IntegerField(null=True, blank=True)
    remains = models.IntegerField(null=True, blank=True)
    
    # Pricing
    provider_rate = models.DecimalField(max_digits=10, decimal_places=4)
    user_rate = models.DecimalField(max_digits=10, decimal_places=4)
    charge = models.DecimalField(max_digits=10, decimal_places=4)  # Total charged
    profit = models.DecimalField(max_digits=10, decimal_places=4, default=Decimal('0'))
    currency = models.CharField(max_length=3, default='NGN')
    
    # Status
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.PENDING)
    status_updated_at = models.DateTimeField(auto_now=True)
    
    # Timestamps
    created_at = models.DateTimeField(auto_now_add=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Order'
        verbose_name_plural = 'Orders'
    
    def __str__(self):
        return f"Order {str(self.id)[:8]} - {self.status}"
    
    def calculate_profit(self):
        """Calculate profit for this order."""
        provider_cost = (self.provider_rate / 1000) * self.quantity
        self.profit = self.charge - provider_cost
        return self.profit


class Ticket(models.Model):
    """Support ticket."""
    
    class Status(models.TextChoices):
        OPEN = 'open', 'Open'
        PENDING = 'pending', 'Pending'
        ANSWERED = 'answered', 'Answered'
        CLOSED = 'closed', 'Closed'
    
    class Priority(models.TextChoices):
        LOW = 'low', 'Low'
        MEDIUM = 'medium', 'Medium'
        HIGH = 'high', 'High'
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='tickets')
    order = models.ForeignKey(Order, on_delete=models.SET_NULL, null=True, blank=True, related_name='tickets')
    
    subject = models.CharField(max_length=255)
    message = models.TextField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    priority = models.CharField(max_length=20, choices=Priority.choices, default=Priority.MEDIUM)
    
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    
    class Meta:
        ordering = ['-created_at']
        verbose_name = 'Ticket'
        verbose_name_plural = 'Tickets'
    
    def __str__(self):
        return f"Ticket: {self.subject} ({self.status})"


class TicketReply(models.Model):
    """Reply to a support ticket."""
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    ticket = models.ForeignKey(Ticket, on_delete=models.CASCADE, related_name='replies')
    user = models.ForeignKey(User, on_delete=models.CASCADE)  # Can be user or admin
    message = models.TextField()
    is_admin = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['created_at']
        verbose_name = 'Ticket Reply'
        verbose_name_plural = 'Ticket Replies'


class APILog(models.Model):
    """Log for SMM provider API requests."""
    
    class Action(models.TextChoices):
        SERVICES = 'services', 'Get Services'
        BALANCE = 'balance', 'Get Balance'
        ADD = 'add', 'Add Order'
        STATUS = 'status', 'Get Status'
    
    action = models.CharField(max_length=20, choices=Action.choices)
    request_data = models.JSONField(default=dict)
    response_data = models.JSONField(default=dict)
    response_code = models.IntegerField(null=True)
    error = models.TextField(blank=True)
    duration_ms = models.IntegerField(null=True)  # Request duration in milliseconds
    
    user = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True)
    order = models.ForeignKey(Order, on_delete=models.SET_NULL, null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']
        verbose_name = 'API Log'
        verbose_name_plural = 'API Logs'
    
    def __str__(self):
        return f"{self.action} - {self.created_at}"


class UserActivity(models.Model):
    """Track user page visits and actions for admin monitoring."""
    
    class Action(models.TextChoices):
        PAGE_VISIT = 'page_visit', 'Page Visit'
        CLICK = 'click', 'Click'
        ORDER = 'order', 'Order Placed'
        LOGIN = 'login', 'Login'
    
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='activities')
    action = models.CharField(max_length=20, choices=Action.choices, default=Action.PAGE_VISIT)
    page = models.CharField(max_length=500)  # URL path e.g. /dashboard
    metadata = models.JSONField(default=dict, blank=True)  # Extra context
    ip_address = models.GenericIPAddressField(null=True, blank=True)
    user_agent = models.CharField(max_length=500, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-created_at']
        verbose_name = 'User Activity'
        verbose_name_plural = 'User Activities'
        indexes = [
            models.Index(fields=['user', '-created_at']),
        ]
    
    def __str__(self):
        return f"{self.user.email} - {self.action} - {self.page}"


# Signal to create wallet when user is created
from django.db.models.signals import post_save
from django.dispatch import receiver

@receiver(post_save, sender=User)
def create_user_wallet(sender, instance, created, **kwargs):
    if created:
        Wallet.objects.create(user=instance)
