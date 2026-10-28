import json
import re

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import StaffProfile, StaffSubclass


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend')
class FreshStaffAccountTests(TestCase):
    """Admin creates a staff account (no password) -> username-only sign-in with an e-mailed code
    -> first-time setup (password + details) -> an ordinary staff account."""
    PASSWORD = 'Sturdy-pass-9271!'

    def setUp(self):
        self.admin = User.objects.create_superuser('boss', 'boss@example.com', 'Admin-pass-123!')
        self.sub, _ = StaffSubclass.objects.get_or_create(name='Guidance Counselor')
        self.client.force_login(self.admin)

    # -- helpers --
    def create(self, **overrides):
        data = {'first_name': 'Maria', 'last_name': 'Santos', 'username': 'msantos',
                'email': 'maria@example.com', 'subclass_id': self.sub.id}
        data.update(overrides)
        return self.client.post(reverse('admin_staff_create'), json.dumps(data), content_type='application/json')

    def fresh_client(self):
        from django.test import Client
        return Client()

    def code(self):
        return re.search(r'^([A-Z2-9]{6})$', mail.outbox[-1].body, re.M).group(1)

    def signed_in(self, client):
        return '_auth_user_id' in client.session

    def sign_in_to_setup(self, password=''):
        """Run the username-only sign-in and return the client, now signed in (setup pending)."""
        c = self.fresh_client()
        c.post(reverse('login'), {'username': 'msantos', 'password': password})
        c.post(reverse('login_verify'), {'code': self.code()})
        return c

    # -- admin creates the account --
    def test_admin_creates_a_fresh_account_without_a_password(self):
        response = self.create()
        self.assertEqual(response.status_code, 201)
        user = User.objects.get(username='msantos')
        self.assertTrue(user.is_staff)
        self.assertFalse(user.has_usable_password())
        self.assertEqual((user.first_name, user.last_name, user.email), ('Maria', 'Santos', 'maria@example.com'))
        profile = user.staff_profile
        self.assertTrue(profile.must_complete_setup)
        self.assertEqual(profile.subclass, self.sub)
        self.assertEqual(response.json()['staff']['status'], 'Pending')
        self.assertTrue(response.json()['staff']['fresh'])
        self.assertEqual(len(mail.outbox), 0)              # nothing is e-mailed at creation

    def test_create_validates_the_fields(self):
        self.assertEqual(self.create(first_name='').status_code, 400)
        self.assertEqual(self.create(last_name='').status_code, 400)
        self.assertEqual(self.create(username='a b').status_code, 400)
        self.assertEqual(self.create(username='ab').status_code, 400)
        self.assertEqual(self.create(email='nope').status_code, 400)
        self.assertEqual(self.create(subclass_id=None).status_code, 400)
        self.assertFalse(User.objects.filter(username='msantos').exists())

    def test_username_and_email_must_be_unique(self):
        self.create()
        self.assertEqual(self.create(username='MSantos', email='other@example.com').status_code, 409)
        self.assertEqual(self.create(username='another', email='MARIA@example.com').status_code, 409)

    def test_only_admins_can_create_staff(self):
        staff = User.objects.create_user('someone', 'someone@example.com', self.PASSWORD, is_staff=True)
        self.client.force_login(staff)
        self.assertNotEqual(self.create().status_code, 201)

    # -- signing in with just the username --
    def test_username_alone_sends_a_code_instead_of_asking_for_a_password(self):
        self.create()
        c = self.fresh_client()
        response = c.post(reverse('login'), {'username': 'msantos', 'password': ''})
        self.assertRedirects(response, reverse('login_check_email'), fetch_redirect_response=False)
        self.assertEqual(len(mail.outbox), 1)
        self.assertEqual(mail.outbox[0].to, ['maria@example.com'])
        self.assertFalse(self.signed_in(c))

    def test_username_is_case_insensitive_and_any_password_typed_is_ignored(self):
        self.create()
        c = self.fresh_client()
        response = c.post(reverse('login'), {'username': 'MSANTOS', 'password': 'whatever'})
        self.assertRedirects(response, reverse('login_check_email'), fetch_redirect_response=False)

    def test_blank_password_does_not_work_for_an_ordinary_account(self):
        User.objects.create_user('lena', 'lena@example.com', self.PASSWORD, is_staff=True)
        c = self.fresh_client()
        response = c.post(reverse('login'), {'username': 'lena', 'password': ''})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(self.signed_in(c))

    def test_blank_password_does_not_work_for_admins_or_unknown_names(self):
        for name in ('boss', 'nobody'):
            c = self.fresh_client()
            c.post(reverse('login'), {'username': name, 'password': ''})
            self.assertFalse(self.signed_in(c))
        self.assertEqual(len(mail.outbox), 0)

    def test_a_suspended_fresh_account_cannot_start_sign_in(self):
        self.create()
        User.objects.filter(username='msantos').update(is_active=False)
        c = self.fresh_client()
        c.post(reverse('login'), {'username': 'msantos', 'password': ''})
        self.assertEqual(len(mail.outbox), 0)
        self.assertFalse(self.signed_in(c))

    def test_wrong_code_does_not_sign_in(self):
        self.create()
        c = self.fresh_client()
        c.post(reverse('login'), {'username': 'msantos', 'password': ''})
        right = self.code()
        c.post(reverse('login_verify'), {'code': 'ZZZZZZ' if right != 'ZZZZZZ' else 'YYYYYY'})
        self.assertFalse(self.signed_in(c))

    def test_right_code_signs_in_and_goes_to_setup(self):
        self.create()
        c = self.fresh_client()
        c.post(reverse('login'), {'username': 'msantos', 'password': ''})
        response = c.post(reverse('login_verify'), {'code': self.code()})
        self.assertRedirects(response, reverse('staff_setup'), fetch_redirect_response=False)
        self.assertTrue(self.signed_in(c))

    # -- nothing else is reachable until setup is done --
    def test_everything_redirects_to_setup_until_it_is_finished(self):
        self.create()
        c = self.sign_in_to_setup()
        for name in ('staff_dashboard', 'change_password'):
            response = c.get(reverse(name))
            self.assertRedirects(response, reverse('staff_setup'), fetch_redirect_response=False)
        api = c.get('/api/staff/anything/')
        self.assertEqual(api.status_code, 403)
        self.assertTrue(api.json()['setup_required'])
        self.assertEqual(c.get(reverse('staff_setup')).status_code, 200)

    def test_login_page_sends_a_half_set_up_account_back_to_setup(self):
        self.create()
        c = self.sign_in_to_setup()
        self.assertRedirects(c.get(reverse('login')), reverse('staff_setup'), fetch_redirect_response=False)

    def test_setup_page_is_only_for_fresh_staff(self):
        anon = self.fresh_client()
        self.assertEqual(anon.get(reverse('staff_setup')).status_code, 302)
        staff = User.objects.create_user('lena', 'lena@example.com', self.PASSWORD, is_staff=True)
        c = self.fresh_client()
        c.force_login(staff)
        response = c.get(reverse('staff_setup'))
        self.assertEqual(response.status_code, 302)
        self.assertNotIn('/staff/setup', response.url)

    # -- first-time setup --
    def setup(self, c, **overrides):
        data = {'new_password1': self.PASSWORD, 'new_password2': self.PASSWORD,
                'first_name': 'Maria', 'last_name': 'Santos', 'license_number': ''}
        data.update(overrides)
        return c.post(reverse('staff_setup'), data)

    def test_setup_needs_matching_strong_passwords_and_a_first_name(self):
        self.create()
        c = self.sign_in_to_setup()
        for bad in ({'new_password2': 'different'}, {'new_password1': 'short', 'new_password2': 'short'},
                    {'new_password1': '12345678', 'new_password2': '12345678'}, {'first_name': ''}):
            response = self.setup(c, **bad)
            self.assertEqual(response.status_code, 200)
            self.assertContains(response, '<ul class="errors">')
        user = User.objects.get(username='msantos')
        self.assertFalse(user.has_usable_password())
        self.assertTrue(user.staff_profile.must_complete_setup)

    def test_finishing_setup_sets_the_password_saves_details_and_clears_the_flag(self):
        self.create()
        c = self.sign_in_to_setup()
        response = self.setup(c, first_name='Maria Luisa', last_name='Santos-Reyes', license_number='PRC-1234')
        self.assertRedirects(response, reverse('staff_dashboard'), fetch_redirect_response=False)
        user = User.objects.get(username='msantos')
        self.assertTrue(user.check_password(self.PASSWORD))
        self.assertEqual((user.first_name, user.last_name), ('Maria Luisa', 'Santos-Reyes'))
        profile = StaffProfile.objects.get(user=user)
        self.assertFalse(profile.must_complete_setup)
        self.assertEqual(profile.license_number, 'PRC-1234')
        self.assertTrue(self.signed_in(c))                  # still signed in after setting the password
        after = c.get(reverse('staff_dashboard'))
        self.assertFalse(after.status_code == 302 and after.url == reverse('staff_setup'))

    def test_username_and_email_are_not_editable_in_setup(self):
        self.create()
        c = self.sign_in_to_setup()
        self.setup(c, username='hacker', email='hacker@example.com')
        self.assertEqual(User.objects.get(email='maria@example.com').username, 'msantos')
        self.assertFalse(User.objects.filter(username='hacker').exists())

    def test_after_setup_the_account_signs_in_like_any_other(self):
        self.create()
        self.setup(self.sign_in_to_setup())
        before = len(mail.outbox)
        # No more bypass: a blank password is refused...
        c = self.fresh_client()
        c.post(reverse('login'), {'username': 'msantos', 'password': ''})
        self.assertFalse(self.signed_in(c))
        self.assertEqual(len(mail.outbox), before)
        # ...and the new password works, followed by the usual e-mailed code.
        response = c.post(reverse('login'), {'username': 'msantos', 'password': self.PASSWORD})
        self.assertRedirects(response, reverse('login_check_email'), fetch_redirect_response=False)
        response = c.post(reverse('login_verify'), {'code': self.code()})
        self.assertRedirects(response, reverse('staff_dashboard'), fetch_redirect_response=False)

    def test_reset_password_is_refused_for_a_fresh_account(self):
        self.create()
        uid = User.objects.get(username='msantos').pk
        response = self.client.post(reverse('admin_staff_reset_password', args=[uid]), '{}', content_type='application/json')
        self.assertEqual(response.status_code, 400)
        self.assertEqual(len(mail.outbox), 0)

    def test_existing_staff_are_not_treated_as_fresh(self):
        staff = User.objects.create_user('lena', 'lena@example.com', self.PASSWORD, is_staff=True)
        staff.staff_profile.refresh_from_db()
        self.assertFalse(staff.staff_profile.must_complete_setup)
        c = self.fresh_client()
        c.force_login(staff)
        response = c.get(reverse('staff_dashboard'))
        self.assertFalse(response.status_code == 302 and response.url == reverse('staff_setup'))
