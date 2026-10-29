import json
import re
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import LoginVerification, UserProfile
from .views import firebase_auth
from .regcode_testing import grant_registration_code


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', SITE_URL='https://mica.test')
class EmailSignInCodeTests(TestCase):
    """Password sign-in must be confirmed by an e-mailed 6-character code, unless the
    browser was remembered."""
    PASSWORD = 'Right-pw1'

    def setUp(self):
        self.user = User.objects.create_user('lena', email='lena@example.com',
                                             password=self.PASSWORD, first_name='Lena')
        UserProfile.objects.create(user=self.user)
        self.cookie_name = f'mica_tb_{self.user.pk}'

    # -- helpers --
    def login(self, remember=False, username='lena', password=None):
        data = {'username': username, 'password': password or self.PASSWORD}
        if remember:
            data['remember_me'] = 'on'
        return self.client.post(reverse('login'), data)

    def signed_in(self):
        return '_auth_user_id' in self.client.session

    def code(self, index=-1):
        """The code in the e-mail: alone on its own line in the plain-text body."""
        return re.search(r'^([A-Z2-9]{6})$', mail.outbox[index].body, re.M).group(1)

    def submit(self, code=None):
        return self.client.post(reverse('login_verify'), {'code': code or self.code()})

    def confirm(self):
        return self.submit()

    def browser_restart(self):
        """Sign out and drop the session, but keep the remembered-browser cookie."""
        trusted = self.client.cookies.get(self.cookie_name)
        value = trusted.value if trusted else None
        self.client.logout()          # also clears every cookie in the test client
        if value:
            self.client.cookies[self.cookie_name] = value

    # -- the basic flow --
    def test_correct_password_sends_code_instead_of_signing_in(self):
        response = self.login()
        self.assertRedirects(response, reverse('login_check_email'), fetch_redirect_response=False)
        self.assertFalse(self.signed_in())
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['lena@example.com'])
        self.assertNotIn('/login/verify/', mail.outbox[0].body)       # a code, not a link

    def test_code_is_six_easy_characters_and_in_the_subject(self):
        self.login()
        code = self.code()
        self.assertEqual(len(code), 6)
        self.assertFalse(set(code) & set('01OIL'))
        self.assertIn(code, mail.outbox[0].subject)
        html = mail.outbox[0].alternatives[0][0]
        self.assertIn(code, html)

    def test_wrong_password_sends_nothing(self):
        self.login(password='nope')
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(self.signed_in())

    def test_right_code_signs_in(self):
        self.login()
        response = self.submit()
        self.assertRedirects(response, reverse('assessment'), fetch_redirect_response=False)
        self.assertTrue(self.signed_in())

    def test_code_ignores_case_spaces_and_dashes(self):
        self.login()
        code = self.code()
        pasted = ' ' + code[:3].lower() + '-' + code[3:].lower() + ' '
        self.assertEqual(self.submit(pasted).status_code, 302)
        self.assertTrue(self.signed_in())

    def test_wrong_code_does_not_sign_in_and_counts_a_try(self):
        self.login()
        response = self.submit('ZZZZZZ' if self.code() != 'ZZZZZZ' else 'YYYYYY')
        self.assertRedirects(response, reverse('login_check_email'), fetch_redirect_response=False)
        self.assertFalse(self.signed_in())
        self.assertEqual(LoginVerification.objects.get().attempts, 1)

    def test_too_many_wrong_tries_locks_that_code(self):
        self.login()
        right = self.code()
        wrong = 'ZZZZZZ' if right != 'ZZZZZZ' else 'YYYYYY'
        for _ in range(5):
            self.submit(wrong)
        self.submit(right)                              # too late: this code is spent
        self.assertFalse(self.signed_in())

    def test_code_works_only_once(self):
        self.login()
        code = self.code()
        self.submit(code)
        self.client.logout()
        self.submit(code)
        self.assertFalse(self.signed_in())

    def test_expired_code_is_rejected(self):
        self.login()
        LoginVerification.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.submit()
        self.assertFalse(self.signed_in())

    def test_verify_page_redirects_when_opened_directly(self):
        self.assertRedirects(self.client.get(reverse('login_verify')), reverse('login_check_email'),
                             fetch_redirect_response=False)

    def test_submitting_the_code_again_when_already_signed_in_is_harmless(self):
        self.login()
        code = self.code()
        self.submit(code)
        response = self.submit(code)               # e.g. a double submit
        self.assertRedirects(response, reverse('login'), fetch_redirect_response=False)
        self.assertTrue(self.signed_in())

    def test_code_needs_a_pending_sign_in(self):
        response = self.client.post(reverse('login_verify'), {'code': 'ABC234'})
        self.assertRedirects(response, reverse('login'), fetch_redirect_response=False)

    def test_newer_code_replaces_older_one(self):
        self.login()
        first = self.code()
        self.login()
        second = self.code()
        if first != second:
            self.submit(first)
            self.assertFalse(self.signed_in())
        self.assertEqual(self.submit(second).status_code, 302)
        self.assertTrue(self.signed_in())

    def test_code_for_disabled_account_is_rejected(self):
        self.login()
        User.objects.filter(pk=self.user.pk).update(is_active=False)
        self.submit()
        self.assertFalse(self.signed_in())

    def test_code_is_stored_hashed(self):
        self.login()
        record = LoginVerification.objects.get()
        self.assertNotEqual(record.code_hash, self.code())
        self.assertEqual(len(record.code_hash), 64)

    def test_staff_go_to_their_dashboard_after_confirming(self):
        User.objects.filter(pk=self.user.pk).update(is_staff=True)
        self.login()
        self.assertRedirects(self.confirm(), reverse('staff_dashboard'), fetch_redirect_response=False)

    def test_admin_superuser_signs_in_without_an_email_code(self):
        admin = User.objects.create_superuser('boss', email='boss@example.com', password=self.PASSWORD)
        response = self.login(username='boss')
        self.assertRedirects(response, reverse('admin_dashboard'), fetch_redirect_response=False)
        self.assertTrue(self.signed_in())
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(LoginVerification.objects.filter(user=admin).exists())

    def test_admin_still_needs_the_right_password(self):
        User.objects.create_superuser('boss', email='boss@example.com', password=self.PASSWORD)
        self.login(username='boss', password='wrong')
        self.assertFalse(self.signed_in())

    def test_staff_who_are_not_superusers_still_need_the_code(self):
        User.objects.filter(pk=self.user.pk).update(is_staff=True, is_superuser=False)
        self.login()
        self.assertFalse(self.signed_in())
        self.assertEqual(len(mail.outbox), 1)

    def test_accounts_without_an_email_still_sign_in_directly(self):
        User.objects.create_user('noemail', password=self.PASSWORD)
        response = self.login(username='noemail')
        self.assertEqual(response.status_code, 302)
        self.assertTrue(self.signed_in())
        self.assertEqual(len(mail.outbox), 0)

    # -- remember me --
    def test_remember_me_skips_the_email_next_time(self):
        self.login(remember=True)
        response = self.confirm()
        self.assertIn(self.cookie_name, response.cookies)
        self.browser_restart()
        response = self.login()                      # box not even ticked this time
        self.assertEqual(response.status_code, 302)
        self.assertNotEqual(response.url, reverse('login_check_email'))
        self.assertTrue(self.signed_in())
        self.assertEqual(len(mail.outbox), 1)        # no second email

    def test_without_remember_me_every_sign_in_sends_an_email(self):
        self.login()
        response = self.confirm()
        self.assertNotIn(self.cookie_name, response.cookies)
        self.browser_restart()
        self.login()
        self.assertEqual(len(mail.outbox), 2)
        self.assertFalse(self.signed_in())

    def test_first_sign_in_with_remember_me_still_needs_the_email(self):
        self.login(remember=True)
        self.assertFalse(self.signed_in())
        self.assertEqual(len(mail.outbox), 1)

    def test_remembering_is_per_user(self):
        other = User.objects.create_user('omar', email='omar@example.com', password=self.PASSWORD)
        UserProfile.objects.create(user=other)
        self.login(remember=True)
        self.confirm()
        self.browser_restart()
        self.login(username='omar')
        self.assertEqual(mail.outbox[-1].to, ['omar@example.com'])
        self.assertFalse(self.signed_in())

    def test_changing_password_forgets_remembered_browsers(self):
        self.login(remember=True)
        self.confirm()
        self.user.set_password('Brand-new-pw2')
        self.user.save()
        self.browser_restart()
        self.login(password='Brand-new-pw2')
        self.assertEqual(len(mail.outbox), 2)
        self.assertFalse(self.signed_in())

    def test_tampered_trust_cookie_is_ignored(self):
        self.client.cookies[self.cookie_name] = 'forged-value'
        self.login()
        self.assertEqual(len(mail.outbox), 1)
        self.assertFalse(self.signed_in())

    def test_remembered_session_lasts_two_weeks_otherwise_ends_with_browser(self):
        self.login(remember=True)
        self.confirm()
        self.assertEqual(self.client.session.get_expiry_age(), 1209600)
        self.client.logout()
        self.login()
        self.confirm()
        self.assertTrue(self.client.session.get_expire_at_browser_close())

    # -- check-email page, resend, limits --
    def test_check_email_page_masks_the_address(self):
        self.login()
        response = self.client.get(reverse('login_check_email'))
        self.assertContains(response, 'l***@example.com')
        self.assertNotContains(response, 'lena@example.com')

    def test_check_email_page_needs_a_pending_sign_in(self):
        self.assertRedirects(self.client.get(reverse('login_check_email')), reverse('login'),
                             fetch_redirect_response=False)

    def test_check_email_page_has_the_code_box(self):
        self.login()
        response = self.client.get(reverse('login_check_email'))
        self.assertContains(response, 'name="code"')
        self.assertContains(response, 'one-time-code')

    def test_resend_is_rate_limited_then_replaces_the_old_code(self):
        self.login()
        first = self.code()
        self.client.post(reverse('login_resend'))
        self.assertEqual(len(mail.outbox), 1)        # still inside the cooldown
        LoginVerification.objects.update(created_at=timezone.now() - timedelta(minutes=2))
        self.client.post(reverse('login_resend'))
        self.assertEqual(len(mail.outbox), 2)
        second = self.code()
        if first != second:
            self.submit(first)
            self.assertFalse(self.signed_in())
        self.assertEqual(self.submit(second).status_code, 302)
        self.assertTrue(self.signed_in())

    def test_resend_keeps_the_remember_me_choice(self):
        self.login(remember=True)
        LoginVerification.objects.update(created_at=timezone.now() - timedelta(minutes=2))
        self.client.post(reverse('login_resend'))
        self.assertTrue(LoginVerification.objects.filter(used_at__isnull=True).get().remember_me)

    def test_hourly_cap_on_sign_in_emails(self):
        for _ in range(5):
            self.assertEqual(self.login().status_code, 302)
        response = self.login()
        self.assertEqual(response.status_code, 429)
        self.assertEqual(len(mail.outbox), 5)
        self.assertFalse(self.signed_in())

    def test_email_failure_does_not_sign_in_and_leaves_no_code(self):
        with patch('core.views.EmailMultiAlternatives.send', side_effect=OSError('smtp down')):
            response = self.login()
        self.assertEqual(response.status_code, 503)
        self.assertFalse(self.signed_in())
        self.assertFalse(LoginVerification.objects.exists())


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', SITE_URL='https://mica.test')
class GoogleEmailCodeTests(TestCase):
    """Google sign-in goes through the same e-mailed code."""
    EMAIL = 'gina@example.com'

    def google_login(self, remember=False):
        claims = {'uid': 'uid-gina', 'email': self.EMAIL, 'email_verified': True, 'name': 'Gina Lopez',
                  'firebase': {'sign_in_provider': 'google.com'}}
        body = {'id_token': 'tok'}
        if remember:
            body['remember_me'] = True
        grant_registration_code(self.client)   # new accounts need a registration code
        with patch.object(firebase_auth, 'verify_id_token', return_value=claims):
            return self.client.post(reverse('google_register'), data=json.dumps(body),
                                    content_type='application/json')

    def signed_in(self):
        return '_auth_user_id' in self.client.session

    def submit_code(self):
        code = re.search(r'^([A-Z2-9]{6})$', mail.outbox[-1].body, re.M).group(1)
        return self.client.post(reverse('login_verify'), {'code': code})

    def test_google_sign_in_sends_a_code_instead_of_signing_in(self):
        response = self.google_login()
        self.assertEqual(response.json()['redirect'], reverse('login_check_email'))
        self.assertFalse(self.signed_in())
        self.assertEqual(mail.outbox[0].to, [self.EMAIL])

    def test_entering_the_code_continues_to_profile_setup(self):
        self.google_login()
        response = self.submit_code()
        self.assertRedirects(response, reverse('complete_profile'), fetch_redirect_response=False)
        self.assertTrue(self.signed_in())

    def test_remember_me_from_the_login_page_skips_the_email_next_time(self):
        self.google_login(remember=True)
        self.submit_code()
        user = User.objects.get(email=self.EMAIL)
        cookie = self.client.cookies[f'mica_tb_{user.pk}'].value
        self.client.logout()
        self.client.cookies[f'mica_tb_{user.pk}'] = cookie
        response = self.google_login()
        self.assertEqual(response.json()['redirect'], reverse('complete_profile'))
        self.assertTrue(self.signed_in())
        self.assertEqual(len(mail.outbox), 1)

    def test_without_remember_me_google_sign_in_asks_again(self):
        self.google_login()
        self.submit_code()
        self.client.logout()
        self.google_login()
        self.assertEqual(len(mail.outbox), 2)
        self.assertFalse(self.signed_in())
