from django.db import migrations, models
from decimal import Decimal


def update_min_topup_amount(apps, schema_editor):
    """Update existing SiteSettings row to ₦500 minimum if it was still ₦1000."""
    SiteSettings = apps.get_model('core', 'SiteSettings')
    SiteSettings.objects.filter(min_topup_amount=Decimal('1000.00')).update(
        min_topup_amount=Decimal('500.00')
    )


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0021_popupcard_action_text'),
    ]

    operations = [
        migrations.AlterField(
            model_name='sitesettings',
            name='min_topup_amount',
            field=models.DecimalField(
                default=Decimal('500.00'),
                decimal_places=2,
                max_digits=10,
                help_text='Minimum allowed top-up amount',
            ),
        ),
        # Also update the live DB row so admins don't have to manually change it
        migrations.RunPython(update_min_topup_amount, migrations.RunPython.noop),
    ]
