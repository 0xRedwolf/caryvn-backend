# Generated manually 2026-03-03 — adds crypto fields to SiteSettings

import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0010_sitesettings_manual_account_name_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='sitesettings',
            name='binance_pay_id',
            field=models.CharField(blank=True, help_text='Admin Binance Pay ID shown to users', max_length=100),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='binance_pay_qr',
            field=models.ImageField(blank=True, null=True, upload_to='crypto_qr/', help_text='QR code for Binance Pay'),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='crypto_usdt_trc20',
            field=models.CharField(blank=True, help_text='USDT-TRC20 wallet address', max_length=200),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='crypto_usdt_bep20',
            field=models.CharField(blank=True, help_text='USDT-BEP20 wallet address', max_length=200),
        ),
        migrations.AddField(
            model_name='sitesettings',
            name='crypto_sol',
            field=models.CharField(blank=True, help_text='SOL wallet address', max_length=200),
        ),
    ]
