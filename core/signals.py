"""Keep the disk and the accounts in sync with the database.

Files are removed after the transaction commits, so a rolled-back delete never
loses a photo. Using signals (not model.delete overrides) also covers cascades
and queryset deletes, e.g. deleting a student or a user from the admin.
"""
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models.signals import post_delete, pre_save
from django.dispatch import receiver

from .models import Exercise, Person, ProgressPhoto


def _delete_files_later(files):
    """files: iterable of (storage, name) pairs; empty names are skipped."""
    files = [(storage, name) for storage, name in files if name]

    def _delete():
        for storage, name in files:
            storage.delete(name)

    if files:
        transaction.on_commit(_delete)


def _field_file(f):
    return (f.storage, f.name) if f else (None, '')


@receiver(post_delete, sender=ProgressPhoto)
def delete_photo_files(sender, instance, **kwargs):
    _delete_files_later([_field_file(instance.image), _field_file(instance.thumbnail)])


@receiver(post_delete, sender=Person)
def cleanup_deleted_person(sender, instance, **kwargs):
    _delete_files_later([_field_file(instance.avatar)])
    # Deleting a Person (e.g. from the admin) must not leave a working login behind.
    # Staff accounts are never touched; if the user was already deleted this is a no-op.
    User.objects.filter(pk=instance.user_id, is_staff=False, is_superuser=False).delete()


@receiver(pre_save, sender=Person)
def delete_replaced_avatar(sender, instance, **kwargs):
    """Drop the old avatar file when it is replaced or cleared."""
    if not instance.pk:
        return
    old = Person.objects.filter(pk=instance.pk).values_list('avatar', flat=True).first()
    new = instance.avatar.name if instance.avatar else ''
    if old and old != new:
        _delete_files_later([(Person._meta.get_field('avatar').storage, old)])


@receiver(post_delete, sender=Exercise)
def delete_exercise_files(sender, instance, **kwargs):
    _delete_files_later([_field_file(instance.media_file), _field_file(instance.thumbnail)])


@receiver(pre_save, sender=Exercise)
def delete_replaced_exercise_media(sender, instance, **kwargs):
    """Drop the old media file / thumbnail from the disk when they are replaced or removed."""
    if not instance.pk:
        return
    old = Exercise.objects.filter(pk=instance.pk).values('media_file', 'thumbnail').first()
    if not old:
        return
    gone = []
    for field in ('media_file', 'thumbnail'):
        current = getattr(instance, field)
        if old[field] and old[field] != (current.name if current else ''):
            gone.append((Exercise._meta.get_field(field).storage, old[field]))
    _delete_files_later(gone)
