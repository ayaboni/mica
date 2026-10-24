import io
import json
import urllib.error
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import firebase_reset
from .firebase_reset import FirebaseResetError
from .models import LoginThrottle, PasswordResetRequest, UserProfile
from .views import firebase_auth

OLD_PW = 'Old-pass1'
NEW_PW = 'New-pass12'


class LoginPromptTests(TestCase):
    """The login page offers a password reset after a few wrong passwords."""

    def setUp(self):
        User.objects.create_user('sam', email='sam@example.com', password=OLD_PW)

    def wrong(self):
        return self.client.post(reverse('login'), {'username': 'sam', 'password': 'nope'})

    def test_no_prompt_on_a_fresh_login_page(self):
        self.assertNotContains(self.client.get(reverse('login')), 'Having trouble signing in?')

    def test_no_prompt_for_the_first_two_wrong_passwords(self):
        for _ in range(2):
            self.assertNotContains(self.wrong(), 'Having trouble signing in?')

    def test_prompt_appears_on_the_third_wrong_password_and_links_to_the_reset_page(self):
        self.wrong(); self.wrong()
        response = self.wrong()
        self.assertContains(response, 'Having trouble signing in?')
        self.assertContains(response, reverse('password_reset') + '?username=sam')

    def test_prompt_is_also_shown_while_locked_out(self):
        for _ in range(5):
            response = self.wrong()
        self.assertEqual(response.status_code, 429)
        self.assertContains(response, 'Having trouble signing in?', status_code=429)
        again = self.wrong()                       # still locked
        self.assertContains(again, 'Having trouble signing in?', status_code=429)

    def test_username_is_url_encoded_in_the_link(self):
        for _ in range(3):
            response = self.client.post(reverse('login'), {'username': 'a b&c=d', 'password': 'x'})
        self.assertContains(response, '?username=a%20b%26c%3Dd')


class PasswordResetRequestTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('lena', email='lena@example.com', password=OLD_PW)

    def post(self, identifier):
        return self.client.post(reverse('password_reset'), {'identifier': identifier})

    def send_patch(self, **kwargs):
        return patch.object(firebase_reset, 'send_password_reset_email', **kwargs)

    def test_request_page_prefills_the_username(self):
        response = self.client.get(reverse('password_reset') + '?username=lena')
        self.assertContains(response, 'value="lena"')

    def test_username_sends_a_reset_email_to_the_accounts_address(self):
        with self.send_patch() as send:
            response = self.post('lena')
        send.assert_called_once()
        self.assertEqual(send.call_args[0][0], 'lena@example.com')
        self.assertContains(response, 'Check your email')

    def test_email_lookup_ignores_case(self):
        with self.send_patch() as send:
            self.post('LENA@Example.com')
        send.assert_called_once()
        self.assertEqual(send.call_args[0][0], 'lena@example.com')

    def test_unknown_account_gets_the_same_answer_and_nothing_is_sent(self):
        with self.send_patch() as send:
            response = self.post('nobody')
        send.assert_not_called()
        self.assertContains(response, 'Check your email')

    def test_google_accounts_have_no_password_to_reset(self):
        google = User.objects.create_user('gina', email='gina@example.com')
        google.set_unusable_password(); google.save()
        with self.send_patch() as send:
            self.post('gina'); self.post('gina@example.com')
        send.assert_not_called()

    def test_disabled_accounts_are_skipped(self):
        User.objects.filter(pk=self.user.pk).update(is_active=False)
        with self.send_patch() as send:
            self.post('lena')
        send.assert_not_called()

    def test_accounts_without_an_email_are_skipped(self):
        User.objects.create_user('noemail', password=OLD_PW)
        with self.send_patch() as send:
            self.post('noemail')
        send.assert_not_called()

    def test_admin_accounts_are_skipped_unless_enabled(self):
        User.objects.create_superuser('boss', 'boss@example.com', OLD_PW)
        with self.send_patch() as send:
            self.post('boss')
        send.assert_not_called()
        with override_settings(PASSWORD_RESET_FOR_ADMINS=True), self.send_patch() as send:
            self.post('boss')
        send.assert_called_once()

    def test_empty_request_asks_again(self):
        with self.send_patch() as send:
            response = self.post('   ')
        send.assert_not_called()
        self.assertContains(response, 'Please enter your username or email.')

    def test_each_email_is_limited_per_hour(self):
        for _ in range(3):
            PasswordResetRequest.objects.create(email_key='lena@example.com')
        with self.send_patch() as send:
            response = self.post('lena')
        send.assert_not_called()
        self.assertContains(response, 'Check your email')       # same answer, no hint that it was limited

    def test_old_requests_do_not_count_against_the_limit(self):
        for _ in range(3):
            PasswordResetRequest.objects.create(email_key='lena@example.com')
        PasswordResetRequest.objects.update(created_at=timezone.now() - timedelta(hours=2))
        with self.send_patch() as send:
            self.post('lena')
        send.assert_called_once()

    def test_each_ip_is_limited_per_hour(self):
        for i in range(10):
            PasswordResetRequest.objects.create(email_key=f'other{i}@example.com', ip='127.0.0.1')
        with self.send_patch() as send:
            self.post('lena')
        send.assert_not_called()

    def test_a_firebase_failure_still_shows_the_same_page(self):
        with self.send_patch(side_effect=FirebaseResetError('OPERATION_NOT_ALLOWED')):
            response = self.post('lena')
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Check your email')

    def test_accounts_sharing_an_email_get_a_single_email(self):
        User.objects.create_user('lena2', email='LENA@example.com', password=OLD_PW)
        with self.send_patch() as send:
            self.post('lena@example.com')
        send.assert_called_once()


class PasswordResetCompleteTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('lena', email='lena@example.com', password=OLD_PW)
        self.url = reverse('password_reset_firebase_complete')

    def post(self, **overrides):
        payload = {'oobCode': 'code123', 'newPassword': NEW_PW, 'confirmPassword': NEW_PW, **overrides}
        return self.client.post(self.url, data=json.dumps(payload), content_type='application/json')

    def confirm_patch(self, **kwargs):
        kwargs.setdefault('return_value', 'lena@example.com')
        return patch.object(firebase_reset, 'confirm_password_reset', **kwargs)

    def test_handler_page_loads(self):
        response = self.client.get(reverse('password_reset_firebase'))
        self.assertContains(response, 'id="resetForm"')
        self.assertContains(response, 'id="firebase-config"')

    def test_success_changes_the_local_password(self):
        with self.confirm_patch() as confirm, patch.object(firebase_reset, 'scramble_password') as scramble:
            response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json()['ok'])
        self.assertEqual(confirm.call_args[0][:2], ('code123', NEW_PW))
        scramble.assert_called_once_with('lena@example.com')
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(NEW_PW))
        self.assertFalse(self.user.check_password(OLD_PW))

    def test_success_lifts_a_login_lockout(self):
        LoginThrottle.objects.create(username_key='lena', failed_count=5,
                                     locked_until=timezone.now() + timedelta(seconds=50))
        with self.confirm_patch(), patch.object(firebase_reset, 'scramble_password'):
            self.post()
        self.assertFalse(LoginThrottle.objects.filter(username_key='lena').exists())

    def test_every_local_account_with_that_email_is_updated(self):
        other = User.objects.create_user('lena2', email='Lena@Example.com', password=OLD_PW)
        with self.confirm_patch(), patch.object(firebase_reset, 'scramble_password'):
            self.post()
        other.refresh_from_db()
        self.assertTrue(other.check_password(NEW_PW))

    def test_google_accounts_are_never_given_a_password(self):
        google = User.objects.create_user('gina', email='gina@example.com')
        google.set_unusable_password(); google.save()
        with self.confirm_patch(return_value='gina@example.com'), patch.object(firebase_reset, 'scramble_password'):
            response = self.post()
        self.assertEqual(response.status_code, 400)
        google.refresh_from_db()
        self.assertFalse(google.has_usable_password())

    def test_expired_or_used_links_are_reported_as_invalid(self):
        for code in ('EXPIRED_OOB_CODE', 'INVALID_OOB_CODE'):
            with self.confirm_patch(side_effect=FirebaseResetError(code)):
                response = self.post()
            self.assertEqual(response.status_code, 400)
            self.assertTrue(response.json()['invalid_link'])
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(OLD_PW))

    def test_other_firebase_errors_are_a_try_again_message(self):
        with self.confirm_patch(side_effect=FirebaseResetError('NETWORK')):
            response = self.post()
        self.assertEqual(response.status_code, 502)
        self.assertNotIn('invalid_link', response.json())

    def test_passwords_must_match_and_firebase_is_not_asked(self):
        with self.confirm_patch() as confirm:
            response = self.post(confirmPassword='Different-1')
        self.assertEqual(response.status_code, 400)
        self.assertIn('do not match', response.json()['error'])
        confirm.assert_not_called()

    def test_new_password_must_meet_the_rules_and_firebase_is_not_asked(self):
        for weak in ('Abcde1!', 'abcdefg1!', 'Abcdefgh', ''):    # 7 chars / no capital / no number / empty
            with self.confirm_patch() as confirm:
                response = self.post(newPassword=weak, confirmPassword=weak)
            self.assertEqual(response.status_code, 400, weak)
            confirm.assert_not_called()
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(OLD_PW))

    def test_missing_code_is_invalid(self):
        with self.confirm_patch() as confirm:
            response = self.post(oobCode='')
        self.assertEqual(response.status_code, 400)
        self.assertTrue(response.json()['invalid_link'])
        confirm.assert_not_called()

    def test_malformed_body_is_rejected(self):
        response = self.client.post(self.url, data='not json', content_type='application/json')
        self.assertEqual(response.status_code, 400)

    def test_an_email_with_no_local_account_changes_nothing(self):
        with self.confirm_patch(return_value='stranger@example.com'), patch.object(firebase_reset, 'scramble_password'):
            response = self.post()
        self.assertEqual(response.status_code, 400)
        self.user.refresh_from_db()
        self.assertTrue(self.user.check_password(OLD_PW))

    def test_requires_post(self):
        self.assertEqual(self.client.get(self.url).status_code, 405)

    def test_can_log_in_with_the_new_password_afterwards(self):
        with self.confirm_patch(), patch.object(firebase_reset, 'scramble_password'):
            self.post()
        self.assertFalse(self.client.login(username='lena', password=OLD_PW))
        self.assertTrue(self.client.login(username='lena', password=NEW_PW))


class FirebaseClientTests(TestCase):
    """core/firebase_reset.py against a faked Firebase REST API."""

    def fake_response(self, body):
        response = MagicMock()
        response.read.return_value = json.dumps(body).encode()
        response.__enter__.return_value = response
        return response

    def http_error(self, message, status=400):
        body = io.BytesIO(json.dumps({'error': {'message': message}}).encode())
        return urllib.error.HTTPError('https://x', status, 'Bad', {}, body)

    def test_confirm_returns_the_email_and_sends_the_right_request(self):
        with patch('urllib.request.urlopen', return_value=self.fake_response({'email': 'lena@example.com'})) as urlopen:
            email = firebase_reset.confirm_password_reset('code123', NEW_PW, referer='https://mica.test/')
        self.assertEqual(email, 'lena@example.com')
        request = urlopen.call_args[0][0]
        self.assertIn('accounts:resetPassword?key=', request.full_url)
        self.assertEqual(json.loads(request.data), {'oobCode': 'code123', 'newPassword': NEW_PW})
        self.assertEqual(request.get_header('Referer'), 'https://mica.test/')

    def test_firebase_error_codes_are_extracted(self):
        for message, code in (('EXPIRED_OOB_CODE', 'EXPIRED_OOB_CODE'),
                              ('WEAK_PASSWORD : Password should be at least 6 characters', 'WEAK_PASSWORD')):
            with patch('urllib.request.urlopen', side_effect=self.http_error(message)):
                with self.assertRaises(FirebaseResetError) as caught:
                    firebase_reset.confirm_password_reset('c', NEW_PW)
            self.assertEqual(caught.exception.code, code)

    def test_unreadable_error_bodies_fall_back_to_the_status(self):
        error = urllib.error.HTTPError('https://x', 503, 'Down', {}, io.BytesIO(b'<html>oops</html>'))
        with patch('urllib.request.urlopen', side_effect=error):
            with self.assertRaises(FirebaseResetError) as caught:
                firebase_reset.confirm_password_reset('c', NEW_PW)
        self.assertEqual(caught.exception.code, 'HTTP_503')

    def test_network_problems_become_a_firebase_error(self):
        with patch('urllib.request.urlopen', side_effect=urllib.error.URLError('no route')):
            with self.assertRaises(FirebaseResetError) as caught:
                firebase_reset.confirm_password_reset('c', NEW_PW)
        self.assertEqual(caught.exception.code, 'NETWORK')

    def test_a_reply_without_an_email_is_an_error(self):
        with patch('urllib.request.urlopen', return_value=self.fake_response({})):
            with self.assertRaises(FirebaseResetError):
                firebase_reset.confirm_password_reset('c', NEW_PW)

    def test_sending_creates_a_mirror_user_when_firebase_has_none(self):
        with patch.object(firebase_auth, 'get_user_by_email', side_effect=firebase_auth.UserNotFoundError('none')), \
             patch.object(firebase_auth, 'create_user') as create, \
             patch('urllib.request.urlopen', return_value=self.fake_response({'email': 'lena@example.com'})) as urlopen:
            firebase_reset.send_password_reset_email('lena@example.com')
        kwargs = create.call_args.kwargs
        self.assertEqual(kwargs['email'], 'lena@example.com')
        self.assertGreaterEqual(len(kwargs['password']), 32)           # long and random
        self.assertEqual(json.loads(urlopen.call_args[0][0].data),
                         {'requestType': 'PASSWORD_RESET', 'email': 'lena@example.com'})

    def test_sending_reuses_an_existing_firebase_user(self):
        with patch.object(firebase_auth, 'get_user_by_email', return_value=MagicMock()), \
             patch.object(firebase_auth, 'create_user') as create, \
             patch('urllib.request.urlopen', return_value=self.fake_response({})):
            firebase_reset.send_password_reset_email('lena@example.com')
        create.assert_not_called()

    def test_generate_reset_code_pulls_the_code_out_of_firebases_link(self):
        link = 'https://p.firebaseapp.com/__/auth/action?mode=resetPassword&oobCode=XYZ-123&apiKey=k&lang=en'
        with patch.object(firebase_auth, 'get_user_by_email', return_value=MagicMock()), \
             patch.object(firebase_auth, 'generate_password_reset_link', return_value=link) as generate:
            code = firebase_reset.generate_reset_code('lena@example.com')
        self.assertEqual(code, 'XYZ-123')
        generate.assert_called_once_with('lena@example.com')

    def test_generate_reset_code_creates_a_mirror_user_first(self):
        link = 'https://p.firebaseapp.com/__/auth/action?mode=resetPassword&oobCode=C0DE'
        with patch.object(firebase_auth, 'get_user_by_email', side_effect=firebase_auth.UserNotFoundError('none')), \
             patch.object(firebase_auth, 'create_user') as create, \
             patch.object(firebase_auth, 'generate_password_reset_link', return_value=link):
            firebase_reset.generate_reset_code('lena@example.com')
        create.assert_called_once()

    def test_generate_reset_code_errors_are_wrapped(self):
        with patch.object(firebase_auth, 'get_user_by_email', return_value=MagicMock()):
            with patch.object(firebase_auth, 'generate_password_reset_link', side_effect=RuntimeError('nope')):
                with self.assertRaises(FirebaseResetError) as caught:
                    firebase_reset.generate_reset_code('lena@example.com')
            self.assertEqual(caught.exception.code, 'GENERATE_FAILED')
            with patch.object(firebase_auth, 'generate_password_reset_link', return_value='https://x/y?mode=resetPassword'):
                with self.assertRaises(FirebaseResetError) as caught:
                    firebase_reset.generate_reset_code('lena@example.com')
            self.assertEqual(caught.exception.code, 'NO_CODE')

    def test_scramble_replaces_the_mirror_password(self):
        user = MagicMock(uid='uid-1')
        with patch.object(firebase_auth, 'get_user_by_email', return_value=user), \
             patch.object(firebase_auth, 'update_user') as update:
            firebase_reset.scramble_password('lena@example.com')
        self.assertEqual(update.call_args.args[0], 'uid-1')
        self.assertGreaterEqual(len(update.call_args.kwargs['password']), 32)

    def test_scramble_never_raises(self):
        with patch.object(firebase_auth, 'get_user_by_email', side_effect=RuntimeError('boom')):
            firebase_reset.scramble_password('lena@example.com')          # must not raise


class GoogleProviderCheckTests(TestCase):
    """Only real Google sign-ins may use /auth/google/, even though Firebase now also has e-mail/password users."""

    def login(self, claims):
        base = {'uid': 'uid-x', 'email': 'x@example.com', 'email_verified': True, 'name': 'Ex Ample'}
        with patch.object(firebase_auth, 'verify_id_token', return_value={**base, **claims}):
            return self.client.post(reverse('google_register'), data=json.dumps({'id_token': 't'}),
                                    content_type='application/json')

    def test_password_provider_tokens_are_refused(self):
        response = self.login({'firebase': {'sign_in_provider': 'password'}})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(User.objects.exists())

    def test_tokens_without_a_provider_are_refused(self):
        self.assertEqual(self.login({}).status_code, 400)
        self.assertFalse(User.objects.exists())

    def test_google_tokens_still_work(self):
        with patch('core.views._is_trusted_browser', return_value=True):
            response = self.login({'firebase': {'sign_in_provider': 'google.com'}})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(User.objects.filter(email='x@example.com').exists())


class PasswordRuleLengthTests(TestCase):
    FORM = {
        'first_name': 'Sam', 'last_name': 'Cruz', 'student_number': '2024-1111', 'course': 'BSN',
        'year_level': '1', 'username': 'sam', 'email': 'sam@example.com',
    }

    def register(self, password):
        return self.client.post(reverse('register'), {**self.FORM, 'password': password, 'confirm_password': password})

    def test_seven_characters_is_now_too_short(self):
        self.assertContains(self.register('Abcde1!'), 'at least 8 characters')
        self.assertFalse(User.objects.exists())

    def test_eight_characters_is_enough(self):
        self.assertRedirects(self.register('Abcdef1!'), reverse('login'), fetch_redirect_response=False)

    def test_register_page_states_the_new_minimum(self):
        self.assertContains(self.client.get(reverse('register')), 'At least 8 characters')


@override_settings(PASSWORD_RESET_EMAIL_VIA='mica', SITE_URL='https://mica.test',
                   EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class PasswordResetViaMicaTests(TestCase):
    """PASSWORD_RESET_EMAIL_VIA = "mica": Firebase makes the code, MICA e-mails the link."""

    def setUp(self):
        self.user = User.objects.create_user('lena', email='lena@example.com', password=OLD_PW)

    def post(self, identifier='lena'):
        return self.client.post(reverse('password_reset'), {'identifier': identifier})

    def test_mica_sends_the_link_and_firebase_does_not(self):
        with patch.object(firebase_reset, 'generate_reset_code', return_value='abc123') as generate, \
             patch.object(firebase_reset, 'send_password_reset_email') as firebase_send:
            response = self.post()
        generate.assert_called_once_with('lena@example.com')
        firebase_send.assert_not_called()
        self.assertContains(response, 'Check your email')
        self.assertEqual(len(mail.outbox), 1)
        message = mail.outbox[0]
        self.assertEqual(message.to, ['lena@example.com'])
        link = 'https://mica.test/password-reset/firebase/?mode=resetPassword&oobCode=abc123'
        self.assertIn(link, message.body)
        self.assertIn(link, message.alternatives[0][0])

    def test_unknown_accounts_get_the_same_page_and_no_email(self):
        with patch.object(firebase_reset, 'generate_reset_code') as generate:
            response = self.post('nobody')
        generate.assert_not_called()
        self.assertEqual(mail.outbox, [])
        self.assertContains(response, 'Check your email')

    def test_a_firebase_failure_sends_nothing_but_looks_the_same(self):
        with patch.object(firebase_reset, 'generate_reset_code', side_effect=FirebaseResetError('GENERATE_FAILED')):
            response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(mail.outbox, [])
        self.assertContains(response, 'Check your email')

    def test_an_email_failure_is_swallowed(self):
        with patch.object(firebase_reset, 'generate_reset_code', return_value='abc123'), \
             patch('core.views.EmailMultiAlternatives.send', side_effect=OSError('smtp down')):
            response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Check your email')

    def test_the_hourly_limit_still_applies(self):
        for _ in range(3):
            PasswordResetRequest.objects.create(email_key='lena@example.com')
        with patch.object(firebase_reset, 'generate_reset_code') as generate:
            self.post()
        generate.assert_not_called()
        self.assertEqual(mail.outbox, [])

    def test_the_default_mode_still_uses_firebase_to_send(self):
        with override_settings(PASSWORD_RESET_EMAIL_VIA='firebase'), \
             patch.object(firebase_reset, 'send_password_reset_email') as firebase_send, \
             patch.object(firebase_reset, 'generate_reset_code') as generate:
            self.post()
        firebase_send.assert_called_once()
        generate.assert_not_called()
        self.assertEqual(mail.outbox, [])
