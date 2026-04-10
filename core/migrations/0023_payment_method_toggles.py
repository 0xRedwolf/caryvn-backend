from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0022_min_topup_500'),
    ]

    operations = [
        migrations.AddField(
            model_name='sitesettings',
            name='squad_enabled',
            field=models.BooleanField(
                default=True,
                help_text='Allow users to deposit using the Squad (card/online) payment gateway',
            ),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='manual_bank_enabled',
            field=models.BooleanField(
                default=True,
                help_text='Allow users to deposit via manual bank transfer',
            ),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='crypto_enabled',
            field=models.BooleanField(
                default=True,
                help_text='Allow users to deposit via Crypto (Binance Pay / On-Chain)',
            ),
        ),
    ]
