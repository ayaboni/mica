from django.contrib.auth.models import User
from django.db import IntegrityError, transaction
from django.test import TestCase, SimpleTestCase, RequestFactory, override_settings
from django.urls import reverse
from django.utils import timezone
from datetime import date, timedelta
from core.reports import aggregate_sessions

import io
import json
import sys
import tempfile
import types
import wave
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile

from .models import LoginThrottle, UserProfile, AccessLog, StudentStatusLog
from .views import _load_speech_model, speech_to_text, firebase_auth, _safety_reply
from .hotlines import get_hotlines, DEFAULT_HOTLINES


def make_wav(sample_rate=16000, channels=1, sample_width=2, frame_count=1600):
    wav_data = io.BytesIO()
    with wave.open(wav_data, 'wb') as wav_file:
        wav_file.setnchannels(channels)
        wav_file.setsampwidth(sample_width)
        wav_file.setframerate(sample_rate)
        wav_file.writeframes(b'\0' * frame_count * channels * sample_width)
    return wav_data.getvalue()


class SpeechToTextTests(SimpleTestCase):
    def setUp(self):
        self.request_factory = RequestFactory()
        self.model_tempdir = tempfile.TemporaryDirectory()
        self.addCleanup(self.model_tempdir.cleanup)
        self.model_path = Path(self.model_tempdir.name)
        acoustic_model = self.model_path / "am" / "final.mdl"
        acoustic_model.parent.mkdir()
        acoustic_model.touch()
        self.addCleanup(_load_speech_model.cache_clear)

    def post_audio(self, audio_data):
        request = self.request_factory.post(
            '/api/speech-to-text/',
            {'audio': SimpleUploadedFile('voice.wav', audio_data, content_type='audio/wav')},
        )
        request.user = SimpleNamespace(is_authenticated=True)
        request._dont_enforce_csrf_checks = True
        return request

    def test_transcribes_valid_wav_with_local_model(self):
        fake_vosk = types.ModuleType('vosk')
        fake_vosk.Model = lambda model_path: object()

        class FakeRecognizer:
            def __init__(self, model, sample_rate):
                self.sample_rate = sample_rate

            def AcceptWaveform(self, audio_chunk):
                return True

            def FinalResult(self):
                return json.dumps({'text': 'hello there'})

        fake_vosk.KaldiRecognizer = FakeRecognizer
        with patch.dict(sys.modules, {'vosk': fake_vosk}), override_settings(VOSK_MODEL_PATH=self.model_path):
            response = speech_to_text(self.post_audio(make_wav()))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content), {'text': 'hello there'})

    def test_rejects_non_mono_wav(self):
        with override_settings(VOSK_MODEL_PATH=self.model_path):
            response = speech_to_text(self.post_audio(make_wav(channels=2)))

        self.assertEqual(response.status_code, 400)
        self.assertIn('16 kHz mono WAV', json.loads(response.content)['error'])


class GoogleRegisterTests(TestCase):
    VICTIM_EMAIL = 'victim@example.com'

    def setUp(self):
        # These tests cover account resolution, not the email-link step, so treat
        # the browser as already trusted. See test_email_login.py.
        patcher = patch('core.views._is_trusted_browser', return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def google_login(self, uid='uid-victim', email=VICTIM_EMAIL, email_verified=True, **extra):
        claims = {'uid': uid, 'email': email, 'name': 'Vic Tim',
                  'firebase': {'sign_in_provider': 'google.com'}, **extra}
        if email_verified is not None:
            claims['email_verified'] = email_verified
        with patch.object(firebase_auth, 'verify_id_token', return_value=claims):
            return self.client.post(
                reverse('google_register'),
                data=json.dumps({'id_token': 'tok'}),
                content_type='application/json',
            )

    def logged_in_user_id(self):
        return self.client.session.get('_auth_user_id')

    def test_rejects_unverified_email(self):
        response = self.google_login(email_verified=False)
        self.assertEqual(response.status_code, 403)
        self.assertFalse(User.objects.exists())
        self.assertIsNone(self.logged_in_user_id())

    def test_rejects_token_without_email_verified_claim(self):
        response = self.google_login(email_verified=None)
        self.assertEqual(response.status_code, 403)
        self.assertFalse(User.objects.exists())

    def test_invalid_token_is_400(self):
        with patch.object(firebase_auth, 'verify_id_token', side_effect=firebase_auth.InvalidIdTokenError('bad')):
            response = self.client.post(
                reverse('google_register'),
                data=json.dumps({'id_token': 'tok'}),
                content_type='application/json',
            )
        self.assertEqual(response.status_code, 400)

    def test_creates_user_keyed_on_uid_not_email(self):
        response = self.google_login()
        self.assertEqual(response.status_code, 200)
        user = User.objects.get(email=self.VICTIM_EMAIL)
        self.assertNotEqual(user.username, self.VICTIM_EMAIL)
        self.assertEqual(user.profile.firebase_uid, 'uid-victim')
        self.assertFalse(user.has_usable_password())
        self.assertEqual(self.logged_in_user_id(), str(user.pk))

    def test_returning_user_matched_by_uid_even_if_email_changed(self):
        self.google_login()
        self.client.logout()
        response = self.google_login(email='new-address@example.com')
        self.assertEqual(response.status_code, 200)
        self.assertEqual(User.objects.count(), 1)

    def test_preregistered_username_equal_to_victim_email_is_not_hijacked(self):
        attacker = User.objects.create_user(
            username=self.VICTIM_EMAIL, email='attacker@example.com', password='attacker-pw',
        )
        UserProfile.objects.create(user=attacker)

        response = self.google_login()

        self.assertEqual(response.status_code, 200)
        self.assertNotEqual(self.logged_in_user_id(), str(attacker.pk))
        victim = User.objects.get(profile__firebase_uid='uid-victim')
        self.assertNotEqual(victim.pk, attacker.pk)
        attacker.refresh_from_db()
        self.assertTrue(attacker.check_password('attacker-pw'))

    def test_links_existing_local_account_by_verified_email_and_drops_password(self):
        local = User.objects.create_user(
            username='vic', email='Victim@Example.com', password='pre-hijack-pw',
        )
        response = self.google_login()
        self.assertEqual(response.status_code, 200)
        local.refresh_from_db()
        self.assertEqual(local.profile.firebase_uid, 'uid-victim')
        self.assertFalse(local.has_usable_password())
        self.assertEqual(self.logged_in_user_id(), str(local.pk))

    def test_refuses_account_linked_to_a_different_google_uid(self):
        local = User.objects.create_user(username='vic', email=self.VICTIM_EMAIL, password='pw')
        UserProfile.objects.create(user=local, firebase_uid='someone-else')
        response = self.google_login()
        self.assertEqual(response.status_code, 409)
        self.assertIsNone(self.logged_in_user_id())
        local.refresh_from_db()
        self.assertTrue(local.check_password('pw'))

    def test_refuses_to_link_into_staff_accounts(self):
        staff = User.objects.create_user(
            username='counselor', email=self.VICTIM_EMAIL, password='pw', is_staff=True,
        )
        response = self.google_login()
        self.assertEqual(response.status_code, 403)
        self.assertIsNone(self.logged_in_user_id())
        staff.refresh_from_db()
        self.assertTrue(staff.check_password('pw'))

    def test_refuses_when_email_is_ambiguous(self):
        User.objects.create_user(username='a', email=self.VICTIM_EMAIL, password='pw')
        User.objects.create_user(username='b', email=self.VICTIM_EMAIL.upper(), password='pw')
        response = self.google_login()
        self.assertEqual(response.status_code, 409)
        self.assertIsNone(self.logged_in_user_id())

    def test_inactive_user_cannot_sign_in(self):
        self.google_login()
        self.client.logout()
        User.objects.update(is_active=False)
        response = self.google_login()
        self.assertEqual(response.status_code, 403)
        self.assertIsNone(self.logged_in_user_id())

    # --- first-time sign-in flow: registration details -> assessment -> dashboard ---

    PROFILE_FORM = {
        'first_name': 'Vic', 'last_name': 'Tim', 'student_number': '2024-0001',
        'course': 'BSN', 'year_level': '2', 'username': 'victim_vic',
    }

    def test_first_google_signin_redirects_to_complete_profile(self):
        response = self.google_login()
        self.assertEqual(response.json()['redirect'], reverse('complete_profile'))
        self.assertTrue(User.objects.get().profile.needs_profile_setup)

    def test_signin_before_finishing_setup_returns_to_setup(self):
        self.google_login()
        self.client.logout()
        response = self.google_login()
        self.assertEqual(response.json()['redirect'], reverse('complete_profile'))

    def test_dashboard_and_assessment_are_gated_until_setup_done(self):
        self.google_login()
        self.assertRedirects(self.client.get(reverse('student_dashboard')),
                             reverse('complete_profile'), fetch_redirect_response=False)
        self.assertRedirects(self.client.get(reverse('assessment')),
                             reverse('complete_profile'), fetch_redirect_response=False)

    def test_complete_profile_saves_details_then_goes_to_assessment(self):
        self.google_login()
        response = self.client.post(reverse('complete_profile'), self.PROFILE_FORM)
        self.assertRedirects(response, reverse('assessment'), fetch_redirect_response=False)
        user = User.objects.get()
        self.assertEqual(user.username, 'victim_vic')
        self.assertEqual(user.profile.student_number, '2024-0001')
        self.assertEqual(user.profile.course, 'BSN')
        self.assertFalse(user.profile.needs_profile_setup)
        self.assertEqual(self.logged_in_user_id(), str(user.pk))  # still signed in

    def test_later_signins_skip_setup(self):
        self.google_login()
        self.client.post(reverse('complete_profile'), self.PROFILE_FORM)
        self.client.logout()
        response = self.google_login()
        self.assertEqual(response.json()['redirect'], '/dashboard/student')

    def test_complete_profile_rejects_taken_username(self):
        User.objects.create_user(username='Victim_Vic', email='other@example.com', password='pw')
        self.google_login()
        response = self.client.post(reverse('complete_profile'), self.PROFILE_FORM)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, 'Username is already registered.')
        self.assertTrue(User.objects.get(profile__firebase_uid='uid-victim').profile.needs_profile_setup)

    def test_complete_profile_rejects_missing_or_invalid_fields(self):
        self.google_login()
        for bad in ({**self.PROFILE_FORM, 'username': ''},
                    {**self.PROFILE_FORM, 'course': 'NOPE'},
                    {**self.PROFILE_FORM, 'username': 'has space'}):
            response = self.client.post(reverse('complete_profile'), bad)
            self.assertEqual(response.status_code, 200)
        self.assertTrue(User.objects.get().profile.needs_profile_setup)

    def test_complete_profile_not_available_to_already_registered_users(self):
        User.objects.create_user(username='vic', email='x@example.com', password='pw')
        self.client.login(username='vic', password='pw')
        response = self.client.get(reverse('complete_profile'))
        self.assertRedirects(response, reverse('login'), fetch_redirect_response=False)

    def test_linked_existing_local_account_skips_setup(self):
        User.objects.create_user(username='vic', email=self.VICTIM_EMAIL, password='pw')
        response = self.google_login()
        self.assertEqual(response.json()['redirect'], '/dashboard/student')

    def test_complete_profile_rejects_taken_student_number(self):
        other = User.objects.create_user(username='other', email='o@example.com', password='pw')
        UserProfile.objects.create(user=other, student_number='2024-0001')
        self.google_login()
        response = self.client.post(
            reverse('complete_profile'), {**self.PROFILE_FORM, 'student_number': ' 2024-0001 '},
        )
        self.assertContains(response, 'Student number is already registered.')
        self.assertTrue(User.objects.get(profile__firebase_uid='uid-victim').profile.needs_profile_setup)


class RegisterUniquenessTests(TestCase):
    FORM = {
        'first_name': 'Sam', 'last_name': 'Cruz', 'student_number': '2024-1111',
        'course': 'BSN', 'year_level': '1', 'username': 'sam', 'email': 'sam@example.com',
        'password': 'Pw-12345', 'confirm_password': 'Pw-12345',
    }

    def register(self, **overrides):
        return self.client.post(reverse('register'), {**self.FORM, **overrides})

    def assert_rejected(self, response, message):
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, message)

    def test_successful_registration_stores_trimmed_values(self):
        response = self.register(username=' sam ', student_number=' 2024-1111 ')
        self.assertRedirects(response, reverse('login'), fetch_redirect_response=False)
        user = User.objects.get(username='sam')
        self.assertEqual(user.profile.student_number, '2024-1111')

    def test_username_must_be_unique_case_insensitively(self):
        self.register()
        response = self.register(username='SAM', student_number='2024-2222', email='other@example.com')
        self.assert_rejected(response, 'Username is already registered.')
        self.assertEqual(User.objects.count(), 1)

    def test_student_number_must_be_unique_case_and_whitespace_insensitively(self):
        self.register(student_number='ab-123')
        response = self.register(username='other', email='other@example.com', student_number=' AB-123 ')
        self.assert_rejected(response, 'Student number is already registered.')
        self.assertEqual(User.objects.count(), 1)

    def test_username_and_student_number_are_required(self):
        self.assert_rejected(self.register(student_number='  '), 'Username and student number are required.')
        self.assert_rejected(self.register(username=''), 'Username and student number are required.')
        self.assertFalse(User.objects.exists())

    def test_database_enforces_student_number_uniqueness(self):
        a = User.objects.create_user(username='a', password='pw')
        b = User.objects.create_user(username='b', password='pw')
        c = User.objects.create_user(username='c', password='pw')
        d = User.objects.create_user(username='d', password='pw')
        UserProfile.objects.create(user=a, student_number='X-1')
        with self.assertRaises(IntegrityError), transaction.atomic():
            UserProfile.objects.create(user=b, student_number='x-1')
        # blank student numbers (e.g. Google users mid-setup) may repeat
        UserProfile.objects.create(user=c, student_number='')
        UserProfile.objects.create(user=d, student_number='')

    def test_settings_page_cannot_take_an_existing_username(self):
        User.objects.create_user(username='Taken', email='t@example.com', password='pw')
        me = User.objects.create_user(username='me', email='me@example.com', password='pw')
        UserProfile.objects.create(user=me)
        self.client.login(username='me', password='pw')
        response = self.client.post(reverse('account_settings'), {'username': 'taken', 'email': 'me@example.com'})
        self.assertRedirects(response, reverse('account_settings'), fetch_redirect_response=False)
        me.refresh_from_db()
        self.assertEqual(me.username, 'me')


class PasswordRuleTests(TestCase):
    FORM = RegisterUniquenessTests.FORM

    def register(self, password):
        return self.client.post(
            reverse('register'), {**self.FORM, 'password': password, 'confirm_password': password},
        )

    def test_weak_passwords_are_rejected(self):
        for weak in ('Ab1!', 'abcdefg1!', 'Abcdefgh', 'ABCDEFGH', '        ', 'abcdefgh',
                     'Abcde1!',      # 7 characters: fine before, too short now
                     'Abcdef1'):     # 7 characters
            response = self.register(weak)
            self.assertContains(response, 'Password must be at least 8 characters')
            self.assertFalse(User.objects.exists(), weak)

    def test_valid_passwords_are_accepted(self):
        for i, good in enumerate(('Abcdef1!', 'Abcdefg!', 'abcdefE_', 'Longer pass 9', 'Abcdef12')):
            response = self.client.post(reverse('register'), {
                **self.FORM, 'password': good, 'confirm_password': good,
                'username': f'user{i}', 'student_number': f'S-{i}', 'email': f'u{i}@example.com',
            })
            self.assertRedirects(response, reverse('login'), fetch_redirect_response=False)


class LoginThrottleTests(TestCase):
    PASSWORD = 'Right-pw1'

    def setUp(self):
        self.user = User.objects.create_user(username='Sam', email='sam@example.com', password=self.PASSWORD)
        # These tests cover throttling, not the email-link step (test_email_login.py).
        patcher = patch('core.views._is_trusted_browser', return_value=True)
        patcher.start()
        self.addCleanup(patcher.stop)

    def attempt(self, password, username='Sam'):
        return self.client.post(reverse('login'), {'username': username, 'password': password})

    def logged_in(self):
        return '_auth_user_id' in self.client.session

    def expire_lock(self, key='sam'):
        LoginThrottle.objects.filter(username_key=key).update(
            locked_until=timezone.now() - timedelta(seconds=1))

    def test_shows_attempts_left_then_locks_on_fifth_failure(self):
        for expected in ('4 attempts left', '3 attempts left', '2 attempts left', '1 attempt left'):
            response = self.attempt('wrong')
            self.assertContains(response, expected)
        response = self.attempt('wrong')
        self.assertEqual(response.status_code, 429)
        self.assertContains(response, 'Too many failed attempts', status_code=429)
        self.assertContains(response, 'data-seconds="60"', status_code=429)

    def test_correct_password_is_refused_while_locked(self):
        for _ in range(5):
            self.attempt('wrong')
        response = self.attempt(self.PASSWORD)
        self.assertEqual(response.status_code, 429)
        self.assertFalse(self.logged_in())

    def test_can_log_in_after_cooldown_with_fresh_attempts(self):
        for _ in range(5):
            self.attempt('wrong')
        self.expire_lock()
        self.assertContains(self.attempt('wrong'), '4 attempts left')
        self.assertRedirects(self.attempt(self.PASSWORD), reverse('assessment'),
                             fetch_redirect_response=False)
        self.assertTrue(self.logged_in())

    def test_success_resets_the_counter(self):
        for _ in range(3):
            self.attempt('wrong')
        self.attempt(self.PASSWORD)
        self.assertFalse(LoginThrottle.objects.exists())
        self.client.logout()
        self.assertContains(self.attempt('wrong'), '4 attempts left')

    def test_lockout_is_per_username_and_ignores_case(self):
        User.objects.create_user(username='other', password=self.PASSWORD)
        for name in ('Sam', 'sam', 'SAM ', 'Sam', 'sAm'):
            self.attempt('wrong', username=name)
        self.assertEqual(self.attempt(self.PASSWORD).status_code, 429)
        self.assertEqual(self.attempt(self.PASSWORD, username='other').status_code, 302)

    def test_unknown_usernames_get_the_same_counter(self):
        response = self.attempt('wrong', username='nobody-here')
        self.assertContains(response, '4 attempts left')

    def test_old_failures_are_forgotten(self):
        for _ in range(3):
            self.attempt('wrong')
        LoginThrottle.objects.update(last_failed_at=timezone.now() - timedelta(minutes=16))
        self.assertContains(self.attempt('wrong'), '4 attempts left')


class ServiceCountTests(SimpleTestCase):
    def test_note_and_appointment_same_day_count_once(self):
        d = date(2026, 10, 5)
        r = aggregate_sessions([(1, 'counseling', d, 'BSIT', '2')], [(1, 'counseling', d, 'BSIT', '2')])
        self.assertEqual(r['by_service']['counseling'], 1)

    def test_walk_in_note_and_unnoted_appointment_both_count(self):
        d = date(2026, 10, 5)
        r = aggregate_sessions([(2, 'counseling', d, 'BSIT', '2')], [(4, 'testing', d, 'BAPSY', '3')])
        self.assertEqual(r['by_service']['counseling'], 1)
        self.assertEqual(r['by_service']['testing'], 1)


class AuditLogApiTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("boss", "boss@x.com", "Pass1234!")
        self.staff = User.objects.create_user("counselor1", "c@x.com", "Pass1234!",
                                              is_staff=True, first_name="Jo", last_name="Cruz")
        self.student = User.objects.create_user("stu1", "s@x.com", "Pass1234!",
                                                first_name="Ana", last_name="Lim")
        AccessLog.objects.create(staff=self.staff, student=self.student, action="open_chat")
        StudentStatusLog.objects.create(student=self.student, changed_by=self.admin,
                                        old_status="active", new_status="inactive", reason="dropped")

    def test_merges_both_logs(self):
        self.client.force_login(self.admin)
        data = self.client.get("/api/admin/audit/").json()
        self.assertEqual(data["total"], 2)

    def test_action_filter(self):
        self.client.force_login(self.admin)
        data = self.client.get("/api/admin/audit/?action=status_change").json()
        self.assertEqual(data["total"], 1)
        self.assertIn("Active to Archived", data["rows"][0]["detail"])

    def test_staff_filter(self):
        self.client.force_login(self.admin)
        data = self.client.get("/api/admin/audit/?staff=cruz").json()
        self.assertEqual(data["total"], 1)

    def test_csv_export(self):
        self.client.force_login(self.admin)
        r = self.client.get("/api/admin/audit/export/")
        self.assertEqual(r["Content-Type"], "text/csv")
        self.assertIn("Timestamp", r.content.decode())

    def test_non_admin_blocked(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get("/api/admin/audit/").status_code, 302)


class HotlineTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("boss2", "b2@x.com", "Pass1234!")
        self.staff = User.objects.create_user("c2", "c2@x.com", "Pass1234!", is_staff=True)

    def save(self, rows):
        self.client.force_login(self.admin)
        return self.client.post("/api/admin/hotlines/save/", data=json.dumps({"hotlines": rows}),
                                content_type="application/json")

    def test_defaults_when_nothing_saved(self):
        self.assertEqual(get_hotlines(), DEFAULT_HOTLINES)

    def test_saved_hotline_shows_in_crisis_reply(self):
        r = self.save([{"name": "Campus Line", "number": "0917-000-0000", "note": "24/7"}])
        self.assertEqual(r.status_code, 200)
        reply = _safety_reply("i want to die")
        self.assertIn("Campus Line", reply)
        self.assertIn("911", reply)

    def test_rejects_bad_number_and_empty_list(self):
        self.assertEqual(self.save([{"name": "Bad", "number": "<script>", "note": ""}]).status_code, 400)
        self.assertEqual(self.save([]).status_code, 400)

    def test_non_admin_blocked(self):
        self.client.force_login(self.staff)
        self.assertEqual(self.client.get("/api/admin/hotlines/").status_code, 302)