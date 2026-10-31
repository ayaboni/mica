from django.conf import settings
from django.core.management import call_command
from django.db import migrations


def create_cache_table(apps, schema_editor):
    """Create the table behind the database cache (used for the registration-code lockout counters).

    `migrate` never makes this table by itself, so without this every code entry on /register/
    fails with: relation "django_cache" does not exist. Does nothing when Redis is in use.
    """
    cache = settings.CACHES["default"]
    if cache["BACKEND"].endswith("DatabaseCache"):
        call_command("createcachetable", cache["LOCATION"],
                     database=schema_editor.connection.alias, verbosity=0)


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0069_userprofile_email_verified_at'),
    ]

    operations = [
        migrations.RunPython(create_cache_table, migrations.RunPython.noop),
    ]
