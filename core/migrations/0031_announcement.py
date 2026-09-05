from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0030_usersession_adminauditlog'),
    ]

    operations = [
        migrations.CreateModel(
            name='Announcement',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('text', models.CharField(help_text='The announcement text', max_length=300)),
                ('link_url', models.URLField(blank=True, help_text='Optional external or internal link', max_length=500, null=True)),
                ('link_text', models.CharField(blank=True, help_text="Optional anchor text (e.g., 'zapotp.com')", max_length=100, null=True)),
                ('color', models.CharField(choices=[('emerald', 'Green (Status / Live)'), ('primary', 'Blue (New Feature / Info)'), ('amber', 'Yellow (Promo / Notice)'), ('purple', 'Purple (Special)'), ('rose', 'Red (Urgent / Alert)')], default='primary', max_length=20)),
                ('is_ping', models.BooleanField(default=False, help_text='Pulsing ping animation on the indicator dot')),
                ('is_active', models.BooleanField(default=True, help_text='Toggle visibility')),
                ('sort_order', models.IntegerField(default=0, help_text='Lower numbers appear first')),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'verbose_name': 'Announcement',
                'verbose_name_plural': 'Announcements',
                'ordering': ['sort_order', '-created_at'],
            },
        ),
    ]
