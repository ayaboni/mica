from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0081_userprofile_self_deactivated'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='birthday',
            field=models.DateField(blank=True, null=True),
        ),
    ]
