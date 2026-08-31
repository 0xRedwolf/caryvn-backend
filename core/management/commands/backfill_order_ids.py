from django.core.management.base import BaseCommand
from django.db.models import Max
from core.models import Order

class Command(BaseCommand):
    help = 'Backfills missing sequential reseller_order_id on existing orders'

    def handle(self, *args, **options):
        orders_without_id = Order.objects.filter(reseller_order_id__isnull=True).order_by('created_at')
        count = orders_without_id.count()
        
        if count == 0:
            self.stdout.write(self.style.SUCCESS('All orders already have reseller_order_id assigned.'))
            return

        self.stdout.write(f'Found {count} orders without reseller_order_id. Assigning...')
        
        max_id = Order.objects.aggregate(m=Max('reseller_order_id'))['m'] or 0
        seq = max_id + 1
        updated = 0
        
        for order in orders_without_id:
            while Order.objects.filter(reseller_order_id=seq).exclude(pk=order.pk).exists():
                seq += 1
            order.reseller_order_id = seq
            order.save(update_fields=['reseller_order_id'])
            seq += 1
            updated += 1

        self.stdout.write(self.style.SUCCESS(f'Successfully assigned reseller_order_id to {updated} orders.'))
