"""Create a personal System Admin (superuser) account.

    python manage.py create_admin

Why this exists: the admin dashboard can create staff accounts but, on purpose, never superusers,
and Django's own `createsuperuser` skips MICA's rules (username format, names, unique email, a
stronger password). Give every person who needs admin access their own account made with this
command, instead of sharing one login: the audit log then shows who did what.

It asks for each value, and asks again when one is not acceptable. The password is typed at a
hidden prompt (never on the command line, so it stays out of shell history and process lists).

Non-interactive use (scripts, a platform shell):

    MICA_ADMIN_PASSWORD='...' python manage.py create_admin --noinput \
        --username ana.reyes --email ana@school.edu --first-name Ana --last-name Reyes
"""
import getpass
import os

from django.contrib.auth.models import User
from django.contrib.auth.password_validation import validate_password
from django.core.exceptions import ValidationError
from django.core.management.base import BaseCommand, CommandError
from django.core.validators import validate_email
from django.db import IntegrityError, transaction

from core.models import AuditEvent
from core.name_validation import normalize_name, validate_person_names, validate_username

MIN_ADMIN_PASSWORD_LENGTH = 12       # admins can read every student's records: stricter than the 8 for students
PASSWORD_ENV_VAR = "MICA_ADMIN_PASSWORD"


def username_problem(username):
    return validate_username(username) or (
        "That username is already taken." if User.objects.filter(username__iexact=username).exists() else None)


def email_problem(email):
    if not email:
        return "An email address is required (it is how this admin gets codes and password resets)."
    if len(email) > 254:
        return "That email address is too long."
    try:
        validate_email(email)
    except ValidationError:
        return "Please enter a valid email address."
    if User.objects.filter(email__iexact=email).exists():
        return "That email address is already used by another account."
    return None


def password_problem(password, user):
    if len(password) < MIN_ADMIN_PASSWORD_LENGTH:
        return "Admin passwords must be at least %d characters." % MIN_ADMIN_PASSWORD_LENGTH
    try:
        validate_password(password, user=user)       # the project's AUTH_PASSWORD_VALIDATORS
    except ValidationError as exc:
        return " ".join(exc.messages)
    return None


class Command(BaseCommand):
    help = "Create a personal System Admin (superuser) account that follows MICA's account rules."

    def add_arguments(self, parser):
        parser.add_argument("--username")
        parser.add_argument("--email")
        parser.add_argument("--first-name", dest="first_name")
        parser.add_argument("--last-name", dest="last_name")
        parser.add_argument("--noinput", "--no-input", action="store_true", dest="noinput",
                            help="Don't prompt. Needs all four options and the %s environment variable." % PASSWORD_ENV_VAR)

    # -- prompting ------------------------------------------------------------------------------

    def _ask(self, label, given, check, interactive, clean=lambda v: v.strip()):
        """The value for one field. A value passed as an option must be valid; a typed one is re-asked."""
        if given is not None:
            value = clean(given)
            problem = check(value)
            if problem:
                raise CommandError("%s: %s" % (label, problem))
            return value
        if not interactive:
            raise CommandError("--noinput needs --username, --email, --first-name and --last-name.")
        while True:
            try:
                value = clean(input("%s: " % label))
            except EOFError:
                raise CommandError("No input available. Pass the options and use --noinput instead.")
            problem = check(value)
            if not problem:
                return value
            self.stderr.write("  " + problem)

    def _names(self, options, interactive):
        first, last = options.get("first_name"), options.get("last_name")
        if first is not None and last is not None:
            first, last = normalize_name(first), normalize_name(last)
            problem = validate_person_names(first, last)
            if problem:
                raise CommandError(problem)
            return first, last
        if not interactive:
            raise CommandError("--noinput needs --username, --email, --first-name and --last-name.")
        while True:
            try:
                first, last = normalize_name(input("First name: ")), normalize_name(input("Last name: "))
            except EOFError:
                raise CommandError("No input available. Pass the options and use --noinput instead.")
            problem = validate_person_names(first, last)
            if not problem:
                return first, last
            self.stderr.write("  " + problem)

    def _password(self, candidate, interactive):
        if not interactive:
            password = os.environ.get(PASSWORD_ENV_VAR, "")
            problem = password_problem(password, candidate) if password else "%s is not set." % PASSWORD_ENV_VAR
            if problem:
                raise CommandError("Password: %s" % problem)
            return password
        while True:
            try:
                password = getpass.getpass("Password: ")
                again = getpass.getpass("Password (again): ")
            except EOFError:
                raise CommandError("No input available. Use --noinput with %s instead." % PASSWORD_ENV_VAR)
            problem = "The two passwords didn't match." if password != again else password_problem(password, candidate)
            if not problem:
                return password
            self.stderr.write("  " + problem)

    # -- the command ----------------------------------------------------------------------------

    def handle(self, *args, **options):
        interactive = not options["noinput"]
        username = self._ask("Username", options.get("username"), username_problem, interactive)
        email = self._ask("Email", options.get("email"), email_problem, interactive,
                          clean=lambda v: v.strip().lower())
        first, last = self._names(options, interactive)
        candidate = User(username=username, email=email, first_name=first, last_name=last)
        password = self._password(candidate, interactive)

        try:
            with transaction.atomic():
                user = User.objects.create_superuser(username=username, email=email, password=password,
                                                     first_name=first, last_name=last)
                AuditEvent.objects.create(
                    actor=None, event="admin_action",
                    detail="System Admin account created from the server: @%s" % user.username)
        except IntegrityError:
            raise CommandError("That username was just taken. Run the command again.")

        self.stdout.write(self.style.SUCCESS(
            "System Admin @%s created (%s). They can sign in now with their own username and password." % (user.username, email)))
        self.stdout.write(
            "Next: have them sign in, and once everyone has a personal account, change the password of "
            "the old shared admin account (or deactivate it) so it stops being used.")
