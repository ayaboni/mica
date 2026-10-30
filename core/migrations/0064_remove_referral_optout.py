from django.db import migrations


class Migration(migrations.Migration):
    """Referral hand-overs are now automatic when the referral is accepted, so the opt-out fields
    added in 0063 are no longer needed."""

    dependencies = [
        ('core', '0063_studentnote_referral_handover'),
    ]

    operations = [
        migrations.RemoveField(model_name='studentnote', name='referral_opted_out'),
        migrations.RemoveField(model_name='studentnote', name='referral_opted_out_at'),
        migrations.RemoveField(model_name='studentnote', name='referral_handover_seen'),
    ]
