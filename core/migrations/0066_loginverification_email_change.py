from django.db import migrations, models


class Migration(migrations.Migration):
    """Adds the 'Email change' purpose, so a code e-mailed for changing the account's email
    address can't be used for signing in or for changing a password (and vice versa)."""

    dependencies = [
        ('core', '0065_staffsubclass_referrals'),
    ]

    operations = [
        migrations.AlterField(
            model_name='loginverification',
            name='purpose',
            field=models.CharField(
                choices=[('login', 'Sign-in'), ('password_change', 'Password change'),
                         ('email_change', 'Email change')],
                default='login', max_length=20),
        ),
    ]
