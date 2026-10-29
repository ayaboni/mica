from django.db import migrations, models


def accept_existing_referrals(apps, schema_editor):
    """Referrals written before staff could answer them count as already accepted."""
    StudentNote = apps.get_model('core', 'StudentNote')
    StudentNote.objects.filter(referred_to__isnull=False).update(referral_status='accepted')


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0059_copy_breathe_sessions'),
    ]

    operations = [
        migrations.AddField(
            model_name='studentnote',
            name='referral_status',
            field=models.CharField(choices=[('pending', 'Pending'), ('accepted', 'Accepted'), ('declined', 'Declined')],
                                   default='pending', max_length=10),
        ),
        migrations.AddField(
            model_name='studentnote',
            name='referral_responded_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='studentnote',
            name='referral_decline_reason',
            field=models.CharField(blank=True, max_length=200),
        ),
        migrations.AddField(
            model_name='studentnote',
            name='referral_response_seen',
            field=models.BooleanField(default=True),
        ),
        migrations.RunPython(accept_existing_referrals, migrations.RunPython.noop),
    ]
