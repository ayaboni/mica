from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        ('core', '0028_accesslog'),
    ]

    operations = [
        # An earlier build of this feature (also named 0025_loginverification) may
        # already have created this table in your database. It only holds short-lived
        # sign-in links, so drop any leftover copy and let CreateModel make it fresh.
        migrations.RunSQL(
            sql="DROP TABLE IF EXISTS core_loginverification",
            reverse_sql=migrations.RunSQL.noop,
        ),
        migrations.CreateModel(
            name='LoginVerification',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('token_hash', models.CharField(max_length=64, unique=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('expires_at', models.DateTimeField()),
                ('used_at', models.DateTimeField(blank=True, null=True)),
                ('remember_me', models.BooleanField(default=False)),
                ('method', models.CharField(choices=[('password', 'Password'), ('google', 'Google')], default='password', max_length=10)),
                ('request_ip', models.GenericIPAddressField(blank=True, null=True)),
                ('user', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='login_verifications', to=settings.AUTH_USER_MODEL)),
            ],
        ),
    ]
