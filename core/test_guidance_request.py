"""The TALK TO GUIDANCE button: one press alerts Guidance staff, with nothing typed."""
import json

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from .models import ConsoleNotification, GuidanceMessage, GuidanceThread, UserProfile
from .views import GUIDANCE_REQUEST_TEXT


class GuidanceRequestTests(TestCase):
    def setUp(self):
        cache.clear()
        self.student = User.objects.create_user("sam", password="Student-pass-1!", first_name="Sam", last_name="Reyes")
        UserProfile.objects.create(user=self.student)
        self.alice = User.objects.create_user("alice", password="Staff-pass-123!", is_staff=True)
        self.carol = User.objects.create_user("carol", password="Staff-pass-123!", is_staff=True)

    def press(self, user=None):
        self.client.force_login(user or self.student)
        return self.client.post(reverse("guidance_request"), json.dumps({}), content_type="application/json")

    def test_one_press_adds_a_message_and_notifies_staff(self):
        r = self.press()
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["created"])
        thread = GuidanceThread.objects.get(student=self.student)
        msg = thread.messages.get()
        self.assertEqual((msg.sender, msg.content), (self.student, GUIDANCE_REQUEST_TEXT))
        for staff in (self.alice, self.carol):
            note = ConsoleNotification.objects.get(recipient=staff, kind="message")
            self.assertEqual(note.ref_id, thread.id)
            self.assertTrue(note.urgent)
            self.assertNotIn("Sam", note.text)          # unclaimed: the student isn't named

    def test_a_claimed_thread_only_notifies_its_counselor_by_name(self):
        GuidanceThread.objects.create(student=self.student, counselor=self.alice)
        self.press()
        note = ConsoleNotification.objects.get(recipient=self.alice, kind="message")
        self.assertIn("Sam Reyes", note.text)
        self.assertFalse(ConsoleNotification.objects.filter(recipient=self.carol).exists())

    def test_the_request_shows_up_as_waiting_in_the_staff_inbox(self):
        self.press()
        self.client.force_login(self.alice)
        threads = self.client.get(reverse("staff_guidance_inbox")).json()["threads"]
        self.assertEqual(len(threads), 1)
        self.assertTrue(threads[0]["awaiting_reply"])
        self.assertEqual(threads[0]["last_message"], GUIDANCE_REQUEST_TEXT)

    def test_pressing_again_while_waiting_does_not_pile_up_messages_or_notifications(self):
        self.press()
        r = self.press()
        self.assertEqual(r.status_code, 200)
        self.assertFalse(r.json()["created"])
        self.assertEqual(GuidanceMessage.objects.count(), 1)
        self.assertEqual(ConsoleNotification.objects.filter(recipient=self.alice, kind="message").count(), 1)

    def test_pressing_after_a_staff_reply_asks_again(self):
        self.press()
        thread = GuidanceThread.objects.get(student=self.student)
        GuidanceMessage.objects.create(thread=thread, sender=self.alice, content="Hi Sam, how can I help?")
        r = self.press()
        self.assertTrue(r.json()["created"])
        self.assertEqual(thread.messages.count(), 3)

    def test_unverified_students_cannot_use_it(self):
        ugo = User.objects.create_user("ugo", password="Student-pass-1!")
        UserProfile.objects.create(user=ugo, verification_status="unverified")
        r = self.press(ugo)
        self.assertEqual(r.status_code, 403)
        self.assertTrue(r.json()["unverified"])
        self.assertFalse(GuidanceMessage.objects.exists())
        self.assertFalse(ConsoleNotification.objects.exists())

    def test_post_and_login_are_required(self):
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(reverse("guidance_request")).status_code, 405)
        self.client.logout()
        self.assertEqual(self.client.post(reverse("guidance_request")).status_code, 302)
