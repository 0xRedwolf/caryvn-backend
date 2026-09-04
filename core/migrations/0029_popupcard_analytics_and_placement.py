from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0028_blogauthor_blogcategory_blogpost'),
    ]

    operations = [
        migrations.AddField(
            model_name='popupcard',
            name='placement_type',
            field=models.CharField(
                choices=[('POPUP', 'Popup Modal'), ('BANNER', 'Dashboard In-Feed Banner')],
                default='POPUP',
                help_text='Where this ad is displayed',
                max_length=20,
            ),
        ),
        migrations.AddField(
            model_name='popupcard',
            name='impressions_count',
            field=models.PositiveIntegerField(
                default=0,
                help_text='Total views/impressions',
            ),
        ),
        migrations.AddField(
            model_name='popupcard',
            name='clicks_count',
            field=models.PositiveIntegerField(
                default=0,
                help_text='Total clicks on action button',
            ),
        ),
        migrations.AlterField(
            model_name='popupcard',
            name='image',
            field=models.CharField(
                blank=True,
                help_text='Image URL or Cloudinary CDN link',
                max_length=500,
                null=True,
            ),
        ),
    ]
