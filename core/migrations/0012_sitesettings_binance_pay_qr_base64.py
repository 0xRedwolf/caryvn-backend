from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0011_crypto_settings'),
    ]

    operations = [
        migrations.AlterField(
            model_name='sitesettings',
            name='binance_pay_qr',
            field=models.TextField(
                blank=True,
                default='',
                help_text='QR code image as base64 data URI (e.g. data:image/png;base64,...)',
            ),
        ),
    ]
