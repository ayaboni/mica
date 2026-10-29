import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from . import registration_codes as rc
from .models import AuditEvent, RegistrationCode, SiteSettings, StaffProfile, StaffSubclass, UserProfile
from .regcode_testing import grant_registration_code, make_code
from .views import firebase_auth

FORM = {
    'first_name': 'Sam', 'last_name': 'Cruz', 'student_number': '2024-1111',
    'course': 'BSN', 'year_level': '1', 'username': 'sam', 'email': 'sam@example.com',
    'password': 'Pw-12345', 'confirm_password': 'Pw-12345',
}


class CodeFormatTests(TestCase):
    def test_codes_are_six_unambiguous_characters(self):
        for _ in range(200):
            code = rc.new_code_value()
            self.assertEqual(len(code), 6)
            self.assertTrue(set(code) <= set(rc.REG_CODE_ALPHABET))
            self.assertFalse(set(code) & set('01OIL'))

    def test_typed_codes_are_normalised(self):
        self.assertEqual(rc.normalize_code(' ab3-k9z '), 'AB3K9Z')


class StaffGenerateTests(TestCase):
    def setUp(self):
        self.staff = User.objects.create_user('staffer', 's@example.com', 'Pw-12345', is_staff=True)
        self.client.force_login(self.staff)

    def generate(self):
        return self.client.post(reverse('staff_registration_code_generate'))

    def test_generates_a_usable_code_that_expires(self):
        response = self.generate()
        self.assertEqual(response.status_code, 200)
        data = response.json()
        code = RegistrationCode.objects.get(code=data['new_code'])
        self.assertEqual(len(code.code), 6)
        self.assertEqual(code.created_by, self.staff)
        self.assertIsNone(code.used_at)
        self.assertGreater(code.expires_at, timezone.now())
        self.assertEqual(data['codes'][0]['status'], 'active')

    def test_only_one_code_a_minute(self):
        self.assertEqual(self.generate().status_code, 200)
        response = self.generate()
        self.assertEqual(response.status_code, 429)
        self.assertGreater(response.json()['cooldown_seconds'], 0)
        self.assertEqual(RegistrationCode.objects.count(), 1)

    def test_allowed_again_after_a_minute(self):
        self.generate()
        RegistrationCode.objects.update(created_at=timezone.now() - timedelta(seconds=61))
        self.assertEqual(self.generate().status_code, 200)
        self.assertEqual(RegistrationCode.objects.count(), 2)

    def test_limit_is_per_staff_member(self):
        self.generate()
        other = User.objects.create_user('other', 'o@example.com', 'Pw-12345', is_staff=True)
        self.client.force_login(other)
        self.assertEqual(self.generate().status_code, 200)

    def test_list_reports_cooldown_and_own_codes_only(self):
        self.generate()
        other = User.objects.create_user('other', 'o@example.com', 'Pw-12345', is_staff=True)
        make_code(created_by=other)
        data = self.client.get(reverse('staff_registration_codes')).json()
        self.assertEqual(len(data['codes']), 1)
        self.assertGreater(data['cooldown_seconds'], 0)

    def test_students_and_visitors_cannot_use_it(self):
        student = User.objects.create_user('stu', 'st@example.com', 'Pw-12345')
        self.client.force_login(student)
        self.assertEqual(self.generate().status_code, 302)
        self.client.logout()
        self.assertEqual(self.generate().status_code, 302)
        self.assertFalse(RegistrationCode.objects.exists())

    def test_generation_is_audited_without_the_code(self):
        code = self.generate().json()['new_code']
        event = AuditEvent.objects.get(event='reg_code_created')
        self.assertNotIn(code, event.detail)

    def test_get_is_not_allowed_to_generate(self):
        self.assertEqual(self.client.get(reverse('staff_registration_code_generate')).status_code, 405)


class RolePermissionTests(TestCase):
    """The admin decides per role whether staff may generate codes."""

    def staff_with_role(self, allowed):
        role = StaffSubclass.objects.create(name='Role %s' % allowed, generate_codes=allowed)
        user = User.objects.create_user('u%s' % allowed, 'u@example.com', 'Pw-12345', is_staff=True)
        StaffProfile.objects.create(user=user, subclass=role)
        self.client.force_login(user)

    def test_role_without_the_permission_is_refused(self):
        self.staff_with_role(False)
        self.assertEqual(self.client.post(reverse('staff_registration_code_generate')).status_code, 403)
        self.assertEqual(self.client.get(reverse('staff_registration_codes')).status_code, 403)
        self.assertFalse(RegistrationCode.objects.exists())

    def test_role_with_the_permission_may_generate(self):
        self.staff_with_role(True)
        self.assertEqual(self.client.post(reverse('staff_registration_code_generate')).status_code, 200)

    def test_new_roles_do_not_get_it_by_default(self):
        self.assertFalse(StaffSubclass.objects.create(name='Plain').generate_codes)

    def test_admin_role_editor_offers_the_option(self):
        admin = User.objects.create_superuser('boss', 'b@example.com', 'Pw-12345')
        self.client.force_login(admin)
        keys = [f['key'] for f in self.client.get(reverse('admin_subclasses')).json()['fields']]
        self.assertIn('generate_codes', keys)


class RegisterGateTests(TestCase):
    def setUp(self):
        cache.clear()

    def enter(self, value):
        return self.client.post(reverse('register'), {'step': 'code', 'registration_code': value})

    def test_page_asks_for_a_code_first(self):
        response = self.client.get(reverse('register'))
        self.assertContains(response, 'registration_code')
        self.assertNotContains(response, 'name="username"')
        self.assertNotContains(response, 'id="googleSignIn"')

    def test_valid_code_shows_the_normal_registration_form(self):
        code = make_code()
        response = self.enter(code.code.lower())
        self.assertRedirects(response, reverse('register'), fetch_redirect_response=False)
        page = self.client.get(reverse('register'))
        self.assertContains(page, 'name="username"')
        self.assertContains(page, 'id="googleSignIn"')

    def test_wrong_code_is_refused(self):
        make_code()
        response = self.enter('ZZZZZZ')
        self.assertContains(response, 'valid or has expired')
        self.assertNotContains(response, 'name="username"')

    def test_expired_and_used_codes_are_refused(self):
        expired = make_code(expires_at=timezone.now() - timedelta(minutes=1))
        used = make_code(used_at=timezone.now())
        for code in (expired, used):
            self.assertContains(self.enter(code.code), 'valid or has expired')

    def test_cannot_register_without_a_code(self):
        response = self.client.post(reverse('register'), FORM)
        self.assertContains(response, 'registration code')
        self.assertFalse(User.objects.exists())

    def test_closed_registration_still_wins(self):
        s = SiteSettings.get()
        s.registration_open = False
        s.save()
        code = make_code()
        response = self.enter(code.code)
        self.assertContains(response, 'closed')

    def test_guessing_is_paused_after_too_many_wrong_codes(self):
        code = make_code()
        for _ in range(rc.ENTRY_MAX_FAILURES):
            self.enter('ZZZZZZ')
        self.assertContains(self.enter(code.code), 'Too many wrong codes')
        self.assertNotIn(rc.SESSION_KEY, self.client.session)


class RegisterWithCodeTests(TestCase):
    def setUp(self):
        cache.clear()

    def test_account_is_created_and_the_code_is_used_up(self):
        code = grant_registration_code(self.client)
        response = self.client.post(reverse('register'), FORM)
        self.assertRedirects(response, reverse('login'), fetch_redirect_response=False)
        user = User.objects.get(username='sam')
        code.refresh_from_db()
        self.assertEqual(code.used_by, user)
        self.assertIsNotNone(code.used_at)
        self.assertNotIn(rc.SESSION_KEY, self.client.session)

    def test_a_code_cannot_be_used_twice(self):
        code = grant_registration_code(self.client)
        self.client.post(reverse('register'), FORM)
        # A second browser that typed the same code before it was used
        other = self.client_class()
        session = other.session
        session[rc.SESSION_KEY] = code.pk
        session.save()
        response = other.post(reverse('register'), {**FORM, 'username': 'sam2', 'student_number': 'X-2', 'email': 'b@example.com'})
        self.assertContains(response, 'registration code')
        self.assertEqual(User.objects.count(), 1)

    def test_a_code_that_expires_mid_registration_is_refused(self):
        code = grant_registration_code(self.client)
        code.expires_at = timezone.now() - timedelta(seconds=1)
        code.save()
        response = self.client.post(reverse('register'), FORM)
        self.assertContains(response, 'registration code')
        self.assertFalse(User.objects.exists())

    def test_a_failed_registration_keeps_the_code(self):
        code = grant_registration_code(self.client)
        self.client.post(reverse('register'), {**FORM, 'confirm_password': 'different'})
        code.refresh_from_db()
        self.assertIsNone(code.used_at)
        self.assertEqual(self.client.session.get(rc.SESSION_KEY), code.pk)


class GoogleWithCodeTests(TestCase):
    def setUp(self):
        cache.clear()
        patcher = patch('core.views._is_trusted_browser', return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def google(self, uid='uid-new', email='new@example.com'):
        claims = {'uid': uid, 'email': email, 'email_verified': True, 'name': 'New Student',
                  'firebase': {'sign_in_provider': 'google.com'}}
        with patch.object(firebase_auth, 'verify_id_token', return_value=claims):
            return self.client.post(reverse('google_register'), data=json.dumps({'id_token': 't'}),
                                    content_type='application/json')

    def test_new_google_account_needs_a_code(self):
        response = self.google()
        self.assertEqual(response.status_code, 403)
        self.assertTrue(response.json()['needs_code'])
        self.assertFalse(User.objects.exists())

    def test_new_google_account_with_a_code_continues_to_profile_setup(self):
        code = grant_registration_code(self.client)
        response = self.google()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()['redirect'], reverse('complete_profile'))
        user = User.objects.get(email='new@example.com')
        code.refresh_from_db()
        self.assertEqual(code.used_by, user)

    def test_the_code_is_single_use_for_google_too(self):
        grant_registration_code(self.client)
        self.google()
        self.client.logout()
        # Same browser/session no longer holds a usable code
        response = self.google(uid='uid-two', email='two@example.com')
        self.assertEqual(response.status_code, 403)
        self.assertEqual(User.objects.count(), 1)

    def test_existing_google_users_sign_in_without_a_code(self):
        grant_registration_code(self.client)
        self.google()
        self.client.logout()
        response = self.google()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(User.objects.count(), 1)

    def test_expired_code_is_refused(self):
        code = grant_registration_code(self.client)
        code.expires_at = timezone.now() - timedelta(seconds=1)
        code.save()
        self.assertEqual(self.google().status_code, 403)
        self.assertFalse(User.objects.exists())
