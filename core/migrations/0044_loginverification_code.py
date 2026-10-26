from django.db import migrations, models
from django.utils import timezone


def retire_old_links(apps, schema_editor):
    """E-mailed links no longer work; make sure none can be mistaken for a code."""
    LoginVerification = apps.get_model("core", "LoginVerification")
    LoginVerification.objects.filter(used_at__isnull=True).update(used_at=timezone.now())


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0043_semester"),
    ]

    operations = [
        migrations.RenameField("loginverification", "token_hash", "code_hash"),
        # Codes are short, so two different users (or days) can share one: not unique any more.
        migrations.AlterField("loginverification", "code_hash", models.CharField(max_length=64)),
        migrations.AddField("loginverification", "attempts", models.PositiveSmallIntegerField(default=0)),
        migrations.RunPython(retire_old_links, migrations.RunPython.noop),
    ]
