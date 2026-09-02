from decimal import Decimal
from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0026_sitesettings_nexapay_enabled'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='sitesettings',
            name='provider_balance_alert_threshold',
            field=models.DecimalField(
                decimal_places=2,
                default=Decimal('15.00'),
                help_text='Alert admin when any active provider balance drops below this amount (USD)',
                max_digits=10,
            ),
        ),
        migrations.CreateModel(
            name='AdminNotification',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('notification_type', models.CharField(choices=[('low_provider_balance', 'Low Provider Balance'), ('order_failed', 'Order Failed'), ('system', 'System Alert')], default='system', max_length=50)),
                ('severity', models.CharField(choices=[('info', 'Info'), ('warning', 'Warning'), ('critical', 'Critical')], default='info', max_length=20)),
                ('title', models.CharField(max_length=255)),
                ('message', models.TextField()),
                ('data', models.JSONField(blank=True, default=dict)),
                ('is_read', models.BooleanField(db_index=True, default=False)),
                ('created_at', models.DateTimeField(auto_now_add=True, db_index=True)),
                ('user', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='admin_notifications', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Admin Notification',
                'verbose_name_plural': 'Admin Notifications',
                'ordering': ['-created_at'],
            },
        ),
    ]
