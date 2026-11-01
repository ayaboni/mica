import django.db.models.deletion
from django.conf import settings
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0072_consolenotification'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='TotpDevice',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('secret_enc', models.TextField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('confirmed_at', models.DateTimeField(blank=True, null=True)),
                ('last_used_step', models.BigIntegerField(default=0)),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='totp_device', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.CreateModel(
            name='RecoveryCode',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code_hash', models.CharField(db_index=True, max_length=64)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('used_at', models.DateTimeField(blank=True, null=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='recovery_codes', to=settings.AUTH_USER_MODEL)),
            ],
        ),
        migrations.AlterField(
            model_name='signinfailure',
            name='reason',
            field=models.CharField(choices=[('password', 'Wrong password'), ('code', 'Wrong e-mail code'), ('totp', 'Wrong authenticator code')], max_length=10),
        ),
    ]
