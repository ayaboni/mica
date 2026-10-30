from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0062_userprofile_dark_mode'),
    ]

    operations = [
        migrations.AddField(
            model_name='studentnote',
            name='referral_opted_out',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='studentnote',
            name='referral_opted_out_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='studentnote',
            name='referral_handover_seen',
            field=models.BooleanField(default=True),
        ),
    ]
