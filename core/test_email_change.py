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
class EmailChangeTests(TestCase):
    """Changing the account email needs the current password AND a code sent to the new address."""
    PW = 'Old-pw-123!'
    OLD = 'lena@example.com'
    NEW = 'lena.new@example.com'

    def setUp(self):
        self.user = User.objects.create_user('lena', email=self.OLD, password=self.PW, first_name='Lena')
        UserProfile.objects.create(user=self.user)
        self.other = User.objects.create_user('other', email='taken@example.com', password='x-Pw-123456!')
        self.client.force_login(self.user)
        self.url = reverse('change_email')

    # -- helpers --
    def send(self, email=None, password=None):
        return self.client.post(self.url, {
            'action': 'send_code', 'new_email': email or self.NEW,
            'current_password': self.PW if password is None else password})

    def code(self):
        return re.search(r'^([A-Z2-9]{6})$', mail.outbox[-1].body, re.M).group(1)

    def verify(self, code=None):
        return self.client.post(self.url, {'action': 'verify_code', 'code': code or self.code()})

    def email_now(self):
        self.user.refresh_from_db()
        return self.user.email

    def skip_cooldown(self):
        LoginVerification.objects.update(created_at=timezone.now() - timedelta(minutes=2))

    # -- the old hole is closed --
    def test_account_settings_form_can_no_longer_change_the_email(self):
        self.client.post(reverse('account_settings'), {
            'username': 'lena', 'first_name': 'Lena', 'last_name': 'Cruz', 'email': 'attacker@example.com'})
        self.assertEqual(self.email_now(), self.OLD)

    # -- the safe flow --
    def test_full_flow_changes_email_only_after_the_code(self):
        self.send()
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, [self.NEW])            # the code goes to the NEW address
        self.assertEqual(self.email_now(), self.OLD)               # ...and nothing has changed yet
        response = self.verify()
        self.assertRedirects(response, reverse('account_settings'), fetch_redirect_response=False)
        self.assertEqual(self.email_now(), self.NEW)
        self.assertEqual(len(mail.outbox), 2)                      # the old address is told
        self.assertEqual(mail.outbox[1].to, [self.OLD])
        self.assertIn('changed', mail.outbox[1].subject.lower())

    def test_wrong_password_sends_nothing(self):
        self.send(password='wrong')
        self.assertEqual(len(mail.outbox), 0)
        self.assertNotIn('pending_email_change', self.client.session)

    def test_account_with_no_password_must_create_one_first(self):
        self.user.set_unusable_password()
        self.user.save()
        self.client.force_login(self.user)
        self.send()
        self.assertEqual(len(mail.outbox), 0)

    def test_password_guessing_is_locked_out(self):
        for _ in range(5):
            self.send(password='wrong')
        self.send()                                                # even the right password is refused now
        self.assertEqual(len(mail.outbox), 0)

    def test_email_used_by_another_account_is_refused_ignoring_case(self):
        self.send(email='Taken@Example.com')
        self.assertEqual(len(mail.outbox), 0)

    def test_invalid_or_unchanged_email_is_refused(self):
        for bad in ['not-an-email', '', self.OLD.upper(), 'a' * 250 + '@x.com']:
            self.send(email=bad or ' ')
        self.assertEqual(len(mail.outbox), 0)

    def test_wrong_code_does_not_change_the_email(self):
        self.send()
        self.verify(code='AAAAAA')
        self.assertEqual(self.email_now(), self.OLD)

    def test_a_password_change_code_cannot_confirm_an_email_change(self):
        self.send()
        LoginVerification.objects.create(
            user=self.user, purpose='password_change', code_hash=_hash_code(self.user, 'ZZZZZZ'),
            expires_at=timezone.now() + timedelta(minutes=10))
        self.verify(code='ZZZZZZ')
        self.assertEqual(self.email_now(), self.OLD)

    def test_email_taken_in_the_meantime_is_refused_at_the_end(self):
        self.send()
        code = self.code()
        User.objects.filter(pk=self.other.pk).update(email=self.NEW)
        self.verify(code=code)
        self.assertEqual(self.email_now(), self.OLD)

    def test_cancel_discards_the_pending_change(self):
        self.send()
        code = self.code()
        self.client.post(self.url, {'action': 'cancel'})
        self.verify(code=code)
        self.assertEqual(self.email_now(), self.OLD)

    def test_a_used_code_cannot_be_replayed(self):
        self.send()
        code = self.code()
        self.verify(code=code)
        self.client.post(reverse('change_email'), {'action': 'send_code', 'new_email': 'third@example.com',
                                                   'current_password': self.PW})
        self.verify(code=code)
        self.assertEqual(self.email_now(), self.NEW)

    def test_staff_are_sent_to_their_dashboard(self):
        staff = User.objects.create_user('staffer', password='Staff-pass-123!', is_staff=True)
        self.client.force_login(staff)
        self.assertEqual(self.client.get(self.url).status_code, 302)

    def test_anonymous_visitors_are_sent_to_sign_in(self):
        self.client.logout()
        self.assertEqual(self.client.get(self.url).status_code, 302)
