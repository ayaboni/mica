"""Remove a System Admin's authenticator so they can set a new one.

    python manage.py reset_admin_2fa --username ana.reyes

For when an admin has lost their phone AND their recovery codes (or the server's secret key changed
and the stored authenticator can no longer be read). Whoever can run this has server access, which
is already the highest level of trust, so it asks for nothing more and leaves a record in the audit
log. The admin's next sign-in is the first-time one: password, an e-mailed code, then a fresh
authenticator and new recovery codes.
"""
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError

from core import twofactor
from core.models import AuditEvent, LoginThrottle


class Command(BaseCommand):
    help = "Remove a System Admin's authenticator and recovery codes so they can set up a new one."

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)

    def handle(self, *args, **options):
        user = User.objects.filter(username__iexact=options["username"].strip(), is_superuser=True).first()
        if user is None:
            raise CommandError("No System Admin account with that username.")
        had_device = twofactor.confirmed_device(user) is not None
        twofactor.remove_device(user)
        LoginThrottle.objects.filter(username_key=twofactor.throttle_key(user)).delete()
        AuditEvent.objects.create(
            actor=None, event="admin_action",
            detail="Two-factor reset from the server for System Admin @%s" % user.username)
        self.stdout.write(self.style.SUCCESS(
            "Two-factor sign-in reset for @%s%s." % (user.username, "" if had_device else " (they had none set up)")))
        if not user.email:
            self.stdout.write(self.style.WARNING(
                "This account has no email address, and the first sign-in after a reset needs one to send "
                "its code. Add one first (python manage.py shell)."))
        else:
            self.stdout.write("Next sign-in: password, an emailed code sent to %s, then set up the new authenticator." % user.email)
