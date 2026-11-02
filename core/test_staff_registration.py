import json
from datetime import timedelta
from unittest.mock import patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from . import registration_codes as rc
from .models import AuditEvent, RegistrationCode, StaffProfile, StaffSubclass, UserProfile
from .regcode_testing import grant_registration_code, make_code, make_staff_code, put_code_in_session
from .views import firebase_auth

PASSWORD = 'Sturdy-pass-9271!'
FORM = {
    'first_name': 'Maria', 'last_name': 'Santos', 'username': 'msantos', 'email': 'maria@example.com',
    'license_number': 'PRC-1234', 'password': PASSWORD, 'confirm_password': PASSWORD,
}


def post_json(client, name, payload=None):
    return client.post(reverse(name), json.dumps(payload or {}), content_type='application/json')


class AdminStaffCodeTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser('boss', 'boss@example.com', 'Admin-pass-123!')
        self.role = StaffSubclass.objects.create(name='Guidance Associate')
        self.client.force_login(self.admin)

    def generate(self, **overrides):
        payload = {'subclass_id': self.role.id, **overrides}
        return post_json(self.client, 'admin_staff_codes_generate', payload)

    def test_admin_generates_a_single_use_staff_code_for_a_role(self):
        response = self.generate()
        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertEqual(len(data['new_codes']), 1)
        code = RegistrationCode.objects.get(code=data['new_codes'][0])
        self.assertEqual((code.audience, code.kind, code.max_uses), ('staff', 'single', 1))
        self.assertEqual(code.subclass, self.role)
        self.assertEqual(code.created_by, self.admin)
        self.assertEqual(len(code.code), rc.STAFF_CODE_LENGTH)
        self.assertGreater(code.expires_at, timezone.now())
        self.assertEqual(data['codes'][0]['role'], 'Guidance Associate')
        self.assertEqual(data['codes'][0]['status'], 'active')

    def test_several_codes_at_once_up_to_the_limit(self):
        self.assertEqual(len(self.generate(count=5).json()['new_codes']), 5)
        self.assertEqual(self.generate(count=rc.MAX_STAFF_BATCH + 1).status_code, 400)
        self.assertEqual(self.generate(count=0).status_code, 400)

    def test_a_role_is_required(self):
        self.assertEqual(post_json(self.client, 'admin_staff_codes_generate', {}).status_code, 400)
        self.assertEqual(self.generate(subclass_id=99999).status_code, 400)
        self.assertFalse(RegistrationCode.objects.exists())

    def test_lifetime_is_limited(self):
        self.assertEqual(self.generate(days=rc.STAFF_MAX_DAYS + 1).status_code, 400)
        self.assertEqual(self.generate(days=3).status_code, 200)

    def test_cap_on_unused_staff_codes(self):
        for _ in range(rc.STAFF_MAX_OUTSTANDING):
            make_staff_code(self.role, self.admin)
        self.assertEqual(self.generate().status_code, 400)

    def test_only_the_system_admin_can_make_staff_codes(self):
        staff = User.objects.create_user('staffer', 's@example.com', PASSWORD, is_staff=True)
        student = User.objects.create_user('stu', 'stu@example.com', PASSWORD)
        for user in (staff, student):
            self.client.force_login(user)
            self.assertNotEqual(self.generate().status_code, 200)
            self.assertNotEqual(self.client.get(reverse('admin_staff_codes')).status_code, 200)
        self.assertFalse(RegistrationCode.objects.exists())

    def test_staff_with_the_student_code_permission_still_cannot_make_staff_codes(self):
        self.role.generate_codes = True
        self.role.save()
        staff = User.objects.create_user('staffer', 's@example.com', PASSWORD, is_staff=True)
        staff.staff_profile.subclass = self.role
        staff.staff_profile.save()
        self.client.force_login(staff)
        self.assertEqual(self.client.post(reverse('staff_registration_code_generate')).status_code, 200)   # student code: fine
        self.assertNotEqual(self.generate().status_code, 200)                                             # staff code: no
        self.assertFalse(RegistrationCode.objects.filter(audience='staff').exists())

    def test_list_shows_staff_codes_and_who_used_them(self):
        used = make_staff_code(self.role, self.admin)
        member = User.objects.create_user('maria', 'm@example.com', PASSWORD, first_name='Maria', last_name='Santos', is_staff=True)
        used.use_count, used.used_by, used.used_at = 1, member, timezone.now()
        used.save()
        make_staff_code(self.role, self.admin)
        rows = self.client.get(reverse('admin_staff_codes')).json()['codes']
        self.assertEqual(sorted(r['status'] for r in rows), ['active', 'used'])
        self.assertIn('Maria Santos', [r['used_by'] for r in rows])

    def test_revoke_stops_a_code_working(self):
        code = make_staff_code(self.role, self.admin)
        response = post_json(self.client, 'admin_staff_codes_revoke', {'id': code.id})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(rc.find_usable_code(code.code))
        self.assertEqual(post_json(self.client, 'admin_staff_codes_revoke', {'id': code.id}).status_code, 404)

    def test_the_audit_log_records_it_without_the_code(self):
        code = self.generate().json()['new_codes'][0]
        event = AuditEvent.objects.get(event='staff_code_created')
        self.assertEqual(event.actor, self.admin)
        self.assertNotIn(code, event.detail)
        self.assertIn('Guidance Associate', event.detail)

    def test_staff_and_student_code_lists_stay_apart(self):
        make_staff_code(self.role, self.admin)
        student_code = make_code(self.admin)
        # The admin has the student-code permission too; their lists must not mix.
        self.assertEqual(self.client.get(reverse('staff_registration_codes')).json()['codes'][0]['code'], student_code.code)
        self.assertEqual(len(self.client.get(reverse('staff_registration_codes')).json()['codes']), 1)
        self.assertEqual(len(self.client.get(reverse('admin_staff_codes')).json()['codes']), 1)

    def test_making_a_staff_code_does_not_start_the_student_code_cooldown(self):
        self.generate()
        self.assertEqual(self.client.post(reverse('staff_registration_code_generate')).status_code, 200)

    def test_student_code_tools_cannot_revoke_staff_codes(self):
        code = make_staff_code(self.role, self.admin)
        response = post_json(self.client, 'staff_registration_revoke', {'id': code.id})
        self.assertEqual(response.status_code, 404)
        code.refresh_from_db()
        self.assertIsNone(code.revoked_at)

    def test_the_admin_can_no_longer_create_staff_directly(self):
        self.assertEqual(self.client.post('/api/admin/staff/create/', '{}', content_type='application/json').status_code, 404)


class StaffRegisterTests(TestCase):
    def setUp(self):
        cache.clear()
        self.admin = User.objects.create_superuser('boss', 'boss@example.com', 'Admin-pass-123!')
        self.role = StaffSubclass.objects.create(name='Guidance Associate')
        self.other_role = StaffSubclass.objects.create(name='Registered Guidance Counselor')

    def enter(self, code):
        return self.client.post(reverse('register'), {'step': 'code', 'registration_code': code.code})

    def register(self, **overrides):
        return self.client.post(reverse('register'), {**FORM, **overrides})

    # -- the code step --
    def test_a_staff_code_opens_the_staff_form_not_the_student_one(self):
        code = make_staff_code(self.role, self.admin)
        self.assertRedirects(self.enter(code), reverse('register'), fetch_redirect_response=False)
        page = self.client.get(reverse('register'))
        self.assertContains(page, 'Create a staff account')
        self.assertContains(page, 'Guidance Associate')
        self.assertContains(page, 'name="license_number"')
        self.assertNotContains(page, 'name="student_number"')
        self.assertNotContains(page, 'id="googleSignIn"')

    def test_a_student_code_still_opens_the_student_form(self):
        code = make_code(self.admin)
        self.enter(code)
        page = self.client.get(reverse('register'))
        self.assertContains(page, 'name="student_number"')
        self.assertNotContains(page, 'Create a staff account')

    def test_a_wrong_expired_or_revoked_staff_code_is_refused(self):
        expired = make_staff_code(self.role, self.admin, expires_at=timezone.now() - timedelta(minutes=1))
        revoked = make_staff_code(self.role, self.admin, revoked_at=timezone.now())
        for code in (expired, revoked):
            self.assertContains(self.enter(code), 'has expired')
        self.assertContains(self.client.post(reverse('register'), {'step': 'code', 'registration_code': 'NOPE1234'}), 'has expired')

    def test_a_staff_code_whose_role_was_deleted_is_dead(self):
        gone = StaffSubclass.objects.create(name='Temporary')
        code = make_staff_code(gone, self.admin)
        gone.delete()
        code.refresh_from_db()
        self.assertIsNone(code.subclass)
        self.assertContains(self.enter(code), 'has expired')

    # -- registering --
    def test_registering_creates_a_staff_account_with_the_role_from_the_code(self):
        code = make_staff_code(self.role, self.admin)
        self.enter(code)
        response = self.register()
        self.assertRedirects(response, reverse('login'), fetch_redirect_response=False)

        user = User.objects.get(username='msantos')
        self.assertTrue(user.is_staff)
        self.assertFalse(user.is_superuser)
        self.assertTrue(user.check_password(PASSWORD))
        self.assertEqual((user.first_name, user.last_name, user.email), ('Maria', 'Santos', 'maria@example.com'))
        profile = StaffProfile.objects.get(user=user)
        self.assertEqual(profile.subclass, self.role)
        self.assertEqual(profile.license_number, 'PRC-1234')
        self.assertFalse(profile.must_complete_setup)          # they chose their own password: no first-time setup
        self.assertFalse(UserProfile.objects.filter(user=user).exists())    # staff are not students

    def test_the_code_is_spent_and_cannot_be_used_twice(self):
        code = make_staff_code(self.role, self.admin)
        self.enter(code)
        self.register()
        code.refresh_from_db()
        self.assertEqual(code.use_count, 1)
        self.assertEqual(code.used_by.username, 'msantos')
        self.assertIsNotNone(code.used_at)
        self.assertNotIn(rc.SESSION_KEY, self.client.session)
        self.assertContains(self.enter(code), 'has expired')

    def test_the_role_cannot_be_chosen_on_the_form(self):
        code = make_staff_code(self.role, self.admin)
        self.enter(code)
        self.register(subclass_id=self.other_role.id, role='counselor', is_superuser='on', is_staff='on')
        user = User.objects.get(username='msantos')
        self.assertEqual(user.staff_profile.subclass, self.role)
        self.assertFalse(user.is_superuser)

    def test_registering_is_recorded_in_the_audit_log(self):
        self.enter(make_staff_code(self.role, self.admin))
        self.register()
        event = AuditEvent.objects.get(event='staff_registered')
        self.assertEqual(event.actor.username, 'msantos')
        self.assertIn('Guidance Associate', event.detail)

    def test_the_new_staff_member_shows_up_in_the_admin_staff_list(self):
        self.enter(make_staff_code(self.role, self.admin))
        self.register()
        self.client.force_login(self.admin)
        staff = self.client.get(reverse('admin_staff')).json()['staff']
        self.assertEqual([s['username'] for s in staff], ['msantos'])
        self.assertEqual(staff[0]['subclass'], 'Guidance Associate')
        self.assertFalse(staff[0]['fresh'])

    def test_a_student_code_never_makes_a_staff_account(self):
        code = make_code(self.admin)
        self.enter(code)
        self.client.post(reverse('register'), {
            'first_name': 'Sam', 'last_name': 'Cruz', 'student_number': '2024-1111', 'course': 'BSN',
            'year_level': '1', 'username': 'sam', 'email': 'sam@example.com',
            'password': 'Pw-12345', 'confirm_password': 'Pw-12345', 'is_staff': 'on',
        })
        sam = User.objects.get(username='sam')
        self.assertFalse(sam.is_staff)
        self.assertFalse(StaffProfile.objects.filter(user=sam).exists())
        self.assertTrue(UserProfile.objects.filter(user=sam).exists())

    # -- validation: nothing is created and the code is kept --
    def assertRefused(self, code, response, text=None):
        self.assertEqual(response.status_code, 200)
        if text:
            self.assertContains(response, text)
        self.assertFalse(User.objects.filter(username='msantos').exists())
        code.refresh_from_db()
        self.assertEqual(code.use_count, 0)
        self.assertEqual(self.client.session.get(rc.SESSION_KEY), code.pk)

    def test_bad_input_is_refused_and_keeps_the_code(self):
        code = make_staff_code(self.role, self.admin)
        self.enter(code)
        cases = [
            ({'confirm_password': 'different'}, 'Passwords do not match'),
            ({'password': 'short', 'confirm_password': 'short'}, None),
            ({'password': 'alllowercase1', 'confirm_password': 'alllowercase1'}, None),
            ({'password': 'Password1', 'confirm_password': 'Password1'}, None),        # too common
            ({'password': 'Msantos12!', 'confirm_password': 'Msantos12!'}, None),      # too like the username
            ({'username': 'a b'}, None),
            ({'username': 'ab'}, None),
            ({'email': 'nope'}, 'valid email'),
            ({'first_name': ''}, None),
            ({'last_name': '<b>x</b>'}, None),
        ]
        for overrides, text in cases:
            with self.subTest(overrides=overrides):
                self.assertRefused(code, self.register(**overrides), text)

    def test_username_and_email_must_be_unique(self):
        User.objects.create_user('taken', 'taken@example.com', PASSWORD)
        code = make_staff_code(self.role, self.admin)
        self.enter(code)
        self.assertRefused(code, self.register(username='TAKEN'), 'Username is already registered')
        self.assertRefused(code, self.register(email='TAKEN@example.com'), 'Email is already registered')

    def test_the_form_keeps_what_was_typed_but_never_the_password(self):
        code = make_staff_code(self.role, self.admin)
        self.enter(code)
        response = self.register(confirm_password='different')
        self.assertContains(response, 'value="msantos"')
        self.assertContains(response, 'value="maria@example.com"')
        self.assertNotContains(response, PASSWORD)

    def test_a_code_revoked_while_the_form_is_open_is_refused(self):
        code = make_staff_code(self.role, self.admin)
        self.enter(code)
        RegistrationCode.objects.filter(pk=code.pk).update(revoked_at=timezone.now())
        self.assertContains(self.register(), 'registration code')
        self.assertFalse(User.objects.filter(username='msantos').exists())

    def test_posting_the_form_without_a_code_is_refused(self):
        self.assertContains(self.register(), 'registration code')
        self.assertFalse(User.objects.filter(username='msantos').exists())

    # -- no Google for staff --
    def test_google_cannot_create_a_staff_account(self):
        code = make_staff_code(self.role, self.admin)
        put_code_in_session(self.client, code)
        claims = {'uid': 'uid-staff', 'email': 'g@example.com', 'email_verified': True, 'name': 'G Staff',
                  'firebase': {'sign_in_provider': 'google.com'}}
        with patch.object(firebase_auth, 'verify_id_token', return_value=claims):
            response = self.client.post(reverse('google_register'), data=json.dumps({'id_token': 't', 'mode': 'register'}),
                                        content_type='application/json')
        self.assertEqual(response.status_code, 403)
        self.assertFalse(User.objects.filter(email='g@example.com').exists())
        code.refresh_from_db()
        self.assertEqual(code.use_count, 0)
