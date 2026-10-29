from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0056_registrationcode'),
    ]

    operations = [
        migrations.AddField(
            model_name='staffsubclass',
            name='generate_codes',
            field=models.BooleanField(default=False),
        ),
    ]
