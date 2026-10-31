import json
import re
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import UserProfile
from .regcode_testing import grant_registration_code
from .views import firebase_auth


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', SITE_URL='https://mica.test')
class DuplicateGoogleRegistrationTests(TestCase):
    """Once an e-mail address has been confirmed with the e-mailed code, it can't register again."""
    EMAIL = 'dana@gmail.com'

    def google(self, mode='register', uid='uid-dana', email=None):
        claims = {'uid': uid, 'email': email or self.EMAIL, 'email_verified': True, 'name': 'Dana Cruz',
                  'firebase': {'sign_in_provider': 'google.com'}}
        body = {'id_token': 't'}
        if mode:
            body['mode'] = mode
        with patch.object(firebase_auth, 'verify_id_token', return_value=claims):
            return self.client.post(reverse('google_register'), data=json.dumps(body),
                                    content_type='application/json')

    def submit_code(self):
        code = re.search(r'^([A-Z2-9]{6})$', mail.outbox[-1].body, re.M).group(1)
        return self.client.post(reverse('login_verify'), {'code': code})

    def register_and_verify(self):
        grant_registration_code(self.client)
        self.assertEqual(self.google().status_code, 200)
        self.submit_code()
        self.client.logout()

    def test_verified_email_cannot_register_again(self):
        self.register_and_verify()
        self.assertIsNotNone(UserProfile.objects.get(user__email=self.EMAIL).email_verified_at)
        grant_registration_code(self.client)          # even with a fresh registration code
        response = self.google()
        self.assertEqual(response.status_code, 409)
        self.assertTrue(response.json()['already_registered'])
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 1)         # no new code e-mailed, nobody signed in
        self.assertNotIn('_auth_user_id', self.client.session)

    def test_same_gmail_in_different_case_is_also_blocked(self):
        self.register_and_verify()
        grant_registration_code(self.client)
        response = self.google(uid='uid-other', email='Dana@Gmail.com')
        self.assertEqual(response.status_code, 409)
        self.assertEqual(User.objects.count(), 1)

    def test_verified_user_can_still_sign_in_from_the_login_page(self):
        self.register_and_verify()
        response = self.google(mode=None)             # the Login page sends no mode
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['redirect'], reverse('login_check_email'))

    def test_unverified_signup_can_be_retried(self):
        """Never typed the code (closed the tab): registering again just e-mails a fresh one."""
        grant_registration_code(self.client)
        self.google()
        response = self.google()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(User.objects.count(), 1)
        self.assertEqual(len(mail.outbox), 2)

    def test_verified_local_account_email_cannot_be_registered_with_google(self):
        user = User.objects.create_user('pat', email=self.EMAIL, password='Right-pw1')
        UserProfile.objects.create(user=user, email_verified_at=user.date_joined)
        grant_registration_code(self.client)
        response = self.google()
        self.assertEqual(response.status_code, 409)
        user.refresh_from_db()
        self.assertTrue(user.has_usable_password())   # nothing was linked or wiped
