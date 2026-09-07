import uuid
from decimal import Decimal
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0031_announcement'),
    ]

    operations = [
        migrations.CreateModel(
            name='OTPProviderSetting',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('api_key', models.CharField(blank=True, help_text='ZapOTP Bearer API Key', max_length=255)),
                ('base_url', models.URLField(default='https://zapotp.com/account/api/v1', help_text='ZapOTP Base API Endpoint')),
                ('is_active', models.BooleanField(default=True, help_text='Master toggle to enable/disable OTP verification service')),
                ('markup_percentage', models.DecimalField(decimal_places=2, default=Decimal('30.00'), help_text='Markup percentage applied on top of ZapOTP base price (e.g. 30.00 for 30%)', max_digits=6)),
                ('min_margin', models.DecimalField(decimal_places=2, default=Decimal('150.00'), help_text='Minimum profit floor in NGN per number (e.g. 150.00 NGN)', max_digits=10)),
                ('low_balance_threshold', models.DecimalField(decimal_places=2, default=Decimal('15000.00'), help_text='ZapOTP wallet balance threshold below which admins are alerted', max_digits=12)),
                ('cached_balance', models.DecimalField(decimal_places=2, default=Decimal('0.00'), help_text='Last known upstream ZapOTP balance', max_digits=12)),
                ('last_balance_check', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'OTP Provider Setting',
                'verbose_name_plural': 'OTP Provider Settings',
            },
        ),
        migrations.CreateModel(
            name='OTPOrder',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('provider_order_id', models.CharField(db_index=True, help_text='ZapOTP order ID', max_length=100)),
                ('phone_number', models.CharField(help_text='Rented phone number', max_length=50)),
                ('country', models.CharField(default='US', help_text='ISO country code', max_length=10)),
                ('service_id', models.CharField(help_text='ZapOTP service identifier (e.g. whatsapp)', max_length=100)),
                ('service_name', models.CharField(help_text='Display service name (e.g. WhatsApp)', max_length=150)),
                ('provider', models.CharField(default='global', help_text='ZapOTP provider pool', max_length=50)),
                ('rental_type', models.CharField(choices=[('short', 'Short-Term (Single OTP)'), ('long', 'Long-Term (3-30 Days)')], default='short', max_length=20)),
                ('rental_days', models.IntegerField(default=0, help_text='Rental days if long-term')),
                ('provider_cost', models.DecimalField(decimal_places=2, help_text='Cost charged by ZapOTP in NGN', max_digits=12)),
                ('user_charge', models.DecimalField(decimal_places=2, help_text='Amount deducted from user wallet in NGN', max_digits=12)),
                ('profit', models.DecimalField(decimal_places=2, default=Decimal('0.00'), help_text='user_charge - provider_cost', max_digits=12)),
                ('status', models.CharField(choices=[('PENDING', 'Pending (Listening for SMS)'), ('RECEIVED', 'Received (Code Delivered)'), ('CANCELED', 'Canceled (Refunded)'), ('EXPIRED', 'Expired (Auto-Refunded)'), ('REFUNDED', 'Refunded')], db_index=True, default='PENDING', max_length=20)),
                ('sms_code', models.CharField(blank=True, help_text='Extracted verification code', max_length=50, null=True)),
                ('full_sms', models.TextField(blank=True, default='', help_text='Full received SMS text body')),
                ('expires_at', models.DateTimeField(db_index=True, help_text='When order expires and becomes eligible for auto-refund')),
                ('received_at', models.DateTimeField(blank=True, null=True)),
                ('refunded_at', models.DateTimeField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='otp_orders', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'OTP Order',
                'verbose_name_plural': 'OTP Orders',
                'ordering': ['-created_at'],
            },
        ),
        migrations.AddIndex(
            model_name='otporder',
            index=models.Index(fields=['user', '-created_at'], name='core_otpord_user_id_44f1c9_idx'),
        ),
        migrations.AddIndex(
            model_name='otporder',
            index=models.Index(fields=['status', 'expires_at'], name='core_otpord_status_67a2bd_idx'),
        ),
    ]
