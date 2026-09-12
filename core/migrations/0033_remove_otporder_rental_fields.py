from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0032_otp_orders'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='otporder',
            name='rental_days',
        ),
        migrations.RemoveField(
            model_name='otporder',
            name='rental_type',
        ),
        migrations.AddField(
            model_name='otporder',
            name='sms_messages',
            field=models.JSONField(blank=True, default=list, help_text='List of received SMS messages'),
        ),
    ]
