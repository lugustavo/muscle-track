# Generated migration - migrate exercise muscle_group field

from django.db import migrations, models
import django.db.models.deletion


def migrate_exercise_muscle_groups(apps, schema_editor):
    Exercise = apps.get_model('core', 'Exercise')
    MuscleGroup = apps.get_model('core', 'MuscleGroup')

    # Mapeamento dos valores antigos para os slugs
    mapping = {
        'peito': 'peito',
        'costas': 'costas',
        'ombros': 'ombros',
        'ombro': 'ombros',
        'biceps': 'biceps',
        'triceps': 'triceps',
        'pernas': 'pernas',
        'perna': 'pernas',
        'gluteos': 'gluteos',
        'gluteo': 'gluteos',
        'abdomen': 'abdomen',
        'panturrilha': 'panturrilha',
        'antebraco': 'antebraco',
        'cardio': 'cardio',
        'funcional': 'funcional',
    }

    for exercise in Exercise.objects.all():
        old_value = exercise.muscle_group_old
        if old_value:
            slug = mapping.get(old_value.lower(), old_value.lower())
            try:
                muscle_group = MuscleGroup.objects.get(slug=slug)
                exercise.muscle_group_new = muscle_group
                exercise.save()
            except MuscleGroup.DoesNotExist:
                print(f'Warning: MuscleGroup not found for slug: {slug}')


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0003_populate_musclegroup'),
    ]

    operations = [
        # Renomear campo antigo
        migrations.RenameField(
            model_name='exercise',
            old_name='muscle_group',
            new_name='muscle_group_old',
        ),
        # Adicionar novo campo nullable
        migrations.AddField(
            model_name='exercise',
            name='muscle_group_new',
            field=models.ForeignKey(
                null=True,
                blank=True,
                on_delete=django.db.models.deletion.PROTECT,
                related_name='exercises',
                to='core.musclegroup',
                verbose_name='Grupo Muscular'
            ),
        ),
        # Migrar dados
        migrations.RunPython(migrate_exercise_muscle_groups, migrations.RunPython.noop),
        # Remover campo antigo
        migrations.RemoveField(
            model_name='exercise',
            name='muscle_group_old',
        ),
        # Renomear novo campo para muscle_group
        migrations.RenameField(
            model_name='exercise',
            old_name='muscle_group_new',
            new_name='muscle_group',
        ),
        # Tornar campo obrigatório
        migrations.AlterField(
            model_name='exercise',
            name='muscle_group',
            field=models.ForeignKey(
                on_delete=django.db.models.deletion.PROTECT,
                related_name='exercises',
                to='core.musclegroup',
                verbose_name='Grupo Muscular'
            ),
        ),
    ]
