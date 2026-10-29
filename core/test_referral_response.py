import json

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .models import StudentNote


class ReferralResponseTests(TestCase):
    """A referral to another staff member waits until that person accepts or declines it."""

    def setUp(self):
        self.student = User.objects.create_user("stud", password="Student-pass-1!")
        self.alice = User.objects.create_user("alice", password="Staff-pass-123!", is_staff=True, first_name="Alice")
        self.bob = User.objects.create_user("bob", password="Staff-pass-123!", is_staff=True, first_name="Bob")
        self.carol = User.objects.create_user("carol", password="Staff-pass-123!", is_staff=True, first_name="Carol")

    # -- helpers --
    def post(self, user, url, data):
        self.client.force_login(user)
        return self.client.post(url, json.dumps(data), content_type="application/json")

    def refer(self, author, to):
        r = self.post(author, reverse("staff_student_notes", args=[self.student.id]),
                      {"service": "referral", "topic": "Needs follow-up", "referred_to_id": to.id})
        self.assertEqual(r.status_code, 200, r.content)
        return StudentNote.objects.get(id=r.json()["note"]["id"])

    def respond(self, user, note, action, reason=""):
        return self.post(user, reverse("staff_referral_respond", args=[note.id]), {"action": action, "reason": reason})

    def inbox(self, user):
        self.client.force_login(user)
        return self.client.get(reverse("staff_referral_inbox")).json()

    # -- tests --
    def test_new_referral_is_pending_and_shows_in_the_inbox_to_answer(self):
        note = self.refer(self.alice, self.bob)
        self.assertEqual(note.referral_status, "pending")
        waiting = self.inbox(self.bob)["referrals"]
        self.assertEqual([r["note_id"] for r in waiting], [note.id])
        self.assertEqual(self.inbox(self.alice)["referrals"], [])

    def test_accepting_clears_it_from_the_inbox_and_tells_the_sender(self):
        note = self.refer(self.alice, self.bob)
        r = self.respond(self.bob, note, "accept")
        self.assertEqual(r.status_code, 200)
        note.refresh_from_db()
        self.assertEqual(note.referral_status, "accepted")
        self.assertIsNotNone(note.referral_responded_at)
        self.assertEqual(self.inbox(self.bob)["referrals"], [])
        answers = self.inbox(self.alice)["responses"]
        self.assertEqual([(a["note_id"], a["status"]) for a in answers], [(note.id, "accepted")])

    def test_declining_keeps_the_reason_and_removes_the_note_from_the_decliner(self):
        note = self.refer(self.alice, self.bob)
        self.respond(self.bob, note, "decline", "I'm on leave next week")
        note.refresh_from_db()
        self.assertEqual(note.referral_status, "declined")
        self.assertEqual(note.referral_decline_reason, "I'm on leave next week")
        notes_for_bob = self.client.get(reverse("staff_student_notes", args=[self.student.id]))
        self.assertEqual(notes_for_bob.json()["notes"], [])
        self.client.force_login(self.alice)
        mine = self.client.get(reverse("staff_student_notes", args=[self.student.id])).json()["notes"]
        self.assertEqual(mine[0]["referral_status"], "declined")
        self.assertEqual(mine[0]["referral_decline_reason"], "I'm on leave next week")

    def test_sender_stops_seeing_the_answer_once_they_open_the_student_notes(self):
        note = self.refer(self.alice, self.bob)
        self.respond(self.bob, note, "accept")
        self.client.force_login(self.alice)
        self.client.get(reverse("staff_student_notes", args=[self.student.id]))
        self.assertEqual(self.inbox(self.alice)["responses"], [])

    def test_only_the_person_it_is_addressed_to_can_answer(self):
        note = self.refer(self.alice, self.bob)
        self.assertEqual(self.respond(self.carol, note, "accept").status_code, 403)
        self.assertEqual(self.respond(self.alice, note, "accept").status_code, 403)
        note.refresh_from_db()
        self.assertEqual(note.referral_status, "pending")

    def test_a_referral_can_only_be_answered_once(self):
        note = self.refer(self.alice, self.bob)
        self.respond(self.bob, note, "accept")
        self.assertEqual(self.respond(self.bob, note, "decline").status_code, 409)
        note.refresh_from_db()
        self.assertEqual(note.referral_status, "accepted")

    def test_unknown_action_is_rejected(self):
        note = self.refer(self.alice, self.bob)
        self.assertEqual(self.respond(self.bob, note, "maybe").status_code, 400)

    def test_referring_to_someone_else_starts_a_fresh_pending_referral(self):
        note = self.refer(self.alice, self.bob)
        self.respond(self.bob, note, "decline")
        r = self.post(self.alice, reverse("staff_note_update", args=[note.id]),
                      {"service": "referral", "topic": "Needs follow-up", "referred_to_id": self.carol.id})
        self.assertEqual(r.status_code, 200, r.content)
        note.refresh_from_db()
        self.assertEqual((note.referred_to_id, note.referral_status, note.referral_decline_reason),
                         (self.carol.id, "pending", ""))
        self.assertEqual([x["note_id"] for x in self.inbox(self.carol)["referrals"]], [note.id])

    def test_editing_the_text_keeps_the_answer(self):
        note = self.refer(self.alice, self.bob)
        self.respond(self.bob, note, "accept")
        self.post(self.alice, reverse("staff_note_update", args=[note.id]),
                  {"service": "referral", "topic": "Updated topic", "referred_to_id": self.bob.id})
        note.refresh_from_db()
        self.assertEqual(note.referral_status, "accepted")
