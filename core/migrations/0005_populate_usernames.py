from django.db import migrations


class Migration(migrations.Migration):
    """Originally populated usernames for existing users. Already applied — now a no-op."""

    dependencies = [
        ('core', '0004_useractivity'),
    ]

    operations = []
