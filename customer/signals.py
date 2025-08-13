from django.db.models.signals import post_save
from django.dispatch import receiver
from accounts.models import CustomUser
from .models import CustomerMeta


@receiver(post_save, sender=CustomUser)
def create_customer_meta(sender, instance, created, **kwargs):
    if created:
        CustomerMeta.objects.get_or_create(user=instance)
