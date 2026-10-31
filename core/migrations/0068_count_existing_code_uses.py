from django.db import migrations


def count_existing_uses(apps, schema_editor):
    """Codes made before shared codes existed were all single-use: a used one has spent its one use."""
    RegistrationCode = apps.get_model('core', 'RegistrationCode')
    RegistrationCode.objects.filter(used_at__isnull=False).update(use_count=1)


class Migration(migrations.Migration):
    # Kept apart from 0067 so the data change and the schema changes don't share a transaction.
    dependencies = [
        ('core', '0067_registration_code_kinds_and_verification'),
    ]

    operations = [
        migrations.RunPython(count_existing_uses, migrations.RunPython.noop),
    ]
