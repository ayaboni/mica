"""The PANIC button: one press flags the student in Risk Alerts and notifies staff."""
import json

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from .models import ConsoleNotification, StudentFlag, UserProfile


class PanicButtonTests(TestCase):
    def setUp(self):
        cache.clear()
        self.student = User.objects.create_user("sam", password="Student-pass-1!", first_name="Sam", last_name="Reyes")
        UserProfile.objects.create(user=self.student)
        self.alice = User.objects.create_user("alice", password="Staff-pass-123!", is_staff=True)
        self.carol = User.objects.create_user("carol", password="Staff-pass-123!", is_staff=True)

    def press(self, user=None):
        self.client.force_login(user or self.student)
        return self.client.post(reverse("panic_alert"), json.dumps({}), content_type="application/json")

    def test_one_press_flags_the_student_and_notifies_staff(self):
        r = self.press()
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["created"])
        flag = StudentFlag.objects.get(student=self.student)
        self.assertTrue(flag.is_panic)
        self.assertEqual(flag.status, "new")
        for staff in (self.alice, self.carol):
            note = ConsoleNotification.objects.get(recipient=staff, kind="flag")
            self.assertTrue(note.urgent)
            self.assertEqual(note.section, "alerts")
            self.assertEqual(note.ref_id, flag.id)
            self.assertIn("Sam Reyes", note.text)

    def test_panic_flag_shows_in_risk_alerts_as_urgent_even_without_mood_data(self):
        self.press()
        self.client.force_login(self.alice)
        flags = self.client.get(reverse("staff_flags")).json()["flags"]
        self.assertEqual(len(flags), 1)
        self.assertTrue(flags[0]["panic"])
        self.assertEqual(flags[0]["severity"], "urgent")
        self.assertEqual(flags[0]["status"], "New")
        self.assertEqual(flags[0]["flagged_by"], "Student (panic button)")

    def test_pressing_again_does_not_create_a_second_flag_or_notification(self):
        self.press()
        r = self.press()
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["created"])
        self.assertEqual(StudentFlag.objects.filter(student=self.student).count(), 1)
        self.assertEqual(ConsoleNotification.objects.filter(recipient=self.alice, kind="flag").count(), 1)

    def test_an_existing_staff_flag_is_escalated_not_duplicated(self):
        flag = StudentFlag.objects.create(student=self.student, flagged_by=self.alice, reason="Low mood",
                                          status="review", assigned_to=self.carol)
        r = self.press()
        self.assertFalse(r.json()["created"])
        flag.refresh_from_db()
        self.assertTrue(flag.is_panic)
        self.assertEqual(flag.assigned_to, self.carol)       # the counselor on the case keeps it
        self.assertEqual(flag.reason, "Low mood")
        self.assertEqual(StudentFlag.objects.count(), 1)
        self.assertTrue(ConsoleNotification.objects.filter(recipient=self.carol, kind="flag").exists())

    def test_a_resolved_flag_does_not_block_a_new_panic_flag(self):
        StudentFlag.objects.create(student=self.student, flagged_by=self.alice, reason="Old", status="resolved")
        r = self.press()
        self.assertTrue(r.json()["created"])
        self.assertEqual(StudentFlag.objects.filter(student=self.student).exclude(status="resolved").count(), 1)

    def test_unverified_students_are_told_staff_cannot_see_them(self):
        ugo = User.objects.create_user("ugo", password="Student-pass-1!")
        UserProfile.objects.create(user=ugo, verification_status="unverified")
        r = self.press(ugo)
        self.assertEqual(r.status_code, 403)
        self.assertTrue(r.json()["unverified"])
        self.assertFalse(StudentFlag.objects.exists())

    def test_staff_cannot_press_it(self):
        r = self.press(self.alice)
        self.assertEqual(r.status_code, 403)
        self.assertFalse(StudentFlag.objects.exists())

    def test_get_is_not_allowed_and_signed_out_users_are_sent_to_login(self):
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(reverse("panic_alert")).status_code, 405)
        self.client.logout()
        r = self.client.post(reverse("panic_alert"))
        self.assertEqual(r.status_code, 302)
        self.assertFalse(StudentFlag.objects.exists())

    def test_pressing_too_fast_is_rate_limited(self):
        for _ in range(5):
            self.assertEqual(self.press().status_code, 200)
        self.assertEqual(self.press().status_code, 429)

    def test_the_button_and_tooltip_are_on_both_chats_of_the_student_dashboard(self):
        self.client.force_login(self.student)
        html = self.client.get(reverse("student_dashboard"), follow=True).content.decode()
        self.assertEqual(html.count('class="panic-btn"'), 2)       # Mica chat + Talk to Guidance
        self.assertIn('id="panic-tip"', html)
        self.assertIn("flagged in the Guidance Office", html)

    def test_the_dashboard_asks_for_confirmation_before_sending(self):
        self.client.force_login(self.student)
        html = self.client.get(reverse("student_dashboard"), follow=True).content.decode()
        self.assertIn('id="panic-confirm"', html)
        self.assertIn("Yes, send alert", html)
        self.assertIn("Send a panic alert?", html)
