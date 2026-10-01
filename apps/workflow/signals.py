"""Departments switched on or off: the days still collecting orders follow (definitions.department_switched)."""
from django.db.models.signals import post_save, pre_save
from django.dispatch import receiver

from apps.orgstructure.models import Department


@receiver(pre_save, sender=Department)
def _remember_active(sender, instance, **kwargs):
    if kwargs.get("raw"):
        return
    instance._was_active = (sender.objects.filter(pk=instance.pk).values_list("is_active", flat=True).first()
                            if instance.pk else None)


@receiver(post_save, sender=Department)
def _active_changed(sender, instance, created, **kwargs):
    if kwargs.get("raw"):
        return
    was = getattr(instance, "_was_active", None)
    if (created and instance.is_active) or (was is not None and was != instance.is_active):
        from .definitions import department_switched

        department_switched(instance)
