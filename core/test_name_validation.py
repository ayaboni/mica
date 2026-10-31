import json

from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from .models import UserProfile
from .name_validation import (
    normalize_name, sanitize_person_name, validate_person_name, validate_person_names, validate_username)

XSS_NAMES = [
    '<img src=x onerror=alert(1)>',
    '"><svg/onload=alert(1)>',
    "');alert(1);//",
    '{{7*7}}',
]


class NameValidatorTests(SimpleTestCase):
    def test_real_world_names_are_accepted(self):
        for name in ["Maria", "Ma. Cristina", "de la Cruz", "O'Brien", "O\u2019Brien",
                     "Santos-Reyes", "Pe\u00f1a", "Jos\u00e9 Luis", "Jr.", "Nguy\u1ec5n", "\u674e"]:
            self.assertIsNone(validate_person_name(name), name)

    def test_markup_and_odd_characters_are_rejected(self):
        for name in XSS_NAMES + ["a&b", "John3", "\U0001F600 Ann", "Ann\u200bLee", "Ann/Lee", "a`b", "..."]:
            self.assertIsNotNone(validate_person_name(name), name)

    def test_empty_and_too_long(self):
        self.assertIsNotNone(validate_person_name("   "))
        self.assertIsNone(validate_person_name("", required=False))
        self.assertIsNotNone(validate_person_name("x" * 151))

    def test_first_and_last_together(self):
        self.assertIsNone(validate_person_names("Sam", "Cruz"))
        self.assertIsNotNone(validate_person_names("Sam", ""))
        self.assertIsNone(validate_person_names("Sam", "", require_last=False))
        self.assertIn("last name", validate_person_names("Sam", "<b>"))

    def test_normalize_and_sanitize(self):
        self.assertEqual(normalize_name("  Ma.   Cristina \t\n Cruz "), "Ma. Cristina Cruz")
        self.assertEqual(sanitize_person_name('<b>Maria</b> "Lu" Santos'), "bMariab Lu Santos")
        self.assertEqual(sanitize_person_name("\U0001F600"), "")


class AccountSettingsNameTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('lena', email='lena@example.com', password='Old-pw-123!',
                                             first_name='Lena', last_name='Cruz')
        UserProfile.objects.create(user=self.user)
        self.client.force_login(self.user)

    def post(self, first, last):
        return self.client.post(reverse('account_settings'), {
            'username': 'lena', 'email': 'lena@example.com', 'first_name': first, 'last_name': last})

    def test_markup_in_a_name_is_refused_and_nothing_is_saved(self):
        for evil in XSS_NAMES:
            self.post('Lena', evil)
            self.user.refresh_from_db()
            self.assertEqual(self.user.last_name, 'Cruz', evil)

    def test_a_normal_name_change_still_works(self):
        self.post("Ma.  Lena", "O'Brien")
        self.user.refresh_from_db()
        self.assertEqual((self.user.first_name, self.user.last_name), ("Ma. Lena", "O'Brien"))


class StaffProfileNameTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('boss', 'boss@example.com', 'Admin-pass-123!',
                                                   first_name='Boss', last_name='Person')
        self.client.force_login(self.admin)

    def put(self, **data):
        return self.client.post(reverse('staff_profile_update'), json.dumps(data),
                                content_type='application/json')

    def test_markup_is_rejected_with_400(self):
        for evil in XSS_NAMES:
            self.assertEqual(self.put(first_name=evil, last_name='X').status_code, 400, evil)
            self.assertEqual(self.put(first_name='Ok', last_name=evil).status_code, 400, evil)
        self.admin.refresh_from_db()
        self.assertEqual(self.admin.first_name, 'Boss')

    def test_valid_name_is_saved(self):
        response = self.put(first_name='Maria Luisa', last_name='Santos-Reyes')
        self.assertEqual(response.status_code, 200)
        self.admin.refresh_from_db()
        self.assertEqual((self.admin.first_name, self.admin.last_name), ('Maria Luisa', 'Santos-Reyes'))


class UsernameRuleTests(SimpleTestCase):
    def test_good_usernames(self):
        for name in ["sam", "maria.santos", "jose_p", "Victim_Vic", "user0", "x" * 30]:
            self.assertIsNone(validate_username(name), name)

    def test_bad_usernames(self):
        for name in ["me", "<script>alert(1)</script>", "x" * 31, "x" * 300, "ann\u0430lee",   # Cyrillic a
                     "a@b.com", "a+b", "has space", ".dot", "-dash", "juan\n", "", "na\u00efve"]:
            self.assertIsNotNone(validate_username(name), repr(name))


class AccountSettingsUsernameTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('lena', email='lena@example.com', password='Old-pw-123!',
                                             first_name='Lena', last_name='Cruz')
        UserProfile.objects.create(user=self.user)
        self.client.force_login(self.user)

    def post(self, username, **extra):
        return self.client.post(reverse('account_settings'), {
            'username': username, 'first_name': 'Lena', 'last_name': 'Cruz', **extra})

    def test_bad_usernames_are_refused_and_nothing_is_saved(self):
        for bad in ['<img src=x onerror=alert(1)>', 'x' * 300, 'lena\u0430', 'a@b.com', 'ab']:
            self.post(bad)
            self.user.refresh_from_db()
            self.assertEqual(self.user.username, 'lena', bad)

    def test_a_valid_new_username_works(self):
        self.post('lena.cruz_2')
        self.user.refresh_from_db()
        self.assertEqual(self.user.username, 'lena.cruz_2')

    def test_an_older_loose_username_can_still_save_other_settings(self):
        User.objects.filter(pk=self.user.pk).update(username='old@school')   # created before the rule existed
        self.client.force_login(User.objects.get(pk=self.user.pk))
        self.post('old@school', nickname='Len')
        self.user.refresh_from_db()
        self.assertEqual(self.user.username, 'old@school')
        self.assertEqual(UserProfile.objects.get(user=self.user).nickname, 'Len')
