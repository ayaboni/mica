from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0086_guidancethread_history_start_id'),
    ]

    operations = [
        migrations.AddField(
            model_name='guidancethread',
            name='grace_notice_ack',
            field=models.BooleanField(default=False),
        ),
    ]
