import csv
from django.http import HttpResponse
from rest_framework.views import APIView
from rest_framework import permissions

class AdminExportUsersCSVView(APIView):
    """Export all active users with emails to a CSV file."""
    permission_classes = [permissions.IsAdminUser]

    def get(self, request):
        from django.contrib.auth import get_user_model
        User = get_user_model()

        # Query active users
        users = User.objects.filter(is_active=True).exclude(email='').values_list('email', 'first_name', 'last_name')

        # Create the HttpResponse object with the appropriate CSV header.
        response = HttpResponse(
            content_type='text/csv',
            headers={'Content-Disposition': 'attachment; filename="users_export.csv"'},
        )

        writer = csv.writer(response)
        writer.writerow(['Email', 'First Name', 'Last Name'])
        for user in users:
            writer.writerow(user)

        return response
