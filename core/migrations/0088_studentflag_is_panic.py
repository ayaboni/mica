from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0087_guidancethread_grace_notice_ack'),
    ]

    operations = [
        migrations.AddField(
            model_name='studentflag',
            name='is_panic',
            field=models.BooleanField(default=False),
        ),
    ]
