from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0029_loginverification'),
    ]

    operations = [
        migrations.AddField(
            model_name='staffprofile',
            name='has_seen_tutorial',
            field=models.BooleanField(default=False),
        ),
    ]
