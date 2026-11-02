import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0075_seed_courses"),
    ]

    operations = [
        migrations.AddField(
            model_name="registrationcode",
            name="audience",
            field=models.CharField(choices=[("student", "Student"), ("staff", "Staff")],
                                   db_index=True, default="student", max_length=7),
        ),
        migrations.AddField(
            model_name="registrationcode",
            name="subclass",
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                    related_name="+", to="core.staffsubclass"),
        ),
    ]
