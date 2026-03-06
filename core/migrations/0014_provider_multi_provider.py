"""
Multi-provider support migration.

Step 1: Create Provider model
Step 2: Rename Service.provider_id → Service.external_id (preserve data)
Step 3: Add provider FK + new fields to Service, Order, APILog
Step 4: Seed EngainsMedia provider from env vars and assign existing data
"""
from decimal import Decimal
from django.db import migrations, models
import django.db.models.deletion
from django.conf import settings


def seed_providers(apps, schema_editor):
    """Create the initial EngainsMedia provider and assign all existing data to it."""
    Provider = apps.get_model('core', 'Provider')
    Service = apps.get_model('core', 'Service')
    Order = apps.get_model('core', 'Order')
    APILog = apps.get_model('core', 'APILog')
    
    # Create EngainsMedia provider using current env vars
    provider = Provider.objects.create(
        name='EngainsMedia',
        slug='engainsmedia',
        api_url=getattr(settings, 'SMM_PROVIDER_URL', ''),
        api_key=getattr(settings, 'SMM_PROVIDER_KEY', ''),
        currency='NGN',
        exchange_rate=Decimal('1.00'),
        is_active=True,
        sort_order=0,
    )
    
    # Assign all existing services to EngainsMedia
    Service.objects.all().update(provider=provider)
    # Copy provider_rate to provider_rate_ngn (same currency, rate = 1.0)
    for svc in Service.objects.all():
        svc.provider_rate_ngn = svc.provider_rate
        svc.save(update_fields=['provider_rate_ngn'])
    
    # Assign all existing orders to EngainsMedia
    Order.objects.all().update(provider=provider)
    
    # Assign all existing API logs to EngainsMedia
    APILog.objects.all().update(provider=provider)


def reverse_seed(apps, schema_editor):
    """Remove seeded providers."""
    Provider = apps.get_model('core', 'Provider')
    Provider.objects.filter(slug='engainsmedia').delete()


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0013_transaction_payment_proof_base64'),
    ]

    operations = [
        # Step 1: Create Provider model
        migrations.CreateModel(
            name='Provider',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100)),
                ('slug', models.SlugField(unique=True)),
                ('api_url', models.URLField()),
                ('api_key', models.CharField(max_length=200)),
                ('currency', models.CharField(default='NGN', max_length=3)),
                ('exchange_rate', models.DecimalField(decimal_places=2, default=Decimal('1.00'), help_text='Conversion rate to NGN. Set 1.00 for NGN providers.', max_digits=10)),
                ('is_active', models.BooleanField(default=True, help_text='Master on/off switch')),
                ('show_inactive_services', models.BooleanField(default=False, help_text='When enabled, inactive services from this provider are visible to users')),
                ('sort_order', models.IntegerField(default=0)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Provider',
                'verbose_name_plural': 'Providers',
                'ordering': ['sort_order', 'name'],
            },
        ),
        
        # Step 2: Rename provider_id → external_id on Service (preserves data)
        migrations.RenameField(
            model_name='service',
            old_name='provider_id',
            new_name='external_id',
        ),
        
        # Step 3a: Add provider FK to Service
        migrations.AddField(
            model_name='service',
            name='provider',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.CASCADE, related_name='services', to='core.provider'),
        ),
        
        # Step 3b: Add provider_rate_ngn to Service
        migrations.AddField(
            model_name='service',
            name='provider_rate_ngn',
            field=models.DecimalField(blank=True, decimal_places=4, max_digits=10, null=True),
        ),
        
        # Step 3c: Add provider FK to Order
        migrations.AddField(
            model_name='order',
            name='provider',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='orders', to='core.provider'),
        ),
        
        # Step 3d: Add provider FK to APILog
        migrations.AddField(
            model_name='apilog',
            name='provider',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, to='core.provider'),
        ),
        
        # Step 3e: Update unique constraint on Service
        migrations.AlterUniqueTogether(
            name='service',
            unique_together={('provider', 'external_id')},
        ),
        
        # Step 3f: Update SiteSettings help text
        migrations.AlterField(
            model_name='sitesettings',
            name='show_inactive_services',
            field=models.BooleanField(default=False, help_text='Legacy field — inactive service visibility is now per-provider'),
        ),
        
        # Step 4: Seed EngainsMedia and assign existing data
        migrations.RunPython(seed_providers, reverse_seed),
    ]
