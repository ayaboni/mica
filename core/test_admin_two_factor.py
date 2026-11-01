"""System Admin two-factor sign-in (authenticator app + recovery codes) and the per-address
sign-in limit. See core/twofactor.py, core/two_factor_views.py and core/middleware.py.

ADMIN_TWO_FACTOR_REQUIRED is off while tests run (so the many tests that log an admin in directly
keep working); every test here switches it back on.
"""
import re
from datetime import timedelta
from io import StringIO
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import mail
from django.core.management import call_command
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import totp, twofactor, views
from .models import (AuditEvent, LoginThrottle, LoginVerification, RecoveryCode, SignInFailure, TotpDevice,
                     UserProfile)

STEP = 60_000_000          # an arbitrary "now", in 30-second steps. current_step is patched to this.


def code_for(secret, step):
    return totp._code_for_step(secret, step)


def at(step):
    """Pretend it is this 30-second step (affects only the authenticator check)."""
    return patch("core.totp.current_step", return_value=step)


@override_settings(ADMIN_TWO_FACTOR_REQUIRED=True,
                   EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend", SITE_URL="https://mica.test")
class AdminTwoFactorTests(TestCase):
    PASSWORD = "Admin-pass-123!"

    def setUp(self):
        self.admin = User.objects.create_superuser("boss", "boss@example.com", self.PASSWORD,
                                                   first_name="Ana", last_name="Reyes")

    # -- helpers --
    def signed_in(self):
        return "_auth_user_id" in self.client.session

    def password_login(self, username="boss", password=None):
        return self.client.post(reverse("login"), {"username": username, "password": password or self.PASSWORD})

    def email_code(self):
        return re.search(r"^([A-Z2-9]{6})$", mail.outbox[-1].body, re.M).group(1)

    def secret(self):
        return twofactor.device_secret(TotpDevice.objects.get(user=self.admin))

    def enroll(self, step=STEP):
        """First sign-in all the way through setup. Returns (recovery codes, secret)."""
        self.password_login()
        self.client.post(reverse("login_verify"), {"code": self.email_code()})
        self.client.get(reverse("two_factor_setup"))
        secret = self.secret()
        with at(step):
            response = self.client.post(reverse("two_factor_setup"), {"code": code_for(secret, step)})
        self.assertEqual(response.status_code, 200)
        return response.context["codes"], secret

    def sign_out(self):
        self.client.logout()

    def totp_sign_in(self, secret, step, code=None):
        self.assertRedirects(self.password_login(), reverse("login_totp"), fetch_redirect_response=False)
        with at(step):
            return self.client.post(reverse("login_totp"), {"code": code or code_for(secret, step)})

    # -- first sign-in: e-mailed code, then forced setup --
    def test_password_alone_does_not_sign_an_admin_in(self):
        response = self.password_login()
        self.assertRedirects(response, reverse("login_check_email"), fetch_redirect_response=False)
        self.assertFalse(self.signed_in())
        self.assertEqual(len(mail.outbox), 1)

    def test_wrong_password_is_still_refused(self):
        self.password_login(password="nope")
        self.assertFalse(self.signed_in())
        self.assertEqual(len(mail.outbox), 0)

    def test_after_the_email_code_only_the_setup_page_opens(self):
        self.password_login()
        response = self.client.post(reverse("login_verify"), {"code": self.email_code()})
        self.assertRedirects(response, reverse("two_factor_setup"), fetch_redirect_response=False)
        self.assertEqual(self.client.get(reverse("two_factor_setup")).status_code, 200)
        self.assertRedirects(self.client.get(reverse("admin_dashboard")), reverse("two_factor_setup"),
                             fetch_redirect_response=False)
        api = self.client.get(reverse("admin_dashboard_stats"))
        self.assertEqual(api.status_code, 403)
        self.assertTrue(api.json()["two_factor_setup_required"])

    def test_setup_needs_a_right_code_and_then_shows_recovery_codes_once(self):
        self.password_login()
        self.client.post(reverse("login_verify"), {"code": self.email_code()})
        self.client.get(reverse("two_factor_setup"))
        secret = self.secret()
        with at(STEP):
            wrong = self.client.post(reverse("two_factor_setup"), {"code": "000000"})
        self.assertEqual(wrong.status_code, 200)
        self.assertFalse(twofactor.has_confirmed_device(self.admin))

        with at(STEP):
            done = self.client.post(reverse("two_factor_setup"), {"code": code_for(secret, STEP)})
        self.assertEqual(done.status_code, 200)
        codes = done.context["codes"]
        self.assertEqual(len(codes), twofactor.RECOVERY_CODE_COUNT)
        self.assertTrue(all(re.fullmatch(r"[A-Z2-9]{5}-[A-Z2-9]{5}", c) for c in codes))
        self.assertTrue(twofactor.has_confirmed_device(self.admin))
        self.assertEqual(self.client.get(reverse("admin_dashboard")).status_code, 200)
        self.assertTrue(AuditEvent.objects.filter(actor=self.admin, detail__contains="turned on two-factor").exists())

    def test_secret_and_recovery_codes_are_not_stored_readably(self):
        codes, secret = self.enroll()
        device = TotpDevice.objects.get(user=self.admin)
        self.assertNotIn(secret, device.secret_enc)
        stored = set(RecoveryCode.objects.filter(user=self.admin).values_list("code_hash", flat=True))
        self.assertEqual(len(stored), twofactor.RECOVERY_CODE_COUNT)
        for c in codes:
            self.assertNotIn(c, stored)
            self.assertNotIn(c.replace("-", ""), stored)

    def test_setup_page_keeps_the_same_secret_when_reloaded(self):
        self.password_login()
        self.client.post(reverse("login_verify"), {"code": self.email_code()})
        first = self.client.get(reverse("two_factor_setup")).context["secret_display"]
        second = self.client.get(reverse("two_factor_setup")).context["secret_display"]
        self.assertEqual(first, second)

    # -- every later sign-in: password, then the authenticator --
    def test_password_then_authenticator_code_signs_in(self):
        _, secret = self.enroll()
        self.sign_out()
        response = self.totp_sign_in(secret, STEP + 2)
        self.assertRedirects(response, reverse("admin_dashboard"), fetch_redirect_response=False)
        self.assertTrue(self.signed_in())
        self.assertEqual(self.client.get(reverse("admin_dashboard")).status_code, 200)
        self.assertEqual(len(mail.outbox), 1)          # only the very first sign-in sent an email

    def test_password_alone_does_not_sign_in_once_set_up(self):
        self.enroll()
        self.sign_out()
        self.assertRedirects(self.password_login(), reverse("login_totp"), fetch_redirect_response=False)
        self.assertFalse(self.signed_in())
        self.assertEqual(self.client.get(reverse("admin_dashboard")).status_code, 302)

    def test_a_used_code_cannot_be_used_again(self):
        _, secret = self.enroll()
        self.sign_out()
        self.totp_sign_in(secret, STEP + 2)
        self.sign_out()
        response = self.totp_sign_in(secret, STEP + 2)          # the very same code
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.signed_in())

    def test_the_code_used_to_finish_setup_cannot_sign_in_straight_after(self):
        _, secret = self.enroll(step=STEP)
        self.sign_out()
        response = self.totp_sign_in(secret, STEP)
        self.assertFalse(self.signed_in())
        self.assertEqual(response.status_code, 200)

    def test_a_wrong_code_is_refused_recorded_and_counted(self):
        _, secret = self.enroll()
        self.sign_out()
        response = self.totp_sign_in(secret, STEP + 2, code="123456")
        self.assertEqual(response.status_code, 200)
        self.assertFalse(self.signed_in())
        self.assertEqual(SignInFailure.objects.filter(user=self.admin, reason="totp").count(), 1)

    def test_five_wrong_codes_lock_the_code_page_even_for_the_right_code(self):
        _, secret = self.enroll()
        self.sign_out()
        self.assertRedirects(self.password_login(), reverse("login_totp"), fetch_redirect_response=False)
        with at(STEP + 2):
            for _ in range(twofactor.TOTP_MAX_ATTEMPTS):
                response = self.client.post(reverse("login_totp"), {"code": "123456"})
            self.assertEqual(response.status_code, 429)
            right = self.client.post(reverse("login_totp"), {"code": code_for(secret, STEP + 2)})
        self.assertEqual(right.status_code, 429)
        self.assertFalse(self.signed_in())

    def test_the_code_page_needs_the_password_step_first(self):
        self.enroll()
        self.sign_out()
        self.assertRedirects(self.client.get(reverse("login_totp")), reverse("login"), fetch_redirect_response=False)
        response = self.client.post(reverse("login_totp"), {"code": "123456"})
        self.assertRedirects(response, reverse("login"), fetch_redirect_response=False)

    def test_the_code_page_expires_after_five_minutes(self):
        self.enroll()
        self.sign_out()
        self.password_login()
        session = self.client.session
        session[twofactor.MFA_PENDING_AT_KEY] -= twofactor.PENDING_MAX_SECONDS + 5
        session.save()
        self.assertRedirects(self.client.get(reverse("login_totp")), reverse("login"), fetch_redirect_response=False)

    def test_trusted_browser_cookie_does_not_skip_the_authenticator(self):
        _, secret = self.enroll()
        self.sign_out()
        response = self.client.post(reverse("login"), {"username": "boss", "password": self.PASSWORD,
                                                       "remember_me": "on"})
        self.assertRedirects(response, reverse("login_totp"), fetch_redirect_response=False)
        self.assertFalse(self.signed_in())

    def test_an_admin_without_an_email_cannot_be_enrolled_and_is_told_how_to_get_help(self):
        User.objects.filter(pk=self.admin.pk).update(email="")
        response = self.password_login()
        self.assertEqual(response.status_code, 403)
        self.assertFalse(self.signed_in())
        self.assertContains(response, "reset_admin_2fa", status_code=403)

    # -- recovery codes --
    def test_a_recovery_code_signs_in_once(self):
        codes, secret = self.enroll()
        self.sign_out()
        response = self.totp_sign_in(secret, STEP + 2, code=codes[0])
        self.assertRedirects(response, reverse("two_factor_manage"), fetch_redirect_response=False)
        self.assertTrue(self.signed_in())
        self.assertEqual(twofactor.recovery_codes_left(self.admin), twofactor.RECOVERY_CODE_COUNT - 1)
        self.assertTrue(AuditEvent.objects.filter(detail__contains="recovery code").exists())

        self.sign_out()
        again = self.totp_sign_in(secret, STEP + 4, code=codes[0])
        self.assertEqual(again.status_code, 200)
        self.assertFalse(self.signed_in())

    def test_recovery_codes_ignore_case_and_the_dash(self):
        codes, secret = self.enroll()
        self.sign_out()
        self.totp_sign_in(secret, STEP + 2, code=" " + codes[1].lower().replace("-", " ") + " ")
        self.assertTrue(self.signed_in())

    def test_another_admins_recovery_code_does_not_work(self):
        codes, secret = self.enroll()
        other = User.objects.create_superuser("second", "second@example.com", self.PASSWORD)
        other_codes = twofactor.new_recovery_codes(other)
        self.sign_out()
        self.totp_sign_in(secret, STEP + 2, code=other_codes[0])
        self.assertFalse(self.signed_in())

    # -- no way round it --
    def test_a_session_made_without_the_second_factor_is_signed_out(self):
        self.enroll()
        self.sign_out()
        self.client.force_login(self.admin)          # what any password-only login would leave behind
        response = self.client.get(reverse("admin_dashboard"))
        self.assertRedirects(response, reverse("login") + "?twofactor=1", fetch_redirect_response=False)
        self.assertFalse(self.signed_in())

    def test_the_api_answers_401_instead_of_redirecting(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("admin_dashboard_stats"))
        self.assertEqual(response.status_code, 401)
        self.assertTrue(response.json()["two_factor_required"])

    def test_the_django_admin_login_form_is_closed(self):
        response = self.client.post("/admin/login/", {"username": "boss", "password": self.PASSWORD})
        self.assertRedirects(response, reverse("login"), fetch_redirect_response=False)
        self.assertFalse(self.signed_in())
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get("/admin/").status_code, 302)
        self.assertFalse(self.signed_in())

    def test_setup_page_is_not_reachable_without_proving_who_you_are(self):
        self.client.force_login(self.admin)
        response = self.client.get(reverse("two_factor_setup"))
        self.assertRedirects(response, reverse("login") + "?twofactor=1", fetch_redirect_response=False)

    def test_an_emailed_sign_in_code_cannot_replace_the_authenticator_once_set_up(self):
        self.enroll()
        self.sign_out()
        session = self.client.session
        session["pending_login_user_id"] = self.admin.pk
        session.save()
        LoginVerification.objects.create(user=self.admin, purpose="login", code_hash=views._hash_code(self.admin, "ABCDEF"),
                                         expires_at=timezone.now() + timedelta(minutes=5))
        response = self.client.post(reverse("login_verify"), {"code": "ABCDEF"})
        self.assertRedirects(response, reverse("login"), fetch_redirect_response=False)
        self.assertFalse(self.signed_in())

    def test_logout_is_always_reachable(self):
        self.client.force_login(self.admin)
        self.assertRedirects(self.client.get(reverse("logout")), reverse("login"), fetch_redirect_response=False)

    def test_students_and_staff_are_not_asked_for_an_authenticator(self):
        staff = User.objects.create_user("coach", "coach@example.com", self.PASSWORD, is_staff=True)
        UserProfile.objects.create(user=staff)
        self.password_login(username="coach")
        self.assertNotIn(twofactor.MFA_PENDING_KEY, self.client.session)
        self.assertEqual(len(mail.outbox), 1)           # the normal e-mailed code, as before

    # -- managing it --
    def test_new_recovery_codes_need_a_right_code_and_replace_the_old_ones(self):
        codes, secret = self.enroll()
        with at(STEP + 2):
            wrong = self.client.post(reverse("two_factor_manage"), {"action": "regenerate", "code": "000000"})
        self.assertRedirects(wrong, reverse("two_factor_manage"), fetch_redirect_response=False)
        self.assertEqual(RecoveryCode.objects.filter(user=self.admin, used_at__isnull=True).count(),
                         twofactor.RECOVERY_CODE_COUNT)

        with at(STEP + 2):
            done = self.client.post(reverse("two_factor_manage"),
                                    {"action": "regenerate", "code": code_for(secret, STEP + 2)})
        self.assertEqual(done.status_code, 200)
        new_codes = done.context["codes"]
        self.assertEqual(len(new_codes), twofactor.RECOVERY_CODE_COUNT)
        self.assertFalse(set(new_codes) & set(codes))
        self.sign_out()
        self.totp_sign_in(secret, STEP + 4, code=codes[0])         # an old code
        self.assertFalse(self.signed_in())

    def test_switching_to_a_new_authenticator(self):
        codes, secret = self.enroll()
        with at(STEP + 2):
            response = self.client.post(reverse("two_factor_manage"),
                                        {"action": "replace", "code": code_for(secret, STEP + 2)})
        self.assertRedirects(response, reverse("two_factor_setup"), fetch_redirect_response=False)
        self.assertFalse(twofactor.has_confirmed_device(self.admin))
        self.assertEqual(RecoveryCode.objects.filter(user=self.admin).count(), 0)
        # Until the new one is confirmed they can reach nothing but the setup page.
        self.assertRedirects(self.client.get(reverse("admin_dashboard")), reverse("two_factor_setup"),
                             fetch_redirect_response=False)
        self.client.get(reverse("two_factor_setup"))
        new_secret = self.secret()
        self.assertNotEqual(new_secret, secret)
        with at(STEP + 4):
            done = self.client.post(reverse("two_factor_setup"), {"code": code_for(new_secret, STEP + 4)})
        self.assertEqual(done.status_code, 200)
        self.sign_out()
        self.totp_sign_in(secret, STEP + 6)                         # the old phone no longer works
        self.assertFalse(self.signed_in())

    def test_manage_page_shows_status_and_warns_when_codes_run_low(self):
        codes, secret = self.enroll()
        keep = RecoveryCode.objects.filter(user=self.admin).first()
        RecoveryCode.objects.filter(user=self.admin).exclude(pk=keep.pk).update(used_at=timezone.now())
        response = self.client.get(reverse("two_factor_manage"))
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.context["enabled"])
        self.assertEqual(response.context["codes_left"], 1)
        self.assertTrue(response.context["codes_low"])

    # -- the server-side way out --
    def test_reset_admin_2fa_command_starts_the_admin_over(self):
        self.enroll()
        self.sign_out()
        out = StringIO()
        call_command("reset_admin_2fa", username="boss", stdout=out)
        self.assertFalse(TotpDevice.objects.filter(user=self.admin).exists())
        self.assertFalse(RecoveryCode.objects.filter(user=self.admin).exists())
        self.assertTrue(AuditEvent.objects.filter(actor=None, detail__contains="Two-factor reset").exists())
        self.assertRedirects(self.password_login(), reverse("login_check_email"), fetch_redirect_response=False)

    def test_reset_admin_2fa_only_works_on_admins(self):
        User.objects.create_user("juan", "juan@example.com", self.PASSWORD)
        from django.core.management.base import CommandError
        with self.assertRaises(CommandError):
            call_command("reset_admin_2fa", username="juan", stdout=StringIO())


@override_settings(EMAIL_BACKEND="django.core.mail.backends.locmem.EmailBackend")
class PerAddressSignInLimitTests(TestCase):
    PASSWORD = "Right-pw1"

    def setUp(self):
        self.student = User.objects.create_user("lena", "lena@example.com", self.PASSWORD)
        UserProfile.objects.create(user=self.student)
        patcher = patch.object(views, "LOGIN_IP_MAX_FAILURES", 3)
        patcher.start()
        self.addCleanup(patcher.stop)

    def fail(self, name, ip="10.0.0.1"):
        return self.client.post(reverse("login"), {"username": name, "password": "nope"}, REMOTE_ADDR=ip)

    def test_many_usernames_from_one_address_lock_that_address(self):
        for name in ("a1", "a2"):
            self.assertEqual(self.fail(name).status_code, 200)
        locked = self.fail("a3")
        self.assertEqual(locked.status_code, 429)
        self.assertTrue(locked.context["ip_lockout"])
        # Even a real account with the right password is refused from that address while it lasts.
        response = self.client.post(reverse("login"), {"username": "lena", "password": self.PASSWORD},
                                    REMOTE_ADDR="10.0.0.1")
        self.assertEqual(response.status_code, 429)
        self.assertEqual(len(mail.outbox), 0)

    def test_other_addresses_are_not_affected(self):
        for name in ("a1", "a2", "a3"):
            self.fail(name)
        response = self.client.post(reverse("login"), {"username": "lena", "password": self.PASSWORD},
                                    REMOTE_ADDR="10.0.0.2")
        self.assertRedirects(response, reverse("login_check_email"), fetch_redirect_response=False)

    def test_the_lock_ends(self):
        for name in ("a1", "a2", "a3"):
            self.fail(name)
        LoginThrottle.objects.filter(username_key="ip:10.0.0.1").update(locked_until=timezone.now() - timedelta(seconds=1))
        response = self.client.post(reverse("login"), {"username": "lena", "password": self.PASSWORD},
                                    REMOTE_ADDR="10.0.0.1")
        self.assertRedirects(response, reverse("login_check_email"), fetch_redirect_response=False)

    def test_a_good_sign_in_does_not_reset_the_address_count(self):
        self.fail("a1")
        self.fail("a2")
        self.client.post(reverse("login"), {"username": "lena", "password": self.PASSWORD}, REMOTE_ADDR="10.0.0.1")
        self.assertEqual(self.fail("a3").status_code, 429)

    def test_the_per_username_lock_still_works_on_its_own(self):
        for _ in range(views.LOGIN_MAX_ATTEMPTS - 1):
            self.assertEqual(self.fail("lena", ip="10.0.0.%d" % (_ + 10)).status_code, 200)
        last = self.fail("lena", ip="10.0.0.99")
        self.assertEqual(last.status_code, 429)
        self.assertFalse(last.context.get("ip_lockout"))

    def test_address_rows_do_not_count_as_locked_usernames_on_the_admin_dashboard(self):
        for name in ("a1", "a2", "a3"):
            self.fail(name)
        admin = User.objects.create_superuser("boss", "boss@example.com", "Admin-pass-123!")
        self.client.force_login(admin)
        self.assertEqual(self.client.get(reverse("admin_signins")).json()["locked_now"], 0)
