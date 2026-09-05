from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import uuid


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0029_popupcard_analytics_and_placement'),
    ]

    operations = [
        migrations.CreateModel(
            name='UserSession',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('session_key', models.CharField(db_index=True, max_length=255, unique=True)),
                ('ip_address', models.GenericIPAddressField(blank=True, null=True)),
                ('user_agent', models.CharField(blank=True, max_length=500)),
                ('device_type', models.CharField(default='Desktop', max_length=50)),
                ('browser', models.CharField(default='Unknown Browser', max_length=100)),
                ('os', models.CharField(default='Unknown OS', max_length=100)),
                ('location', models.CharField(default='Unknown Location', max_length=255)),
                ('is_active', models.BooleanField(default=True)),
                ('last_active_at', models.DateTimeField(auto_now=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='sessions', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'User Session',
                'verbose_name_plural': 'User Sessions',
                'ordering': ['-last_active_at'],
                'indexes': [
                    models.Index(fields=['user', '-last_active_at'], name='core_userses_user_id_9cf0a1_idx'),
                    models.Index(fields=['session_key'], name='core_userses_session_1a4b82_idx'),
                ],
            },
        ),
        migrations.CreateModel(
            name='AdminAuditLog',
            fields=[
                ('id', models.UUIDField(default=uuid.uuid4, editable=False, primary_key=True, serialize=False)),
                ('action', models.CharField(choices=[('balance_adjustment', 'Manual Balance Adjustment'), ('service_price_update', 'Service Price / Margin Override'), ('provider_update', 'Provider Configuration Update'), ('order_status_override', 'Order Status Override / Refund'), ('user_role_change', 'User Role / Permission Change'), ('system_setting', 'System Setting Update')], max_length=50)),
                ('target_model', models.CharField(max_length=100)),
                ('target_id', models.CharField(max_length=255)),
                ('description', models.TextField(blank=True)),
                ('changes', models.JSONField(blank=True, default=dict)),
                ('ip_address', models.GenericIPAddressField(blank=True, null=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('actor', models.ForeignKey(null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='admin_actions', to=settings.AUTH_USER_MODEL)),
            ],
            options={
                'verbose_name': 'Admin Audit Log',
                'verbose_name_plural': 'Admin Audit Logs',
                'ordering': ['-created_at'],
                'indexes': [
                    models.Index(fields=['action', '-created_at'], name='core_adminau_action_7eb943_idx'),
                    models.Index(fields=['actor', '-created_at'], name='core_adminau_actor_i_5df3a2_idx'),
                    models.Index(fields=['target_model', 'target_id'], name='core_adminau_target__c74c61_idx'),
                ],
            },
        ),
    ]
