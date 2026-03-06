import os
import django
import csv

# Setup Django environment
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
django.setup()

from django.contrib.auth import get_user_model

User = get_user_model()

def export_users():
    # Only get active users who have an email
    users = User.objects.filter(is_active=True).exclude(email='').values_list('email', 'first_name', 'last_name')
    
    file_path = 'users_export.csv'
    
    with open(file_path, 'w', newline='', encoding='utf-8') as f:
        writer = csv.writer(f)
        writer.writerow(['Email', 'First Name', 'Last Name'])
        for user in users:
            writer.writerow(user)
            
    print(f"Successfully exported {users.count()} users to {file_path}")

if __name__ == '__main__':
    export_users()
