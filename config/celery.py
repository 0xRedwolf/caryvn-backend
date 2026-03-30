"""
Celery application for Caryvn.
"""
import os
from celery import Celery

# Set the default Django settings module
os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

app = Celery('config')

# Load config from Django settings, using the CELERY_ namespace
app.config_from_object('django.conf:settings', namespace='CELERY')

# Auto-discover tasks in all installed apps
app.autodiscover_tasks()

# Beat schedule — periodic tasks
app.conf.beat_schedule = {
    # Sync active order statuses from provider — every 5 minutes for fast updates
    'sync-active-orders-every-5-min': {
        'task': 'core.tasks.sync_orders_task',
        'schedule': 5 * 60,  # Every 5 minutes
    },
    # Sync the service catalogue from providers — every 6 hours is plenty
    'sync-services-every-6-hours': {
        'task': 'core.tasks.sync_services_task',
        'schedule': 6 * 60 * 60,  # Every 6 hours
    },
}
app.conf.timezone = 'UTC'
