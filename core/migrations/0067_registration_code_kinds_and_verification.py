import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models
from django.db.models.functions import Lower


class Migration(migrations.Migration):
    """Registration codes grow up:

    * batches of single-use codes, and a shared term code with a use cap + expiry
    * accounts made with a shared code start 'unverified' and stay hidden from Guidance until
      a staff member approves them (new role permission: verify_students)

    Every existing student account stays 'verified'.
    """

    dependencies = [
        ('core', '0066_loginverification_email_change'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        # -- registration codes
        migrations.AlterField(
            model_name='registrationcode',
            name='code',
            field=models.CharField(max_length=24, unique=True),
        ),
        migrations.AddField(
            model_name='registrationcode',
            name='kind',
            field=models.CharField(choices=[('single', 'Single-use'), ('shared', 'Shared term code')],
                                   db_index=True, default='single', max_length=10),
        ),
        migrations.AddField(
            model_name='registrationcode',
            name='label',
            field=models.CharField(blank=True, max_length=80),
        ),
        migrations.AddField(
            model_name='registrationcode',
            name='batch',
            field=models.CharField(blank=True, db_index=True, max_length=32),
        ),
        migrations.AddField(
            model_name='registrationcode',
            name='max_uses',
            field=models.PositiveIntegerField(default=1),
        ),
        migrations.AddField(
            model_name='registrationcode',
            name='use_count',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='registrationcode',
            name='revoked_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='registrationcode',
            name='revoked_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                    related_name='+', to=settings.AUTH_USER_MODEL),
        ),

        # -- student verification
        migrations.AddField(
            model_name='userprofile',
            name='verification_status',
            field=models.CharField(choices=[('verified', 'Verified'), ('unverified', 'Unverified'),
                                            ('rejected', 'Rejected')],
                                   db_index=True, default='verified', max_length=10),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='verified_by',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                    related_name='+', to=settings.AUTH_USER_MODEL),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='verified_at',
            field=models.DateTimeField(blank=True, null=True),
        ),
        migrations.AddField(
            model_name='userprofile',
            name='registered_code',
            field=models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL,
                                    related_name='registrants', to='core.registrationcode'),
        ),
        migrations.RemoveConstraint(
            model_name='userprofile',
            name='unique_student_number_ci',
        ),
        migrations.AddConstraint(
            model_name='userprofile',
            constraint=models.UniqueConstraint(
                Lower('student_number'),
                condition=~models.Q(student_number='') & ~models.Q(verification_status='rejected'),
                name='unique_student_number_ci',
            ),
        ),

        # -- the new role permission
        migrations.AddField(
            model_name='staffsubclass',
            name='verify_students',
            field=models.BooleanField(default=False),
        ),
    ]
