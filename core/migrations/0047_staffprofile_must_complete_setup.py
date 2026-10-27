from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0046_auditevent_sitesettings"),
    ]

    operations = [
        migrations.AddField(
            model_name="staffprofile",
            name="must_complete_setup",
            field=models.BooleanField(default=False),
        ),
    ]
