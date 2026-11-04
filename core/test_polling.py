"""Chat and notification polling: incremental fetches (?after=), the combined staff pulse, and that none
of it loosens who can read what or skips the audit trail."""
import json

from django.contrib.auth.models import User
from django.db import connection
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from django.urls import reverse

from .models import AccessLog, GuidanceMessage, GuidanceThread, StaffProfile, StaffSubclass, UserProfile


class PollingTestBase(TestCase):
    def setUp(self):
        self.sam = User.objects.create_user("sam", password="Student-pass-1!", first_name="Sam")
        UserProfile.objects.create(user=self.sam)
        self.tia = User.objects.create_user("tia", password="Student-pass-1!", first_name="Tia")
        UserProfile.objects.create(user=self.tia)
        self.alice = User.objects.create_user("alice", password="Staff-pass-123!", is_staff=True)
        self.carol = User.objects.create_user("carol", password="Staff-pass-123!", is_staff=True)
        self.admin = User.objects.create_superuser("admin", password="Admin-pass-123!")
        self.thread = GuidanceThread.objects.create(student=self.sam, counselor=self.alice)
        self.m1 = GuidanceMessage.objects.create(thread=self.thread, sender=self.sam, content="first")
        self.m2 = GuidanceMessage.objects.create(thread=self.thread, sender=self.alice, content="second")
        self.m3 = GuidanceMessage.objects.create(thread=self.thread, sender=self.sam, content="third")

    def get(self, user, url, **params):
        self.client.force_login(user)
        return self.client.get(url, params)

    def post(self, user, url, data=None):
        self.client.force_login(user)
        return self.client.post(url, json.dumps(data or {}), content_type="application/json")


class StudentGuidancePollTests(PollingTestBase):
    def ids(self, response):
        return [m["id"] for m in response.json()["messages"]]

    def test_full_load_has_ids_and_the_last_id(self):
        r = self.get(self.sam, reverse("guidance_messages"))
        self.assertEqual(self.ids(r), [self.m1.id, self.m2.id, self.m3.id])
        self.assertEqual(r.json()["last_id"], self.m3.id)

    def test_after_returns_only_newer_messages(self):
        r = self.get(self.sam, reverse("guidance_messages"), after=self.m1.id)
        self.assertEqual(self.ids(r), [self.m2.id, self.m3.id])
        r = self.get(self.sam, reverse("guidance_messages"), after=self.m3.id)
        self.assertEqual(self.ids(r), [])
        self.assertEqual(r.json()["last_id"], self.m3.id)        # nothing new: the pointer stays put

    def test_a_junk_after_value_means_everything_never_an_error(self):
        for junk in ("abc", "-1", "\u00b2", "1" * 20, "1.5", ""):
            r = self.get(self.sam, reverse("guidance_messages"), after=junk)
            self.assertEqual(r.status_code, 200, junk)
            self.assertEqual(len(self.ids(r)), 3, junk)

    def test_messages_cleared_from_the_screen_stay_cleared(self):
        self.assertEqual(self.post(self.sam, reverse("guidance_hide"), {"ids": [self.m1.id]}).json()["hidden"], 1)
        r = self.get(self.sam, reverse("guidance_messages"))
        self.assertEqual(self.ids(r), [self.m2.id, self.m3.id])
        # ...but the Guidance Office keeps its record
        t = self.get(self.alice, reverse("staff_guidance_thread", args=[self.thread.id])).json()
        self.assertIn(self.m1.id, [m["id"] for m in t["messages"]])

    def test_a_student_only_ever_sees_their_own_conversation(self):
        other = GuidanceThread.objects.create(student=self.tia)
        mine = GuidanceMessage.objects.create(thread=other, sender=self.tia, content="tia's own")
        r = self.get(self.tia, reverse("guidance_messages"), after=0)
        self.assertEqual(self.ids(r), [mine.id])
        r = self.get(self.tia, reverse("guidance_messages"), after=self.m1.id - 1)   # asking from before sam's messages
        self.assertEqual(self.ids(r), [mine.id])

    def test_no_conversation_yet(self):
        r = self.get(self.tia, reverse("guidance_messages"), after=5)
        self.assertEqual(r.json(), {"messages": [], "counselor_name": None, "last_id": 0})


class StaffThreadPollTests(PollingTestBase):
    def thread_url(self):
        return reverse("staff_guidance_thread", args=[self.thread.id])

    def test_first_load_is_the_whole_conversation(self):
        d = self.get(self.alice, self.thread_url()).json()
        self.assertEqual([m["id"] for m in d["messages"]], [self.m1.id, self.m2.id, self.m3.id])
        self.assertEqual(d["last_id"], self.m3.id)
        self.assertIn("notes", d)
        self.assertIn("student_info", d)
        self.assertNotIn("incremental", d)

    def test_after_returns_only_new_messages_and_skips_the_heavy_parts(self):
        d = self.get(self.alice, self.thread_url(), after=self.m1.id).json()
        self.assertTrue(d["incremental"])
        self.assertEqual([m["id"] for m in d["messages"]], [self.m2.id, self.m3.id])
        self.assertEqual([m["sender"] for m in d["messages"]], ["counselor", "student"])
        self.assertEqual(d["last_id"], self.m3.id)
        self.assertNotIn("notes", d)
        self.assertNotIn("student_info", d)

    def test_a_new_message_shows_up_on_the_next_incremental_poll(self):
        m4 = GuidanceMessage.objects.create(thread=self.thread, sender=self.sam, content="new one")
        d = self.get(self.alice, self.thread_url(), after=self.m3.id).json()
        self.assertEqual([m["id"] for m in d["messages"]], [m4.id])
        self.assertEqual(d["last_id"], m4.id)

    def test_an_incremental_poll_is_still_audit_logged(self):
        self.assertEqual(AccessLog.objects.filter(staff=self.alice, student=self.sam).count(), 0)
        self.get(self.alice, self.thread_url(), after=self.m3.id)
        self.assertEqual(AccessLog.objects.filter(staff=self.alice, student=self.sam, action="open_chat").count(), 1)

    def test_other_staff_are_refused_on_incremental_polls_too(self):
        self.assertEqual(self.get(self.carol, self.thread_url(), after=0).status_code, 403)
        self.assertEqual(self.get(self.carol, self.thread_url(), after=self.m1.id).status_code, 403)
        self.assertEqual(AccessLog.objects.filter(staff=self.carol).count(), 0)

    def test_students_cannot_use_the_staff_endpoint(self):
        r = self.get(self.sam, self.thread_url(), after=self.m1.id)
        self.assertEqual(r.status_code, 302)                      # bounced to the login page


class InboxAndPulseTests(PollingTestBase):
    def test_inbox_summary_still_reports_the_latest_message(self):
        t = next(t for t in self.get(self.alice, reverse("staff_guidance_inbox")).json()["threads"]
                 if t["id"] == self.thread.id)
        self.assertEqual(t["last_message"], "third")
        self.assertEqual(t["last_message_id"], self.m3.id)
        self.assertTrue(t["awaiting_reply"])                      # the student spoke last
        GuidanceMessage.objects.create(thread=self.thread, sender=self.alice, content="reply")
        t = next(t for t in self.get(self.alice, reverse("staff_guidance_inbox")).json()["threads"]
                 if t["id"] == self.thread.id)
        self.assertEqual(t["last_message"], "reply")
        self.assertFalse(t["awaiting_reply"])

    def test_a_thread_with_no_messages_is_listed_without_error(self):
        empty = GuidanceThread.objects.create(student=self.tia)
        t = next(t for t in self.get(self.alice, reverse("staff_guidance_inbox")).json()["threads"] if t["id"] == empty.id)
        self.assertEqual((t["last_message"], t["last_message_id"], t["awaiting_reply"]), ("", 0, False))

    def test_the_latest_message_is_not_fetched_one_thread_at_a_time(self):
        for i in range(6):
            s = User.objects.create_user(f"extra{i}", password="Student-pass-1!")
            UserProfile.objects.create(user=s)
            t = GuidanceThread.objects.create(student=s)
            GuidanceMessage.objects.create(thread=t, sender=s, content="hi")
        self.client.force_login(self.alice)
        with CaptureQueriesContext(connection) as ctx:
            threads = self.client.get(reverse("staff_guidance_inbox")).json()["threads"]
        self.assertEqual(len(threads), 7)
        on_messages = [q for q in ctx.captured_queries if "guidancemessage" in q["sql"].lower()]
        self.assertEqual(len(on_messages), 1)                     # one query, however many conversations

    def test_pulse_returns_exactly_what_the_two_endpoints_return(self):
        pulse = self.get(self.alice, reverse("staff_pulse")).json()
        self.assertEqual(pulse["inbox"], self.get(self.alice, reverse("staff_guidance_inbox")).json())
        self.assertEqual(pulse["referrals"], self.get(self.alice, reverse("staff_referral_inbox")).json())

    def test_pulse_applies_the_same_privacy_rules_as_the_inbox(self):
        ids = [t["id"] for t in self.get(self.carol, reverse("staff_pulse")).json()["inbox"]["threads"]]
        self.assertNotIn(self.thread.id, ids)                     # alice's conversation is not carol's to see
        ids = [t["id"] for t in self.get(self.admin, reverse("staff_pulse")).json()["inbox"]["threads"]]
        self.assertIn(self.thread.id, ids)                        # the system admin can still oversee

    def test_pulse_has_no_inbox_for_a_role_that_cannot_chat_and_holds_no_referral(self):
        bob = User.objects.create_user("bob", password="Staff-pass-123!", is_staff=True)
        limited = StaffSubclass.objects.create(name="Records only", chat_students=False, take_cases=False,
                                               view_students=True)
        StaffProfile.objects.create(user=bob, role="other", subclass=limited)
        self.assertEqual(self.get(bob, reverse("staff_guidance_inbox")).status_code, 403)
        pulse = self.get(bob, reverse("staff_pulse"))
        self.assertEqual(pulse.status_code, 200)
        self.assertIsNone(pulse.json()["inbox"])
        self.assertIn("referrals", pulse.json())

    def test_pulse_is_staff_only(self):
        self.client.logout()
        self.assertEqual(self.client.get(reverse("staff_pulse")).status_code, 302)
        self.assertEqual(self.get(self.sam, reverse("staff_pulse")).status_code, 302)

    def test_pulse_is_a_get_under_api_so_it_never_keeps_a_session_alive(self):
        # IdleTimeoutMiddleware only ignores GET requests under /api/ when measuring inactivity.
        self.assertTrue(reverse("staff_pulse").startswith("/api/"))
        self.client.force_login(self.alice)
        self.assertEqual(self.client.post(reverse("staff_pulse")).status_code, 405)    # GET only
