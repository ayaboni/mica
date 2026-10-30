from django.db import migrations, models


class Migration(migrations.Migration):
    """Referrals become a switch in the admin's Roles & Permissions. Existing roles keep working
    exactly as before, so they all start with it on."""

    dependencies = [
        ('core', '0064_remove_referral_optout'),
    ]

    operations = [
        migrations.AddField(
            model_name='staffsubclass',
            name='referrals',
            field=models.BooleanField(default=True),
        ),
    ]
