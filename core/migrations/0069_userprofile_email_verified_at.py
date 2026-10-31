from django.db import migrations, models
from django.db.models import OuterRef, Subquery


def backfill_verified(apps, schema_editor):
    """Anyone who has already signed in got past the sign-in code (or predates it): their e-mail is verified."""
    UserProfile = apps.get_model('core', 'UserProfile')
    User = apps.get_model('auth', 'User')
    last_login = User.objects.filter(pk=OuterRef('user_id')).values('last_login')[:1]
    UserProfile.objects.filter(
        email_verified_at__isnull=True, user__last_login__isnull=False,
    ).update(email_verified_at=Subquery(last_login))


class Migration(migrations.Migration):

    dependencies = [
        ('auth', '0012_alter_user_first_name_max_length'),
        ('core', '0068_count_existing_code_uses'),
    ]

    operations = [
        migrations.AddField(
            model_name='userprofile',
            name='email_verified_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.RunPython(backfill_verified, migrations.RunPython.noop),
    ]
