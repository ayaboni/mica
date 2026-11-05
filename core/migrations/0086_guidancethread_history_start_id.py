from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0085_alter_userprofile_chat_theme'),
    ]

    operations = [
        migrations.AddField(
            model_name='guidancethread',
            name='history_start_id',
            field=models.PositiveBigIntegerField(default=0),
        ),
    ]
