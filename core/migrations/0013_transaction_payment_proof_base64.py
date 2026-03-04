from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0012_sitesettings_binance_pay_qr_base64'),
    ]

    operations = [
        migrations.AlterField(
            model_name='transaction',
            name='payment_proof',
            field=models.TextField(
                blank=True,
                default='',
                help_text='Payment proof image as base64 data URI',
            ),
        ),
    ]
