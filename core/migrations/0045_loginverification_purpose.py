from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("core", "0044_loginverification_code"),
    ]

    operations = [
        migrations.AddField(
            model_name="loginverification",
            name="purpose",
            field=models.CharField(choices=[("login", "Sign-in"), ("password_change", "Password change")],
                                   default="login", max_length=20),
        ),
    ]
