import re
from datetime import timedelta

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from .models import LoginVerification, UserProfile
from .views import _hash_code


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class PasswordChangeCodeTests(TestCase):
    """Changing a password from the account page needs an e-mailed 6-character code first."""
    OLD = 'Old-pw-123!'
    NEW = 'Brand-new-pw-456!'

    def setUp(self):
        self.user = User.objects.create_user('lena', email='lena@example.com', password=self.OLD, first_name='Lena')
        UserProfile.objects.create(user=self.user)
        self.client.force_login(self.user)
        self.url = reverse('change_password')

    # -- helpers --
    def send_code(self):
        return self.client.post(self.url, {'action': 'send_code'})

    def code(self):
        return re.search(r'^([A-Z2-9]{6})$', mail.outbox[-1].body, re.M).group(1)

    def verify(self, code=None):
        return self.client.post(self.url, {'action': 'verify_code', 'code': code or self.code()})

    def change(self, old=None, new=None):
        return self.client.post(self.url, {
            'action': 'change', 'old_password': old or self.OLD,
            'new_password1': new or self.NEW, 'new_password2': new or self.NEW})

    def password_is(self, raw):
        self.user.refresh_from_db()
        return self.user.check_password(raw)

    # -- the flow --
    def test_page_asks_for_a_code_before_showing_the_password_form(self):
        response = self.client.get(self.url)
        self.assertContains(response, 'Email me a code')
        self.assertNotContains(response, 'new_password1')
        self.assertContains(response, 'l***@example.com')

    def test_sending_a_code_emails_it_and_shows_the_code_box(self):
        self.send_code()
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['lena@example.com'])
        self.assertIn(self.code(), mail.outbox[0].subject)
        response = self.client.get(self.url)
        self.assertContains(response, 'name="code"')
        self.assertNotContains(response, 'new_password1')

    def test_password_cannot_be_changed_without_the_code(self):
        response = self.change()
        self.assertRedirects(response, self.url, fetch_redirect_response=False)
        self.assertTrue(self.password_is(self.OLD))

    def test_password_cannot_be_changed_after_only_requesting_a_code(self):
        self.send_code()
        self.change()
        self.assertTrue(self.password_is(self.OLD))

    def test_right_code_unlocks_the_form_and_the_password_changes(self):
        self.send_code()
        self.verify()
        self.assertContains(self.client.get(self.url), 'new_password1')
        response = self.change()
        self.assertRedirects(response, reverse('account_settings'), fetch_redirect_response=False)
        self.assertTrue(self.password_is(self.NEW))

    def test_code_ignores_case_spaces_and_dashes(self):
        self.send_code()
        code = self.code()
        self.verify(' ' + code[:3].lower() + '-' + code[3:].lower() + ' ')
        self.assertContains(self.client.get(self.url), 'new_password1')

    def test_wrong_code_does_not_unlock(self):
        self.send_code()
        right = self.code()
        self.verify('ZZZZZZ' if right != 'ZZZZZZ' else 'YYYYYY')
        self.change()
        self.assertTrue(self.password_is(self.OLD))
        self.assertEqual(LoginVerification.objects.get(purpose='password_change').attempts, 1)

    def test_too_many_wrong_tries_locks_that_code(self):
        self.send_code()
        right = self.code()
        wrong = 'ZZZZZZ' if right != 'ZZZZZZ' else 'YYYYYY'
        for _ in range(5):
            self.verify(wrong)
        self.verify(right)
        self.change()
        self.assertTrue(self.password_is(self.OLD))

    def test_expired_code_is_rejected(self):
        self.send_code()
        LoginVerification.objects.update(expires_at=timezone.now() - timedelta(seconds=1))
        self.verify()
        self.change()
        self.assertTrue(self.password_is(self.OLD))

    def test_one_code_allows_one_change(self):
        self.send_code()
        self.verify()
        self.change()
        self.client.force_login(self.user)
        self.change(old=self.NEW, new='Another-pw-789!')
        self.assertTrue(self.password_is(self.NEW))          # needed a fresh code

    def test_wrong_current_password_keeps_the_form_open_without_a_new_code(self):
        self.send_code()
        self.verify()
        self.change(old='not-my-password')
        self.assertTrue(self.password_is(self.OLD))
        self.assertContains(self.client.get(self.url), 'new_password1')   # still verified: just retry

    def test_unlock_expires(self):
        self.send_code()
        self.verify()
        session = self.client.session
        session['pw_change_verified_until'] = timezone.now().timestamp() - 1
        session.save()
        self.change()
        self.assertTrue(self.password_is(self.OLD))

    # -- codes are for one purpose only --
    def test_a_sign_in_code_cannot_confirm_a_password_change(self):
        LoginVerification.objects.create(
            user=self.user, purpose='login', code_hash=_hash_code(self.user, 'ABC234'),
            expires_at=timezone.now() + timedelta(minutes=5))
        self.verify('ABC234')
        self.change()
        self.assertTrue(self.password_is(self.OLD))

    def test_asking_for_a_password_code_does_not_cancel_a_pending_sign_in_code(self):
        pending = LoginVerification.objects.create(
            user=self.user, purpose='login', code_hash=_hash_code(self.user, 'ABC234'),
            expires_at=timezone.now() + timedelta(minutes=5))
        self.send_code()
        pending.refresh_from_db()
        self.assertIsNone(pending.used_at)

    # -- limits and edge cases --
    def test_resend_waits_for_the_cooldown(self):
        self.send_code()
        self.send_code()
        self.assertEqual(len(mail.outbox), 1)
        LoginVerification.objects.update(created_at=timezone.now() - timedelta(minutes=2))
        self.send_code()
        self.assertEqual(len(mail.outbox), 2)

    def test_a_new_code_replaces_the_old_one(self):
        self.send_code()
        first = self.code()
        LoginVerification.objects.update(created_at=timezone.now() - timedelta(minutes=2))
        self.send_code()
        second = self.code()
        if first != second:
            self.verify(first)
            self.change()
            self.assertTrue(self.password_is(self.OLD))
        self.verify(second)
        self.change()
        self.assertTrue(self.password_is(self.NEW))

    def test_hourly_cap_on_password_code_emails(self):
        for _ in range(5):
            self.send_code()
            LoginVerification.objects.update(created_at=timezone.now() - timedelta(minutes=2))
        before = len(mail.outbox)
        self.send_code()
        self.assertEqual(len(mail.outbox), before)

    def test_account_without_an_email_keeps_the_old_form(self):
        nomail = User.objects.create_user('noemail', password=self.OLD)
        UserProfile.objects.create(user=nomail)
        self.client.force_login(nomail)
        self.assertContains(self.client.get(self.url), 'new_password1')
        self.change()
        nomail.refresh_from_db()
        self.assertTrue(nomail.check_password(self.NEW))
        self.assertEqual(len(mail.outbox), 0)

    def test_page_needs_a_signed_in_user(self):
        self.client.logout()
        response = self.client.get(self.url)
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response.url)

    def test_email_failure_sends_no_code_and_leaves_no_record(self):
        from unittest.mock import patch
        with patch('core.views.EmailMultiAlternatives.send', side_effect=OSError('smtp down')):
            self.send_code()
        self.assertFalse(LoginVerification.objects.filter(purpose='password_change').exists())
