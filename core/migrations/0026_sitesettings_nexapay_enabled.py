from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0025_add_order_source'),
    ]

    operations = [
        migrations.AddField(
            model_name='sitesettings',
            name='nexapay_enabled',
            field=models.BooleanField(
                default=True,
                help_text='Allow users to deposit using the NexaPay (virtual bank account) payment gateway',
            ),
        ),
    ]
