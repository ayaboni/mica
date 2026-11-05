import json
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import (Appointment, GuidanceMessage, GuidanceThread, StaffProfile, StaffSubclass,
                     StudentNote, UserProfile)


class ConversationPrivacyTests(TestCase):
    """Staff only see and open the conversations assigned to them (plus unclaimed ones waiting to be claimed)."""

    def setUp(self):
        self.student = User.objects.create_user("stud", password="Student-pass-1!", first_name="Sam")
        self.alice = User.objects.create_user("alice", password="Staff-pass-123!", is_staff=True)
        self.carol = User.objects.create_user("carol", password="Staff-pass-123!", is_staff=True)
        self.admin = User.objects.create_superuser("admin", password="Admin-pass-123!")
        self.thread = GuidanceThread.objects.create(student=self.student, counselor=self.alice)
        GuidanceMessage.objects.create(thread=self.thread, sender=self.student, content="private worry")

    def get(self, user, url):
        self.client.force_login(user)
        return self.client.get(url)

    def post(self, user, url, data=None):
        self.client.force_login(user)
        return self.client.post(url, json.dumps(data or {}), content_type="application/json")

    def test_other_staff_do_not_see_an_assigned_conversation(self):
        ids = [t["id"] for t in self.get(self.carol, reverse("staff_guidance_inbox")).json()["threads"]]
        self.assertNotIn(self.thread.id, ids)
        ids = [t["id"] for t in self.get(self.alice, reverse("staff_guidance_inbox")).json()["threads"]]
        self.assertIn(self.thread.id, ids)

    def test_other_staff_cannot_open_read_write_or_take_over_an_assigned_conversation(self):
        self.assertEqual(self.get(self.carol, reverse("staff_guidance_thread", args=[self.thread.id])).status_code, 403)
        self.assertEqual(self.post(self.carol, reverse("staff_guidance_send", args=[self.thread.id]),
                                   {"message": "hi"}).status_code, 403)
        self.assertEqual(self.post(self.carol, reverse("staff_guidance_note", args=[self.thread.id]),
                                   {"note": "x"}).status_code, 403)
        r = self.post(self.carol, reverse("staff_guidance_claim", args=[self.thread.id]), {"takeover": True})
        self.assertEqual(r.status_code, 403)
        r = self.post(self.carol, reverse("staff_student_thread", args=[self.student.id]))
        self.assertEqual(r.status_code, 409)
        self.thread.refresh_from_db()
        self.assertEqual(self.thread.counselor_id, self.alice.id)

    def test_unclaimed_conversations_stay_visible_so_someone_can_claim_them(self):
        other = User.objects.create_user("stud2", password="Student-pass-1!")
        free = GuidanceThread.objects.create(student=other)
        ids = [t["id"] for t in self.get(self.carol, reverse("staff_guidance_inbox")).json()["threads"]]
        self.assertEqual(ids, [free.id])
        self.assertEqual(self.get(self.carol, reverse("staff_guidance_thread", args=[free.id])).status_code, 200)

    def test_the_system_admin_can_still_oversee(self):
        ids = [t["id"] for t in self.get(self.admin, reverse("staff_guidance_inbox")).json()["threads"]]
        self.assertIn(self.thread.id, ids)
        r = self.post(self.admin, reverse("staff_guidance_claim", args=[self.thread.id]), {"takeover": True})
        self.assertEqual(r.status_code, 200)


class ReferralHandoverTests(TestCase):
    """staff 1 refers -> staff 2 accepts or declines -> accepting hands the conversation over for good."""

    def setUp(self):
        self.student = User.objects.create_user("stud", password="Student-pass-1!", first_name="Sam", last_name="Student")
        self.other_student = User.objects.create_user("stud2", password="Student-pass-1!")
        self.alice = User.objects.create_user("alice", password="Staff-pass-123!", is_staff=True, first_name="Alice")
        self.bob = User.objects.create_user("bob", password="Staff-pass-123!", is_staff=True, first_name="Bob")
        self.carol = User.objects.create_user("carol", password="Staff-pass-123!", is_staff=True, first_name="Carol")
        # Bob's role may not chat with students or take cases at all.
        self.limited = StaffSubclass.objects.create(name="Records only", chat_students=False, take_cases=False,
                                                    view_students=True)
        StaffProfile.objects.create(user=self.bob, role="other", subclass=self.limited)

    # -- helpers --
    def post(self, user, url, data=None):
        self.client.force_login(user)
        return self.client.post(url, json.dumps(data or {}), content_type="application/json")

    def get(self, user, url):
        self.client.force_login(user)
        return self.client.get(url)

    def refer(self, author=None, to=None):
        author, to = author or self.alice, to or self.bob
        r = self.post(author, reverse("staff_student_notes", args=[self.student.id]),
                      {"service": "referral", "topic": "Needs follow-up", "realizations": "Struggling with sleep",
                       "referred_to_id": to.id, "student_informed": True})
        self.assertEqual(r.status_code, 200, r.content)
        return StudentNote.objects.get(id=r.json()["note"]["id"])

    def answer(self, note, action, user=None, reason=""):
        r = self.post(user or self.bob, reverse("staff_referral_respond", args=[note.id]),
                      {"action": action, "reason": reason})
        note.refresh_from_db()
        return r

    def accept(self, note, user=None):
        r = self.answer(note, "accept", user)
        self.assertEqual(r.status_code, 200, r.content)
        return note

    def start_chat(self, note, user=None):
        return self.post(user or self.bob, reverse("staff_referral_start_chat", args=[note.id]))

    def alice_thread(self):
        thread = GuidanceThread.objects.create(student=self.student, counselor=self.alice)
        GuidanceMessage.objects.create(thread=thread, sender=self.student, content="private worry")
        GuidanceMessage.objects.create(thread=thread, sender=self.alice, content="I hear you")
        return thread

    # -- step 2: staff 2 is notified, with the notes --
    def test_referred_staff_gets_the_notes_with_the_referral(self):
        note = self.refer()
        item = self.get(self.bob, reverse("staff_referral_inbox")).json()["referrals"][0]
        self.assertEqual(item["note_id"], note.id)
        self.assertEqual(item["topic"], "Needs follow-up")
        self.assertEqual(item["realizations"], "Struggling with sleep")
        self.assertEqual(item["from_name"], "Alice")
        self.assertTrue(item["student_informed"])

    # -- step 4: accepting gives permission to open the conversation --
    def test_no_access_until_the_referral_is_accepted(self):
        note = self.refer()
        self.assertEqual(self.post(self.bob, reverse("staff_student_thread", args=[self.student.id])).status_code, 403)
        self.assertEqual(self.start_chat(note).status_code, 409)
        self.assertEqual(self.get(self.bob, reverse("staff_guidance_inbox")).status_code, 403)

    def test_accepting_lets_a_role_without_chat_claim_the_student_and_write(self):
        note = self.accept(self.refer())
        r = self.start_chat(note)
        self.assertEqual(r.status_code, 200, r.content)
        thread = GuidanceThread.objects.get(student=self.student)
        self.assertEqual(thread.counselor_id, self.bob.id)
        sent = self.post(self.bob, reverse("staff_guidance_send", args=[thread.id]), {"message": "Hi Sam"})
        self.assertEqual(sent.status_code, 200, sent.content)
        self.assertTrue(GuidanceMessage.objects.filter(thread=thread, sender=self.bob, content="Hi Sam").exists())
        threads = self.get(self.bob, reverse("staff_guidance_inbox")).json()["threads"]
        self.assertEqual([t["student_id"] for t in threads], [self.student.id])

    def test_accepted_referral_shows_a_start_chat_entry_and_chat_access(self):
        note = self.accept(self.refer())
        data = self.get(self.bob, reverse("staff_referral_inbox")).json()
        self.assertTrue(data["chat_access"])
        self.assertEqual([c["note_id"] for c in data["chats"]], [note.id])
        self.start_chat(note)
        data = self.get(self.bob, reverse("staff_referral_inbox")).json()
        self.assertEqual(data["chats"], [])           # it's in their conversations now

    def test_access_is_only_for_the_referred_student(self):
        self.accept(self.refer())
        self.assertEqual(self.post(self.bob, reverse("staff_student_thread", args=[self.other_student.id])).status_code, 403)
        self.assertEqual(self.post(self.bob, reverse("staff_student_thread", args=[self.student.id])).status_code, 200)

    def test_only_the_referred_staff_member_can_start_the_chat(self):
        note = self.accept(self.refer())
        self.assertEqual(self.start_chat(note, self.carol).status_code, 403)

    def test_archived_student_cannot_be_chatted_with(self):
        UserProfile.objects.update_or_create(user=self.student, defaults={"account_status": "inactive"})
        note = self.accept(self.refer())
        self.assertEqual(self.start_chat(note).status_code, 409)

    # -- the hand-over: accepting moves the conversation away from staff 1 --
    def test_waiting_for_an_answer_staff_1_keeps_the_conversation(self):
        thread = self.alice_thread()
        self.refer()
        thread.refresh_from_db()
        self.assertEqual(thread.counselor_id, self.alice.id)
        self.assertEqual(self.get(self.alice, reverse("staff_guidance_thread", args=[thread.id])).status_code, 200)
        self.assertEqual(self.get(self.bob, reverse("staff_guidance_thread", args=[thread.id])).status_code, 403)

    def test_accepting_hands_the_conversation_over_for_good(self):
        thread = self.alice_thread()
        appt = Appointment.objects.create(thread=thread, student=self.student, counselor=self.alice,
                                          scheduled_time=timezone.now() + timedelta(days=2), status="confirmed")
        note = self.accept(self.refer())
        thread.refresh_from_db(); appt.refresh_from_db()
        self.assertEqual(thread.counselor_id, self.bob.id)
        self.assertEqual(appt.counselor_id, self.bob.id)

        # Staff 1 is out completely.
        self.assertEqual(self.get(self.alice, reverse("staff_guidance_thread", args=[thread.id])).status_code, 403)
        self.assertEqual(self.post(self.alice, reverse("staff_guidance_send", args=[thread.id]),
                                   {"message": "still here?"}).status_code, 403)
        ids = [t["id"] for t in self.get(self.alice, reverse("staff_guidance_inbox")).json()["threads"]]
        self.assertNotIn(thread.id, ids)
        self.assertEqual(self.post(self.alice, reverse("staff_guidance_claim", args=[thread.id]),
                                   {"takeover": True}).status_code, 403)

        # Staff 2 owns it with a fresh chat (no history), even though their role can't chat.
        view = self.get(self.bob, reverse("staff_guidance_thread", args=[thread.id]))
        self.assertEqual(view.status_code, 200, view.content)
        self.assertEqual(len(view.json()["messages"]), 0)
        self.assertEqual(self.post(self.bob, reverse("staff_guidance_send", args=[thread.id]),
                                   {"message": "Hello, I'm Bob"}).status_code, 200)
        view = self.get(self.bob, reverse("staff_guidance_thread", args=[thread.id]))
        self.assertEqual([m["content"] for m in view.json()["messages"]], ["Hello, I'm Bob"])

    def test_chat_history_does_not_carry_over_to_the_new_staff_member(self):
        thread = self.alice_thread()
        self.accept(self.refer())
        # Bob's inbox preview doesn't leak the old messages either.
        row = [t for t in self.get(self.bob, reverse("staff_guidance_inbox")).json()["threads"] if t["id"] == thread.id][0]
        self.assertEqual(row["last_message"], "")
        self.assertFalse(row["awaiting_reply"])
        # The student's screen starts fresh too, and what the old messages said stays on record.
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(reverse("guidance_messages")).json()["messages"], [])
        self.assertEqual(thread.messages.count(), 2)
        # A new message from the student is the first thing Bob sees.
        GuidanceMessage.objects.create(thread=thread, sender=self.student, content="hi Bob")
        view = self.get(self.bob, reverse("staff_guidance_thread", args=[thread.id]))
        self.assertEqual([m["content"] for m in view.json()["messages"]], ["hi Bob"])

    def test_pressing_talk_to_guidance_after_a_handover_alerts_the_new_staff_member(self):
        thread = self.alice_thread()           # the student's last message is already "private worry"-era history
        self.accept(self.refer())
        self.client.force_login(self.student)
        r = self.client.post(reverse("guidance_request"), "{}", content_type="application/json")
        self.assertEqual(r.json()["created"], True)
        view = self.get(self.bob, reverse("staff_guidance_thread", args=[thread.id]))
        self.assertEqual(len(view.json()["messages"]), 1)

    def test_start_chat_for_an_older_accepted_referral_also_starts_fresh(self):
        thread = self.alice_thread()
        note = self.accept(self.refer())
        # Simulate a referral accepted before handovers were automatic: Alice still holds the thread.
        thread.counselor = self.alice; thread.history_start_id = 0; thread.save()
        self.assertEqual(self.start_chat(note).status_code, 200)
        view = self.get(self.bob, reverse("staff_guidance_thread", args=[thread.id]))
        self.assertEqual(view.json()["messages"], [])

    def test_the_system_admin_still_sees_the_full_history(self):
        thread = self.alice_thread()
        self.accept(self.refer())
        admin = User.objects.create_superuser("root", password="Admin-pass-123!")
        view = self.get(admin, reverse("staff_guidance_thread", args=[thread.id]))
        self.assertEqual(len(view.json()["messages"]), 2)

    def test_accepting_does_not_take_a_conversation_from_a_third_counselor(self):
        thread = GuidanceThread.objects.create(student=self.student, counselor=self.carol)
        note = self.accept(self.refer())
        thread.refresh_from_db()
        self.assertEqual(thread.counselor_id, self.carol.id)
        self.assertEqual(self.start_chat(note).status_code, 409)
        self.assertEqual(self.get(self.bob, reverse("staff_guidance_thread", args=[thread.id])).status_code, 403)

    def test_unclaimed_conversation_is_claimed_with_start_chat_not_automatically(self):
        thread = GuidanceThread.objects.create(student=self.student)
        note = self.accept(self.refer())
        thread.refresh_from_db()
        self.assertIsNone(thread.counselor_id)
        self.assertEqual(self.start_chat(note).status_code, 200)
        thread.refresh_from_db()
        self.assertEqual(thread.counselor_id, self.bob.id)

    def test_start_chat_finishes_the_handover_for_an_older_accepted_referral(self):
        thread = self.alice_thread()
        note = self.accept(self.refer())
        GuidanceThread.objects.filter(id=thread.id).update(counselor=self.alice)    # as it was before automatic handovers
        self.assertTrue(self.get(self.bob, reverse("staff_referral_inbox")).json()["chat_access"])
        self.assertEqual(self.start_chat(note).status_code, 200)
        thread.refresh_from_db()
        self.assertEqual(thread.counselor_id, self.bob.id)

    # -- step 5: declined means it goes back to staff 1 --
    def test_declining_leaves_everything_with_staff_1(self):
        thread = self.alice_thread()
        note = self.refer()
        r = self.answer(note, "decline", reason="At capacity")
        self.assertEqual(r.status_code, 200)
        thread.refresh_from_db()
        self.assertEqual(thread.counselor_id, self.alice.id)
        self.assertEqual(self.get(self.alice, reverse("staff_guidance_thread", args=[thread.id])).status_code, 200)
        self.assertEqual(self.start_chat(note).status_code, 409)
        self.assertEqual(self.post(self.bob, reverse("staff_student_thread", args=[self.student.id])).status_code, 403)
        # Staff 1 hears about it, with the reason.
        responses = self.get(self.alice, reverse("staff_referral_inbox")).json()["responses"]
        self.assertEqual([(x["status"], x["reason"]) for x in responses], [("declined", "At capacity")])

    def test_staff_1_can_refer_again_after_a_decline(self):
        thread = self.alice_thread()
        note = self.refer()
        self.answer(note, "decline")
        r = self.post(self.alice, reverse("staff_note_update", args=[note.id]),
                      {"service": "referral", "topic": "Needs follow-up", "referred_to_id": self.carol.id})
        self.assertEqual(r.status_code, 200, r.content)
        note.refresh_from_db()
        self.assertEqual((note.referred_to_id, note.referral_status), (self.carol.id, "pending"))
        self.assertEqual(self.answer(note, "accept", self.carol).status_code, 200)
        thread.refresh_from_db()
        self.assertEqual(thread.counselor_id, self.carol.id)

    def test_cannot_answer_a_referral_twice(self):
        note = self.accept(self.refer())
        self.assertEqual(self.answer(note, "decline").status_code, 409)


class ReferralPermissionTests(TestCase):
    """Admins switch referrals on or off per role under Roles & Permissions."""

    def setUp(self):
        self.student = User.objects.create_user("stud", password="Student-pass-1!", first_name="Sam")
        self.alice = User.objects.create_user("alice", password="Staff-pass-123!", is_staff=True, first_name="Alice")
        self.bob = User.objects.create_user("bob", password="Staff-pass-123!", is_staff=True, first_name="Bob")
        self.no_ref = StaffSubclass.objects.create(name="No referrals", referrals=False)
        self.with_ref = StaffSubclass.objects.create(name="With referrals", referrals=True)
        StaffProfile.objects.create(user=self.alice, role="other", subclass=self.with_ref)
        StaffProfile.objects.create(user=self.bob, role="other", subclass=self.with_ref)

    def post(self, user, url, data=None):
        self.client.force_login(user)
        return self.client.post(url, json.dumps(data or {}), content_type="application/json")

    def refer(self, author, to):
        return self.post(author, reverse("staff_student_notes", args=[self.student.id]),
                         {"service": "referral", "topic": "Follow up", "referred_to_id": to.id})

    def set_role(self, user, role):
        StaffProfile.objects.filter(user=user).update(subclass=role)

    def test_new_switch_is_on_for_existing_style_roles_and_listed_for_the_admin(self):
        self.assertTrue(StaffSubclass.objects.create(name="Plain").referrals)
        admin = User.objects.create_superuser("boss", "b@example.com", "Pw-12345")
        self.client.force_login(admin)
        keys = [f["key"] for f in self.client.get(reverse("admin_subclasses")).json()["fields"]]
        self.assertIn("referrals", keys)

    def test_admin_can_toggle_it_on_a_role(self):
        admin = User.objects.create_superuser("boss", "b@example.com", "Pw-12345")
        r = self.post(admin, reverse("admin_subclass_update", args=[self.with_ref.id]),
                      {"name": "With referrals", "referrals": False})
        self.assertEqual(r.status_code, 200, r.content)
        self.with_ref.refresh_from_db()
        self.assertFalse(self.with_ref.referrals)

    def test_role_without_it_cannot_make_a_referral(self):
        self.set_role(self.alice, self.no_ref)
        self.assertEqual(self.refer(self.alice, self.bob).status_code, 403)
        self.assertFalse(StudentNote.objects.filter(service="referral").exists())

    def test_cannot_refer_to_a_role_that_cannot_receive_referrals(self):
        self.set_role(self.bob, self.no_ref)
        self.assertEqual(self.refer(self.alice, self.bob).status_code, 400)

    def test_referral_picker_marks_who_can_receive(self):
        self.set_role(self.bob, self.no_ref)
        self.client.force_login(self.alice)
        members = {m["id"]: m for m in self.client.get(reverse("staff_care_team")).json()["members"]}
        self.assertFalse(members[self.bob.id]["can_receive_referrals"])
        self.assertTrue(members[self.alice.id]["can_receive_referrals"])

    def test_role_switched_off_later_is_not_asked_and_cannot_answer(self):
        note_id = self.refer(self.alice, self.bob).json()["note"]["id"]
        self.set_role(self.bob, self.no_ref)
        self.client.force_login(self.bob)
        self.assertEqual(self.client.get(reverse("staff_referral_inbox")).json()["referrals"], [])
        r = self.post(self.bob, reverse("staff_referral_respond", args=[note_id]), {"action": "accept"})
        self.assertEqual(r.status_code, 403)

    def test_switching_it_off_removes_chat_access_from_an_accepted_referral(self):
        note_id = self.refer(self.alice, self.bob).json()["note"]["id"]
        self.post(self.bob, reverse("staff_referral_respond", args=[note_id]), {"action": "accept"})
        self.set_role(self.bob, self.no_ref)
        r = self.post(self.bob, reverse("staff_referral_start_chat", args=[note_id]))
        self.assertEqual(r.status_code, 403)
