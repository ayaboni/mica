"""Batch codes, the shared term code, unverified accounts, and Mica's daily cap."""
import json
from datetime import timedelta
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import Client, TestCase, override_settings
from django.urls import reverse
from django.utils import timezone

from . import registration_codes as rc
from .models import (AuditEvent, ChatConversation, ChatMessage, GuidanceMessage, GuidanceThread, MoodEntry,
                     RegistrationCode, StaffProfile, StaffSubclass, StudentFlag, UserProfile)
from .regcode_testing import make_code, make_shared_code, put_code_in_session
from .views import firebase_auth

FORM = {
    'first_name': 'Sam', 'last_name': 'Cruz', 'student_number': '2024-1111',
    'course': 'BSN', 'year_level': '1', 'username': 'sam', 'email': 'sam@example.com',
    'password': 'Pw-12345', 'confirm_password': 'Pw-12345',
}


def post_json(client, name, body=None, **kwargs):
    return client.post(reverse(name, kwargs=kwargs or None), json.dumps(body or {}), content_type='application/json')


def make_student(username, status='verified', **profile):
    user = User.objects.create_user(username, '%s@example.com' % username, 'Pw-12345',
                                    first_name=username.title(), last_name='Cruz')
    profile.setdefault('student_number', '2024-%s' % username)
    UserProfile.objects.create(user=user, course='BSN', year_level='1', verification_status=status, **profile)
    return user


def make_staff(username='staffer', **perms):
    """A staff member. With perms, they get a role with exactly those switches on."""
    user = User.objects.create_user(username, '%s@example.com' % username, 'Pw-12345', is_staff=True)
    if perms:
        role = StaffSubclass.objects.create(name='Role for %s' % username, **perms)
        StaffProfile.objects.filter(user=user).update(subclass=role)
    return user


def register_with(code, n=1):
    """A fresh browser that types `code`, then registers student number n."""
    client = Client()
    client.post(reverse('register'), {'step': 'code', 'registration_code': code.code})
    form = {**FORM, 'username': 'stu%d' % n, 'student_number': '2024-%04d' % n,
            'email': 'stu%d@example.com' % n}
    return client, client.post(reverse('register'), form)


# -- Batches ---------------------------------------------------------------------------------------

class BatchTests(TestCase):
    def setUp(self):
        self.staff = make_staff()
        self.client.force_login(self.staff)

    def batch(self, **body):
        return post_json(self.client, 'staff_registration_batch', body)

    def test_makes_the_requested_single_use_codes(self):
        response = self.batch(count=5, label='BSN 1st years', days=3)
        self.assertEqual(response.status_code, 200)
        info = response.json()['new_batch']
        self.assertEqual(len(info['codes']), 5)
        self.assertEqual(len(set(info['codes'])), 5)
        rows = RegistrationCode.objects.filter(batch=info['batch'])
        self.assertEqual(rows.count(), 5)
        for row in rows:
            self.assertEqual((row.kind, row.max_uses, row.use_count, row.label, row.created_by),
                             ('single', 1, 0, 'BSN 1st years', self.staff))
            self.assertEqual(len(row.code), 6)
            self.assertAlmostEqual((row.expires_at - timezone.now()).total_seconds(), 3 * 86400, delta=60)

    def test_batch_codes_stay_out_of_the_single_code_list(self):
        self.batch(count=5)
        data = self.client.get(reverse('staff_registration_codes')).json()
        self.assertEqual(data['codes'], [])
        self.assertEqual(data['batches'][0]['total'], 5)
        self.assertEqual(data['batches'][0]['active'], 5)

    def test_size_and_length_limits(self):
        for bad in ({}, {'count': 0}, {'count': rc.MAX_BATCH + 1}, {'count': 'lots'},
                    {'count': 5, 'days': 0}, {'count': 5, 'days': rc.BATCH_MAX_DAYS + 1}):
            self.assertEqual(self.batch(**bad).status_code, 400, bad)
        self.assertFalse(RegistrationCode.objects.exists())

    def test_the_one_a_minute_limit_covers_batches(self):
        self.assertEqual(self.batch(count=2).status_code, 200)
        self.assertEqual(self.batch(count=2).status_code, 429)
        self.assertEqual(self.client.post(reverse('staff_registration_code_generate')).status_code, 429)

    def test_cannot_hold_too_many_unused_codes(self):
        RegistrationCode.objects.bulk_create([
            RegistrationCode(code='T%05d' % i, created_by=self.staff,
                             expires_at=timezone.now() + timedelta(days=1))
            for i in range(rc.BATCH_MAX_OUTSTANDING - 5)])
        RegistrationCode.objects.update(created_at=timezone.now() - timedelta(minutes=5))
        response = self.batch(count=10)
        self.assertEqual(response.status_code, 400)
        self.assertIn('unused codes', response.json()['error'])

    def test_batch_is_audited_without_the_codes(self):
        info = self.batch(count=3).json()['new_batch']
        event = AuditEvent.objects.get(event='reg_batch_created')
        for code in info['codes']:
            self.assertNotIn(code, event.detail)

    def test_csv_is_for_the_staff_member_who_made_it(self):
        info = self.batch(count=3, label='Slips').json()['new_batch']
        url = reverse('staff_registration_batch_csv', kwargs={'batch': info['batch']})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response['Content-Type'], 'text/csv')
        body = response.content.decode()
        for code in info['codes']:
            self.assertIn(code, body)
        self.client.force_login(make_staff('other'))
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_batch_detail_lists_codes_and_who_used_them(self):
        info = self.batch(count=2, label='Slips').json()['new_batch']
        client, response = register_with(RegistrationCode.objects.get(code=info['codes'][0]), 1)
        url = reverse('staff_registration_batch_detail', kwargs={'batch': info['batch']})
        data = self.client.get(url).json()
        self.assertEqual(len(data['codes']), 2)
        used = [c for c in data['codes'] if c['status'] == 'used']
        self.assertEqual(len(used), 1)
        self.assertEqual(used[0]['student_number'], '2024-0001')
        self.assertTrue(used[0]['used_by'])
        self.client.force_login(make_staff('other'))
        self.assertEqual(self.client.get(url).status_code, 404)

    def test_revoking_a_batch_stops_its_unused_codes(self):
        info = self.batch(count=3).json()['new_batch']
        first = RegistrationCode.objects.get(code=info['codes'][0])
        first.use_count, first.used_at = 1, timezone.now()
        first.save()
        response = post_json(self.client, 'staff_registration_revoke', {'batch': info['batch']})
        self.assertEqual(response.json()['revoked'], 2)
        self.assertIsNone(rc.find_usable_code(info['codes'][1]))
        self.assertIsNone(RegistrationCode.objects.get(pk=first.pk).revoked_at)

    def test_used_codes_and_other_peoples_codes_cannot_be_revoked(self):
        mine = make_code(created_by=self.staff, use_count=1, used_at=timezone.now())
        theirs = make_code(created_by=make_staff('other'))
        self.assertEqual(post_json(self.client, 'staff_registration_revoke', {'id': mine.id}).status_code, 404)
        self.assertEqual(post_json(self.client, 'staff_registration_revoke', {'id': theirs.id}).status_code, 404)
        self.assertIsNotNone(rc.find_usable_code(theirs.code))

    def test_batches_need_the_generate_codes_permission(self):
        self.client.force_login(make_staff('limited', generate_codes=False))
        self.assertEqual(self.batch(count=2).status_code, 403)
        self.assertFalse(RegistrationCode.objects.exists())


# -- The shared term code ---------------------------------------------------------------------------

class SharedCodeTests(TestCase):
    def setUp(self):
        cache.clear()
        self.staff = make_staff()
        self.client.force_login(self.staff)

    def shared(self, **over):
        body = {'label': '1st Sem AY 2026-2027', 'max_uses': 2, 'days': 10}
        body.update(over)
        return post_json(self.client, 'staff_registration_shared', body)

    def test_creates_a_capped_expiring_code(self):
        response = self.shared()
        self.assertEqual(response.status_code, 200)
        code = RegistrationCode.objects.get(code=response.json()['new_shared'])
        self.assertEqual((code.kind, code.max_uses, code.use_count, code.label),
                         ('shared', 2, 0, '1st Sem AY 2026-2027'))
        self.assertEqual(len(code.code), rc.SHARED_CODE_LENGTH)
        self.assertAlmostEqual((code.expires_at - timezone.now()).total_seconds(), 10 * 86400, delta=60)
        self.assertEqual(response.json()['shared'][0]['status'], 'active')

    def test_validation(self):
        for bad in ({'label': ''}, {'max_uses': 0}, {'max_uses': rc.SHARED_MAX_USES + 1},
                    {'days': 0}, {'days': rc.SHARED_MAX_DAYS + 1}, {'code': 'abc'}):
            self.assertEqual(self.shared(**bad).status_code, 400, bad)
        self.assertFalse(RegistrationCode.objects.exists())

    def test_custom_codes_are_normalised_and_must_be_new(self):
        self.assertEqual(self.shared(code=' bsn-2026 ').json()['new_shared'], 'BSN2026')
        RegistrationCode.objects.create(code='TAKEN1', expires_at=timezone.now() + timedelta(days=1))
        RegistrationCode.objects.update(created_at=timezone.now() - timedelta(minutes=5))
        response = self.shared(code='taken1')
        self.assertEqual(response.status_code, 400)
        self.assertIn('already in use', response.json()['error'])

    def test_it_is_audited(self):
        self.shared()
        self.assertTrue(AuditEvent.objects.filter(event='reg_shared_created').exists())

    def test_works_for_many_students_until_the_cap(self):
        code = make_shared_code(max_uses=2)
        for n in (1, 2):
            _, response = register_with(code, n)
            self.assertRedirects(response, reverse('login'), fetch_redirect_response=False)
        code.refresh_from_db()
        self.assertEqual(code.use_count, 2)
        third = self.client_class()
        response = third.post(reverse('register'), {'step': 'code', 'registration_code': code.code})
        self.assertContains(response, 'valid or has expired')
        self.assertEqual(User.objects.filter(username__startswith='stu').count(), 2)

    def test_cap_holds_when_two_browsers_hold_the_last_use(self):
        code = make_shared_code(max_uses=1)
        a, b = self.client_class(), self.client_class()
        put_code_in_session(a, code)
        put_code_in_session(b, code)
        first = a.post(reverse('register'), {**FORM, 'username': 'a1', 'student_number': 'A-1', 'email': 'a@example.com'})
        second = b.post(reverse('register'), {**FORM, 'username': 'b1', 'student_number': 'B-1', 'email': 'b@example.com'})
        self.assertEqual(first.status_code, 302)
        self.assertContains(second, 'registration code')
        self.assertFalse(User.objects.filter(username='b1').exists())

    def test_accounts_from_a_shared_code_start_unverified(self):
        code = make_shared_code()
        _, response = register_with(code, 1)
        profile = UserProfile.objects.get(user__username='stu1')
        self.assertEqual(profile.verification_status, 'unverified')
        self.assertEqual(profile.registered_code, code)
        self.assertIsNone(profile.verified_by)
        code.refresh_from_db()
        self.assertIsNone(code.used_by)         # a shared code isn't "used by" anyone in particular

    def test_accounts_from_a_single_use_code_are_verified(self):
        code = make_code()
        _, response = register_with(code, 1)
        profile = UserProfile.objects.get(user__username='stu1')
        self.assertEqual(profile.verification_status, 'verified')
        code.refresh_from_db()
        self.assertEqual((code.use_count, code.used_by.username), (1, 'stu1'))

    def test_expired_revoked_and_full_codes_are_refused(self):
        expired = make_shared_code(expires_at=timezone.now() - timedelta(minutes=1))
        revoked = make_shared_code(revoked_at=timezone.now())
        full = make_shared_code(max_uses=3, use_count=3)
        for code in (expired, revoked, full):
            response = self.client_class().post(reverse('register'), {'step': 'code', 'registration_code': code.code})
            self.assertContains(response, 'valid or has expired')

    def test_revoked_after_entry_but_before_registering(self):
        code = make_shared_code()
        browser = self.client_class()
        put_code_in_session(browser, code)
        code.revoked_at = timezone.now()
        code.save()
        response = browser.post(reverse('register'), FORM)
        self.assertContains(response, 'registration code')
        self.assertFalse(User.objects.filter(username='sam').exists())

    def test_staff_can_revoke_a_shared_code_a_colleague_made(self):
        code = make_shared_code(created_by=make_staff('colleague'))
        response = post_json(self.client, 'staff_registration_revoke', {'id': code.id})
        self.assertEqual(response.status_code, 200)
        self.assertIsNone(rc.find_usable_code(code.code))

    @patch('core.views._is_trusted_browser', return_value=True)
    def test_google_sign_up_with_a_shared_code_is_unverified(self, _):
        code = make_shared_code()
        browser = self.client_class()
        put_code_in_session(browser, code)
        claims = {'uid': 'uid-1', 'email': 'g@example.com', 'email_verified': True, 'name': 'Gina Lopez',
                  'firebase': {'sign_in_provider': 'google.com'}}
        with patch.object(firebase_auth, 'verify_id_token', return_value=claims):
            response = browser.post(reverse('google_register'), data=json.dumps({'id_token': 't'}),
                                    content_type='application/json')
        self.assertEqual(response.status_code, 200)
        profile = UserProfile.objects.get(firebase_uid='uid-1')
        self.assertEqual(profile.verification_status, 'unverified')
        self.assertEqual(profile.registered_code, code)
        code.refresh_from_db()
        self.assertEqual(code.use_count, 1)


# -- Unverified accounts are invisible to Guidance --------------------------------------------------

class HiddenFromGuidanceTests(TestCase):
    def setUp(self):
        self.staff = make_staff()
        self.verified = make_student('vera')
        self.unverified = make_student('ugo', status='unverified')
        for student in (self.verified, self.unverified):
            MoodEntry.objects.create(user=student, mood='4')
            GuidanceThread.objects.create(student=student)
            StudentFlag.objects.create(student=student, reason='checking in')
        self.client.force_login(self.staff)

    def ids(self, name, key):
        return {row['id'] for row in self.client.get(reverse(name)).json()[key]}

    def test_student_list(self):
        self.assertEqual(self.ids('staff_students_list', 'students'), {self.verified.id})

    def test_summary_and_assessments(self):
        self.assertEqual(len(self.client.get(reverse('staff_summary')).json()['students']), 1)
        self.assertEqual(self.ids('staff_assessments', 'students'), {self.verified.id})

    def test_dashboard_counts(self):
        data = self.client.get(reverse('staff_dashboard_stats')).json()
        self.assertEqual(data['students_total'], 1)
        self.assertEqual(data['pending_accounts'], 1)       # this staff member may approve, so they see the count

    def test_flags(self):
        names = {f['name'] for f in self.client.get(reverse('staff_flags')).json()['flags']}
        self.assertEqual(names, {'Vera Cruz'})

    def test_conversations(self):
        threads = self.client.get(reverse('staff_guidance_inbox')).json()['threads']
        self.assertEqual({t['student_id'] for t in threads}, {self.verified.id})

    def test_opening_an_unverified_student_directly_is_a_404(self):
        self.assertEqual(self.client.get(reverse('staff_student_notes', args=[self.unverified.id])).status_code, 404)
        self.assertEqual(self.client.get(reverse('staff_student_notes', args=[self.verified.id])).status_code, 200)
        self.assertEqual(self.client.post(reverse('staff_student_thread', args=[self.unverified.id])).status_code, 404)
        thread = GuidanceThread.objects.get(student=self.unverified)
        self.assertEqual(self.client.get(reverse('staff_guidance_thread', args=[thread.id])).status_code, 404)

    def test_rejected_accounts_are_hidden_too(self):
        UserProfile.objects.filter(user=self.verified).update(verification_status='rejected')
        self.assertEqual(self.ids('staff_students_list', 'students'), set())

    def test_approving_makes_them_appear(self):
        post_json(self.client, 'staff_unverified_approve', {'ids': [self.unverified.id]})
        self.assertEqual(self.ids('staff_students_list', 'students'), {self.verified.id, self.unverified.id})

    def test_unverified_students_cannot_message_guidance(self):
        browser = self.client_class()
        browser.force_login(self.unverified)
        response = post_json(browser, 'guidance_send', {'message': 'hello?'})
        self.assertEqual(response.status_code, 403)
        self.assertTrue(response.json()['unverified'])
        self.assertFalse(GuidanceMessage.objects.exists())

    def test_verified_students_still_can(self):
        browser = self.client_class()
        browser.force_login(self.verified)
        self.assertEqual(post_json(browser, 'guidance_send', {'message': 'hello?'}).status_code, 200)


# -- Approving and deactivating -----------------------------------------------------------------------

class ApprovalTests(TestCase):
    def setUp(self):
        cache.clear()
        self.staff = make_staff()
        self.a = make_student('amy', status='unverified')
        self.b = make_student('ben', status='unverified')
        self.c = make_student('cat')                          # already verified
        self.client.force_login(self.staff)

    def status(self, user):
        return UserProfile.objects.get(user=user).verification_status

    def test_list_shows_only_accounts_waiting(self):
        UserProfile.objects.filter(user=self.b).update(registered_code=make_shared_code(label='BSN 1st'))
        make_student('dan', status='rejected')
        rows = self.client.get(reverse('staff_unverified')).json()['accounts']
        self.assertEqual({r['username'] for r in rows}, {'amy', 'ben'})
        ben = next(r for r in rows if r['username'] == 'ben')
        self.assertEqual(ben['code_label'], 'BSN 1st')
        self.assertEqual(ben['student_number'], '2024-ben')

    def test_list_says_whether_they_are_on_the_enrollment_list(self):
        from .models import EnrolledStudent
        EnrolledStudent.objects.create(student_number='2024-amy', last_name='Cruz')
        data = self.client.get(reverse('staff_unverified')).json()
        self.assertTrue(data['enrollment_list_loaded'])
        by_name = {r['username']: r['on_enrollment_list'] for r in data['accounts']}
        self.assertEqual(by_name, {'amy': True, 'ben': False})

    def test_bulk_approve(self):
        response = post_json(self.client, 'staff_unverified_approve', {'ids': [self.a.id, self.b.id, self.c.id]})
        self.assertEqual(response.json(), {'approved': 2, 'skipped': 1})
        for user in (self.a, self.b):
            profile = UserProfile.objects.get(user=user)
            self.assertEqual((profile.verification_status, profile.verified_by), ('verified', self.staff))
            self.assertIsNotNone(profile.verified_at)
        self.assertEqual(AuditEvent.objects.filter(event='accounts_verified').count(), 1)
        self.assertEqual(self.client.get(reverse('staff_unverified')).json()['accounts'], [])

    def test_approving_never_touches_staff_accounts(self):
        other = make_staff('colleague')
        response = post_json(self.client, 'staff_unverified_approve', {'ids': [other.id]})
        self.assertEqual(response.json()['approved'], 0)

    def test_bad_requests(self):
        for body in ({}, {'ids': []}, {'ids': 'all'}, {'ids': ['x']}):
            self.assertEqual(post_json(self.client, 'staff_unverified_approve', body).status_code, 400, body)
            self.assertEqual(post_json(self.client, 'staff_unverified_deactivate', body).status_code, 400, body)
        self.assertEqual(self.client.get(reverse('staff_unverified_approve')).status_code, 405)

    def test_deactivate_junk(self):
        browser = self.client_class()
        browser.force_login(self.a)
        response = post_json(self.client, 'staff_unverified_deactivate', {'ids': [self.a.id, self.c.id]})
        self.assertEqual(response.json(), {'deactivated': 1, 'skipped': 1})
        self.a.refresh_from_db()
        self.assertFalse(self.a.is_active)
        self.assertEqual(self.status(self.a), 'rejected')
        self.assertTrue(User.objects.get(pk=self.c.pk).is_active)             # verified students are never touched
        self.assertEqual(self.status(self.c), 'verified')
        self.assertFalse(self.client_class().login(username='amy', password='Pw-12345'))
        self.assertEqual(browser.get(reverse('student_dashboard')).status_code, 302)   # signed out
        self.assertEqual(AuditEvent.objects.filter(event='accounts_deactivated').count(), 1)

    def test_a_deactivated_account_does_not_block_the_real_student(self):
        post_json(self.client, 'staff_unverified_deactivate', {'ids': [self.a.id]})
        code = make_code()
        browser = self.client_class()
        put_code_in_session(browser, code)
        response = browser.post(reverse('register'), {**FORM, 'student_number': '2024-AMY', 'username': 'realamy',
                                                      'email': 'amy@example.com'})
        self.assertRedirects(response, reverse('login'), fetch_redirect_response=False)
        self.assertTrue(UserProfile.objects.filter(user__username='realamy', student_number='2024-AMY').exists())

    def test_admin_sees_the_status(self):
        admin = User.objects.create_superuser('boss', 'b@example.com', 'Pw-12345')
        self.client.force_login(admin)
        data = self.client.get(reverse('admin_students'), {'status': 'unverified'}).json()
        self.assertEqual({r['username'] for r in data['students']}, {'amy', 'ben'})
        self.assertEqual(data['counts']['unverified'], 2)


class ApprovalPermissionTests(TestCase):
    def setUp(self):
        self.pending = make_student('amy', status='unverified')

    def test_new_roles_do_not_get_it_by_default(self):
        self.assertFalse(StaffSubclass.objects.create(name='Plain').verify_students)

    def test_role_editor_offers_the_option(self):
        admin = User.objects.create_superuser('boss', 'b@example.com', 'Pw-12345')
        self.client.force_login(admin)
        keys = [f['key'] for f in self.client.get(reverse('admin_subclasses')).json()['fields']]
        self.assertIn('verify_students', keys)

    def test_a_role_without_it_is_refused_everywhere(self):
        self.client.force_login(make_staff('limited', verify_students=False))
        self.assertEqual(self.client.get(reverse('staff_unverified')).status_code, 403)
        self.assertEqual(post_json(self.client, 'staff_unverified_approve', {'ids': [self.pending.id]}).status_code, 403)
        self.assertEqual(post_json(self.client, 'staff_unverified_deactivate', {'ids': [self.pending.id]}).status_code, 403)
        self.assertEqual(UserProfile.objects.get(user=self.pending).verification_status, 'unverified')

    def test_a_role_with_it_may_approve(self):
        self.client.force_login(make_staff('approver', verify_students=True))
        self.assertEqual(post_json(self.client, 'staff_unverified_approve', {'ids': [self.pending.id]}).json()['approved'], 1)

    def test_the_pending_count_is_only_shown_to_those_who_can_act_on_it(self):
        self.client.force_login(make_staff('viewer', verify_students=False))
        self.assertEqual(self.client.get(reverse('staff_dashboard_stats')).json()['pending_accounts'], 0)

    def test_students_and_visitors_are_turned_away(self):
        self.client.force_login(make_student('stu'))
        self.assertEqual(self.client.get(reverse('staff_unverified')).status_code, 302)
        self.client.logout()
        self.assertEqual(self.client.get(reverse('staff_unverified')).status_code, 302)


# -- Mica's daily cap ---------------------------------------------------------------------------------

def fake_gemini(text='Hello from Mica.'):
    response = MagicMock()
    response.json.return_value = {'candidates': [{'content': {'parts': [{'text': text}]}}]}
    return response


@override_settings(MICA_DAILY_LIMIT=3, MICA_DAILY_LIMIT_UNVERIFIED=2, MICA_DAILY_LIMIT_UNVERIFIED_TOTAL=3)
class MicaCapTests(TestCase):
    def setUp(self):
        patcher = patch('core.views.requests.post', return_value=fake_gemini())
        self.post = patcher.start()
        self.addCleanup(patcher.stop)

    def chat(self, user, text='hi Mica'):
        browser = self.client_class()
        browser.force_login(user)
        return post_json(browser, 'chatbot_reply', {'message': text})

    def test_an_unverified_student_gets_a_small_daily_allowance(self):
        ugo = make_student('ugo', status='unverified')
        self.assertEqual(self.chat(ugo).status_code, 200)
        self.assertEqual(self.chat(ugo).status_code, 200)
        response = self.chat(ugo)
        self.assertEqual(response.status_code, 429)
        data = response.json()
        self.assertTrue(data['limit_reached'])
        self.assertIn('limit for today', data['reply'])         # the chat window shows `reply`
        self.assertEqual(self.post.call_count, 2)                # the AI wasn't called for the third
        self.assertEqual(ChatMessage.objects.filter(sender='user').count(), 2)

    def test_verified_students_have_a_bigger_allowance(self):
        vera = make_student('vera')
        for _ in range(3):
            self.assertEqual(self.chat(vera).status_code, 200)
        self.assertEqual(self.chat(vera).status_code, 429)

    def test_all_unverified_accounts_share_one_pool(self):
        one, two, three = (make_student(n, status='unverified') for n in ('one', 'two', 'three'))
        self.chat(one)
        self.chat(one)
        self.chat(two)                                           # pool is now 3 of 3
        response = self.chat(three)
        self.assertEqual(response.status_code, 429)
        self.assertIn('taking a rest', response.json()['reply'])
        self.assertEqual(self.post.call_count, 3)

    def test_verified_students_are_not_held_back_by_the_unverified_pool(self):
        for n in ('one', 'two'):
            ugo = make_student(n, status='unverified')
            self.chat(ugo)
            self.chat(ugo)
        self.assertEqual(self.chat(make_student('vera')).status_code, 200)

    def test_crisis_messages_are_always_answered(self):
        ugo = make_student('ugo', status='unverified')
        self.chat(ugo)
        self.chat(ugo)
        response = self.chat(ugo, 'I want to die')
        self.assertEqual(response.status_code, 200)
        self.assertIn('reach out to someone', response.json()['reply'])
        self.assertEqual(self.post.call_count, 2)                # answered by the built-in safety reply, no AI

    def test_the_allowance_resets_each_day(self):
        ugo = make_student('ugo', status='unverified')
        self.chat(ugo)
        self.chat(ugo)
        ChatMessage.objects.update(created_at=timezone.now() - timedelta(hours=25))
        self.assertEqual(self.chat(ugo).status_code, 200)

    def test_approval_lifts_the_small_allowance(self):
        ugo = make_student('ugo', status='unverified')
        self.chat(ugo)
        self.chat(ugo)
        self.assertEqual(self.chat(ugo).status_code, 429)
        UserProfile.objects.filter(user=ugo).update(verification_status='verified')
        self.assertEqual(self.chat(ugo).status_code, 200)


# -- Verified / Unverified label in account settings ---------------------------------------------

class AccountSettingsVerificationLabelTests(TestCase):
    def _settings_html(self, user):
        self.client.force_login(user)
        return self.client.get(reverse('account_settings')).content.decode()

    def test_shared_code_account_is_labelled_unverified(self):
        html = self._settings_html(make_student('shared1', status='unverified'))
        self.assertIn('id="verificationCard"', html)
        self.assertIn('>Unverified<', html)
        self.assertNotIn('>Verified<', html)

    def test_single_or_batch_code_account_is_labelled_verified(self):
        html = self._settings_html(make_student('single1', status='verified'))
        self.assertIn('>Verified<', html)
        self.assertNotIn('>Unverified<', html)

    def test_label_flips_once_staff_approve_the_account(self):
        user = make_student('shared2', status='unverified')
        self.assertIn('>Unverified<', self._settings_html(user))
        UserProfile.objects.filter(user=user).update(
            verification_status='verified', verified_at=timezone.now())
        html = self._settings_html(user)
        self.assertIn('>Verified<', html)
        self.assertNotIn('>Unverified<', html)
