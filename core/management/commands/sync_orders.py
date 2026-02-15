from django.core.management.base import BaseCommand
from core.utils import sync_active_orders

class Command(BaseCommand):
    help = 'Syncs order statuses with the SMM provider'

    def handle(self, *args, **options):
        self.stdout.write('Starting order sync...')
        
        result = sync_active_orders()
        
        self.stdout.write(self.style.SUCCESS(
            f'Sync complete. Updated: {result["updated"]}, Errors: {result["errors"]}'
        ))
