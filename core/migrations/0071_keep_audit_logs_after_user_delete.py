import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


def fill_student_labels(apps, schema_editor):
    """Snapshot each existing row's student name before the link can be cleared."""
    User = apps.get_model(settings.AUTH_USER_MODEL)
    for model_name in ("AccessLog", "StudentStatusLog"):
        Log = apps.get_model("core", model_name)
        ids = Log.objects.exclude(student__isnull=True).values_list("student_id", flat=True).distinct()
        for user in User.objects.filter(pk__in=ids).iterator():
            full = ("%s %s" % (user.first_name, user.last_name)).strip()
            label = (full or user.username)[:150]
            Log.objects.filter(student_id=user.pk, student_label="").update(student_label=label)


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0070_create_cache_table'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.AddField(
            model_name='accesslog',
            name='student_label',
            field=models.CharField(blank=True, max_length=150),
        ),
        migrations.AddField(
            model_name='studentstatuslog',
            name='student_label',
            field=models.CharField(blank=True, max_length=150),
        ),
        migrations.AlterField(
            model_name='accesslog',
            name='student',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                    related_name='access_logs_received', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AlterField(
            model_name='studentstatuslog',
            name='student',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                    related_name='status_history', to=settings.AUTH_USER_MODEL),
        ),
        migrations.RunPython(fill_student_labels, migrations.RunPython.noop),
    ]
