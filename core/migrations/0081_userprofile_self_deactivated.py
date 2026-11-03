from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0080_staffsubclass_clear_avatars'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='self_deactivated',
            field=models.BooleanField(default=False),
        ),
        migrations.AlterField(
            model_name='userprofile',
            name='status_reason',
            field=models.CharField(blank=True, choices=[
                ('not_enrolled', 'Not enrolled this semester'),
                ('dropped', 'Dropped out'),
                ('transferred', 'Transferred'),
                ('graduated', 'Graduated'),
                ('other', 'Other'),
                ('self_deactivated', 'Deactivated by the student'),
            ], max_length=20),
        ),
    ]
