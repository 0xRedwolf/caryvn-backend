from django.db import migrations

def populate_usernames(apps, schema_editor):
    User = apps.get_model('core', 'User')
    for user in User.objects.all():
        if not user.username:
            # Derive username from email
            original_username = user.email.split('@')[0]
            username = original_username
            counter = 1
            # Ensure it's unique within the migration context
            while User.objects.filter(username=username).exists():
                username = f"{original_username}_{counter}"
                counter += 1
            user.username = username
            user.save()

class Migration(migrations.Migration):

    dependencies = [
        ('core', '0004_useractivity'),
    ]

    operations = [
        migrations.RunPython(populate_usernames),
    ]
