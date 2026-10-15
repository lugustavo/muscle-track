import django.db.models.deletion
from django.db import migrations, models


def link_photos(apps, schema_editor):
    """Photos sent before assessments held photos: attach each to the assessment of the same day (the newest one),
    leaving the others loose (nothing is lost)."""
    Photo = apps.get_model('core', 'ProgressPhoto')
    Assessment = apps.get_model('core', 'Assessment')
    for photo in Photo.objects.filter(assessment__isnull=True):
        found = Assessment.objects.filter(person_id=photo.person_id, date=photo.date).order_by('-created_at').first()
        if found:
            photo.assessment = found
            photo.save(update_fields=['assessment'])


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0025_package_alerts'),
    ]

    operations = [
        migrations.AddField(
            model_name='progressphoto',
            name='angle',
            field=models.CharField(choices=[('front', 'Frente'), ('left', 'Lado esquerdo'), ('right', 'Lado direito'), ('back', 'Costas'), ('other', 'Outra')], default='other', max_length=10, verbose_name='Posição'),
        ),
        migrations.AddField(
            model_name='progressphoto',
            name='assessment',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='photos', to='core.assessment', verbose_name='Avaliação'),
        ),
        migrations.RunPython(link_photos, migrations.RunPython.noop),
    ]
