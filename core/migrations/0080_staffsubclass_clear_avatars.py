from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0079_guidancemessage_hidden_for_student'),
    ]

    operations = [
        migrations.AddField(
            model_name='staffsubclass',
            name='clear_avatars',
            field=models.BooleanField(default=False),
        ),
    ]
