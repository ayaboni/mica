"""Offensive names, usernames and email addresses can't be used to register."""
import json
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core import mail
from django.test import SimpleTestCase, TestCase, override_settings
from django.urls import reverse

from .models import StaffSubclass, UserProfile
from .name_validation import (email_has_blocked_term, has_blocked_term, validate_email_wording,
                              validate_person_name, validate_username)
from .regcode_testing import grant_registration_code, make_staff_code
from .views import firebase_auth

STUDENT_FORM = {
    'first_name': 'Sam', 'last_name': 'Cruz', 'student_number': '2024-1111',
    'course': 'BSN', 'year_level': '1', 'username': 'samcruz', 'email': 'sam@example.com',
    'password': 'Pw-12345', 'confirm_password': 'Pw-12345',
}
STAFF_PASSWORD = 'Sturdy-pass-9271!'
STAFF_FORM = {
    'first_name': 'Maria', 'last_name': 'Santos', 'username': 'msantos', 'email': 'maria@example.com',
    'license_number': 'PRC-1234', 'password': STAFF_PASSWORD, 'confirm_password': STAFF_PASSWORD,
}


class WordFilterTests(SimpleTestCase):
    OFFENSIVE = [
        "fuck", "F U C K", "f.u.c.k", "fvck", "a$$hole", "$h1t", "n1gg3r", "NIGGA", "ni.gg.er", "niiiigger",
        "faggot", "retard", "k1ke", "ch1nk", "putangina", "pu7ang1na", "xxbitchxx", "dick123", "sex!", "hitler88",
    ]
    FINE = [
        "Chinkee Tan", "Fukuda", "Kikuchi", "Essex", "Hancock", "Cockburn", "Dickinson", "Analyn", "Assunta",
        "Santos-Reyes", "O'Brien", "Peña", "Ma. Cristina de la Cruz", "Bob", "Nigel", "Niger", "Nigeria",
        "Pakistan", "Spicer", "Kiko", "Shitara", "Titus", "Sussex", "Nudelman",
    ]

    def test_offensive_words_are_caught_even_when_disguised(self):
        for word in self.OFFENSIVE:
            self.assertTrue(has_blocked_term(word), word)

    def test_real_names_that_only_look_similar_are_not_blocked(self):
        for word in self.FINE:
            self.assertFalse(has_blocked_term(word), word)

    def test_names_and_usernames_use_the_same_filter(self):
        self.assertEqual(validate_person_name("Nigger", "first name"), "Please use your real first name.")
        self.assertIsNone(validate_person_name("Chinkee", "first name"))
        self.assertTrue(validate_username("xx_faggot_xx"))
        self.assertIsNone(validate_username("juan_dc"))

    def test_emails_are_checked_before_and_after_the_at_sign(self):
        for email in ["fuckyou@gmail.com", "john.nigger@gmail.com", "bitch123@yahoo.com", "n1gga99@gmail.com",
                      "a@fuck.com", "student@sex.com", "sex.guy@gmail.com"]:
            self.assertTrue(email_has_blocked_term(email), email)
            self.assertTrue(validate_email_wording(email), email)
        for email in ["juan.delacruz@gmail.com", "maria_santos@my.xu.edu.ph", "kikuchi.tanaka@gmail.com",
                      "essex.student@gmail.com", "chinkee.tan@gmail.com", "scunthorpe@essex.ac.uk"]:
            self.assertFalse(email_has_blocked_term(email), email)
            self.assertIsNone(validate_email_wording(email), email)


class StudentRegistrationTests(TestCase):
    def setUp(self):
        grant_registration_code(self.client)

    def register(self, **overrides):
        return self.client.post(reverse('register'), {**STUDENT_FORM, **overrides})

    def test_a_clean_registration_still_works(self):
        self.assertRedirects(self.register(), reverse('login'), fetch_redirect_response=False)
        self.assertTrue(User.objects.filter(username='samcruz').exists())

    def test_an_offensive_email_is_refused(self):
        response = self.register(email='nigga.sam@example.com')
        self.assertContains(response, 'Please use a different email address.')
        self.assertFalse(User.objects.exists())

    def test_an_offensive_name_is_refused(self):
        self.assertContains(self.register(first_name='Fuck'), 'Please use your real first name.')
        self.assertContains(self.register(last_name='Faggot'), 'Please use your real last name.')
        self.assertFalse(User.objects.exists())

    def test_an_offensive_username_is_refused(self):
        self.assertContains(self.register(username='big_bitch'), 'Please choose a different username.')
        self.assertFalse(User.objects.exists())


class StaffRegistrationTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('boss', 'boss@example.com', 'Admin-pass-123!')
        self.role = StaffSubclass.objects.create(name='Guidance Associate')
        self.client.post(reverse('register'), {'step': 'code', 'registration_code':
                                               make_staff_code(self.role, self.admin).code})

    def test_an_offensive_email_is_refused(self):
        response = self.client.post(reverse('register'), {**STAFF_FORM, 'email': 'bitch@example.com'})
        self.assertContains(response, 'Please use a different email address.')
        self.assertFalse(User.objects.filter(username='msantos').exists())

    def test_a_clean_staff_registration_still_works(self):
        self.client.post(reverse('register'), STAFF_FORM)
        self.assertTrue(User.objects.filter(username='msantos').exists())


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend', SITE_URL='https://mica.test')
class GoogleRegistrationTests(TestCase):
    def google(self, email, name='Dana Cruz', uid='uid-dana'):
        claims = {'uid': uid, 'email': email, 'email_verified': True, 'name': name,
                  'firebase': {'sign_in_provider': 'google.com'}}
        with patch.object(firebase_auth, 'verify_id_token', return_value=claims):
            return self.client.post(reverse('google_register'), data=json.dumps({'id_token': 't', 'mode': 'register'}),
                                    content_type='application/json')

    def test_an_offensive_google_email_cannot_start_a_new_account(self):
        grant_registration_code(self.client)
        response = self.google('nigger.dana@gmail.com')
        self.assertEqual(response.status_code, 403)
        self.assertIn("can't be used to register", response.json()['error'])
        self.assertFalse(User.objects.exists())

    def test_a_clean_google_account_still_registers(self):
        grant_registration_code(self.client)
        self.assertEqual(self.google('dana@gmail.com').status_code, 200)
        self.assertEqual(User.objects.get(email='dana@gmail.com').first_name, 'Dana')

    def test_an_offensive_google_display_name_is_not_copied_into_the_account(self):
        grant_registration_code(self.client)
        self.assertEqual(self.google('dana@gmail.com', name='Fuck Face').status_code, 200)
        user = User.objects.get(email='dana@gmail.com')
        self.assertEqual((user.first_name, user.last_name), ('', ''))


class ProfileSetupAfterGoogleTests(TestCase):
    """Used to crash with a NameError (500) instead of showing the name error."""

    def test_an_offensive_name_shows_an_error_instead_of_crashing(self):
        user = User.objects.create_user('google_x', 'dana@gmail.com', first_name='Dana', last_name='Cruz')
        UserProfile.objects.create(user=user, needs_profile_setup=True)
        self.client.force_login(user)
        response = self.client.post(reverse('complete_profile'), {
            'first_name': 'Fuck', 'last_name': 'Cruz', 'student_number': '2024-2222',
            'course': 'BSN', 'year_level': '1', 'username': 'danac'})
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Please use your real first name.')


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class EmailChangeTests(TestCase):
    PW = 'Old-pw-123!'

    def setUp(self):
        self.user = User.objects.create_user('lena', email='lena@example.com', password=self.PW, first_name='Lena')
        UserProfile.objects.create(user=self.user)
        self.client.force_login(self.user)

    def test_an_offensive_new_email_is_refused_and_nothing_is_sent(self):
        self.client.post(reverse('change_email'), {
            'action': 'send_code', 'new_email': 'fuckyou@example.com', 'current_password': self.PW})
        self.assertEqual(len(mail.outbox), 0)
        self.user.refresh_from_db()
        self.assertEqual(self.user.email, 'lena@example.com')
