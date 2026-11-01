import os
from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TestCase

from .models import AuditEvent

GOOD_PW = "Correct-horse-battery-9"


def run(**options):
    out, err = StringIO(), StringIO()
    options.setdefault("noinput", True)
    call_command("create_admin", stdout=out, stderr=err, **options)
    return out.getvalue(), err.getvalue()


class CreateAdminNonInteractiveTests(TestCase):
    def setUp(self):
        self.shared = User.objects.create_superuser("admin", "shared@example.com", "Shared-pw-123!")
        patcher = patch.dict(os.environ, {"MICA_ADMIN_PASSWORD": GOOD_PW})
        patcher.start()
        self.addCleanup(patcher.stop)

    def ok(self, **extra):
        values = dict(username="ana.reyes", email="Ana@School.edu", first_name="Ana", last_name="Reyes")
        values.update(extra)
        return run(**values)

    def test_creates_a_personal_superuser_with_the_right_details(self):
        out, _ = self.ok()
        user = User.objects.get(username="ana.reyes")
        self.assertTrue(user.is_superuser and user.is_staff and user.is_active)
        self.assertEqual(user.email, "ana@school.edu")                   # stored lower-case
        self.assertEqual((user.first_name, user.last_name), ("Ana", "Reyes"))
        self.assertTrue(user.check_password(GOOD_PW))
        self.assertNotIn(GOOD_PW, out)                                   # the password is never printed

    def test_it_is_recorded_in_the_audit_log_without_the_password(self):
        self.ok()
        event = AuditEvent.objects.get(event="admin_action")
        self.assertIsNone(event.actor)
        self.assertIn("ana.reyes", event.detail)
        self.assertNotIn(GOOD_PW, event.detail)

    def test_the_existing_shared_admin_is_left_alone(self):
        self.ok()
        self.shared.refresh_from_db()
        self.assertTrue(self.shared.check_password("Shared-pw-123!") and self.shared.is_active)

    def test_refuses_a_taken_username_in_any_case_and_a_taken_email(self):
        with self.assertRaises(CommandError):
            self.ok(username="ADMIN")
        with self.assertRaises(CommandError):
            self.ok(email="SHARED@example.com")
        self.assertEqual(User.objects.count(), 1)

    def test_refuses_bad_usernames_emails_and_names(self):
        for extra in [dict(username="<img src=x>"), dict(username="ab"), dict(username="a@b.com"),
                      dict(email="not-an-email"), dict(email=""),
                      dict(first_name="<b>Ana</b>"), dict(last_name=""), dict(first_name="A" * 200)]:
            with self.assertRaises(CommandError, msg=str(extra)):
                self.ok(**extra)
        self.assertEqual(User.objects.count(), 1)

    def test_refuses_weak_passwords(self):
        for weak in ["Short-1!", "123456789012", "ana.reyes2026", ""]:      # too short, all digits, like the username, unset
            with patch.dict(os.environ, {"MICA_ADMIN_PASSWORD": weak}):
                with self.assertRaises(CommandError, msg=weak):
                    self.ok()
        self.assertEqual(User.objects.count(), 1)

    def test_noinput_needs_every_option_and_the_password_variable(self):
        with self.assertRaises(CommandError):
            run(username="ana.reyes", email="ana@school.edu")            # names missing
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(CommandError):
                self.ok()                                                # no MICA_ADMIN_PASSWORD


class CreateAdminInteractiveTests(TestCase):
    def test_asks_again_after_a_bad_answer_and_hides_the_password(self):
        User.objects.create_user("taken", "taken@example.com", "x-Pw-123456!")
        answers = ["taken", "<x>", "ana.reyes",              # username: taken, invalid, then fine
                   "nope", "ana@school.edu",                  # email: invalid, then fine
                   "<b>", "Reyes",                            # first name invalid -> asks both names again
                   "Ana", "Reyes"]
        passwords = ["Short-1!", "Short-1!",                  # too short
                     GOOD_PW, "something else",               # doesn't match
                     GOOD_PW, GOOD_PW]
        out, err = StringIO(), StringIO()
        with patch("builtins.input", side_effect=answers), patch("getpass.getpass", side_effect=passwords):
            call_command("create_admin", stdout=out, stderr=err)
        user = User.objects.get(username="ana.reyes")
        self.assertTrue(user.is_superuser and user.check_password(GOOD_PW))
        self.assertIn("already taken", err.getvalue())
        self.assertIn("at least 12", err.getvalue())
        self.assertIn("didn't match", err.getvalue())
        self.assertNotIn(GOOD_PW, out.getvalue() + err.getvalue())

    def test_gives_up_cleanly_when_there_is_no_input(self):
        with patch("builtins.input", side_effect=EOFError):
            with self.assertRaises(CommandError):
                call_command("create_admin", stdout=StringIO(), stderr=StringIO())
