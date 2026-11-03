import json
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Appointment, GuidanceThread, StudentFlag, StudentStatusLog, UserProfile


class SelfDeactivationTests(TestCase):
    PASSWORD = "Sturdy-pass-9271!"

    def setUp(self):
        # No e-mail on the account, so the password alone signs in (no e-mailed code step).
        self.student = User.objects.create_user("juan", "", self.PASSWORD)
        self.profile = UserProfile.objects.create(user=self.student)
        self.admin = User.objects.create_superuser("boss", "boss@example.com", "Admin-pass-123!")

    def refresh(self):
        self.profile.refresh_from_db()
        return self.profile

    def deactivate(self, **extra):
        data = {"password": self.PASSWORD}
        data.update(extra)
        return self.client.post(reverse("account_deactivate"), data)

    def sign_in(self):
        return self.client.post(reverse("login"), {"username": "juan", "password": self.PASSWORD})

    def staff_set_status(self, status, reason=""):
        other = self.client_class()
        other.force_login(self.admin)
        return other.post(
            reverse("staff_student_status", args=[self.student.id]),
            data=json.dumps({"status": status, "reason": reason, "force": True}),
            content_type="application/json")

    # ---- deactivating ----

    def test_student_can_deactivate_with_password(self):
        self.client.force_login(self.student)
        resp = self.deactivate()
        self.assertRedirects(resp, reverse("login"), fetch_redirect_response=False)
        p = self.refresh()
        self.assertEqual((p.account_status, p.status_reason, p.self_deactivated),
                         ("inactive", "self_deactivated", True))
        self.assertNotIn("_auth_user_id", self.client.session)      # signed out
        log = StudentStatusLog.objects.get()
        self.assertEqual((log.old_status, log.new_status, log.reason, log.changed_by),
                         ("active", "inactive", "self_deactivated", self.student))

    def test_wrong_password_does_not_deactivate(self):
        self.client.force_login(self.student)
        self.deactivate(password="wrong")
        self.assertEqual(self.refresh().account_status, "active")

    def test_google_only_account_must_type_the_word(self):
        self.student.set_unusable_password()
        self.student.save()
        self.client.force_login(self.student)
        self.client.post(reverse("account_deactivate"), {"confirm_text": "nope"})
        self.assertEqual(self.refresh().account_status, "active")
        self.client.post(reverse("account_deactivate"), {"confirm_text": "deactivate"})
        self.assertTrue(self.refresh().self_deactivated)

    def test_get_is_not_allowed(self):
        self.client.force_login(self.student)
        self.assertEqual(self.client.get(reverse("account_deactivate")).status_code, 405)
        self.assertEqual(self.refresh().account_status, "active")

    def test_open_risk_alert_blocks_deactivation(self):
        StudentFlag.objects.create(student=self.student, reason="needs follow-up", status="new")
        self.client.force_login(self.student)
        self.deactivate()
        self.assertEqual(self.refresh().account_status, "active")
        self.assertEqual(StudentStatusLog.objects.count(), 0)

    def test_resolved_alert_does_not_block(self):
        StudentFlag.objects.create(student=self.student, reason="old", status="resolved")
        self.client.force_login(self.student)
        self.deactivate()
        self.assertEqual(self.refresh().account_status, "inactive")

    def test_upcoming_appointments_are_cancelled(self):
        thread = GuidanceThread.objects.create(student=self.student, counselor=self.admin)
        appt = Appointment.objects.create(
            thread=thread, student=self.student, counselor=self.admin,
            scheduled_time=timezone.now() + timedelta(days=2), status="confirmed")
        self.client.force_login(self.student)
        self.deactivate()
        appt.refresh_from_db()
        self.assertEqual(appt.status, "cancelled")

    def test_graduated_student_cannot_use_it(self):
        UserProfile.objects.filter(pk=self.profile.pk).update(account_status="graduated")
        self.client.force_login(self.student)
        self.deactivate()
        p = self.refresh()
        self.assertEqual((p.account_status, p.self_deactivated), ("graduated", False))

    # ---- signing back in ----

    def test_signing_in_again_reactivates(self):
        self.client.force_login(self.student)
        self.deactivate()
        resp = self.sign_in()
        self.assertEqual(resp.status_code, 302)
        p = self.refresh()
        self.assertEqual((p.account_status, p.status_reason, p.self_deactivated),
                         ("active", "", False))
        self.assertEqual(StudentStatusLog.objects.count(), 2)
        self.assertEqual(StudentStatusLog.objects.first().new_status, "active")
        # and they can really use the site again
        resp = self.client.get(reverse("student_dashboard"))
        self.assertNotIn("archived", resp.get("Location", ""))

    # ---- staff archive is never bypassed ----

    def test_staff_archived_student_cannot_sign_back_in(self):
        self.assertEqual(self.staff_set_status("inactive", "dropped").status_code, 200)
        resp = self.sign_in()
        self.assertEqual(resp.status_code, 403)
        p = self.refresh()
        self.assertEqual((p.account_status, p.status_reason), ("inactive", "dropped"))
        self.assertNotIn("_auth_user_id", self.client.session)

    def test_staff_archive_after_self_deactivation_wins(self):
        self.client.force_login(self.student)
        self.deactivate()
        # staff reactivate, then archive properly with a reason
        self.assertEqual(self.staff_set_status("active").status_code, 200)
        self.assertFalse(self.refresh().self_deactivated)
        self.assertEqual(self.staff_set_status("inactive", "transferred").status_code, 200)
        self.assertEqual(self.sign_in().status_code, 403)
        self.assertEqual(self.refresh().account_status, "inactive")

    def test_stale_flag_cannot_undo_a_staff_archive(self):
        # Even if the flag were somehow left on, only a plain "inactive + flag" account reactivates.
        UserProfile.objects.filter(pk=self.profile.pk).update(
            account_status="graduated", self_deactivated=True)
        self.assertEqual(self.sign_in().status_code, 403)
        self.assertEqual(self.refresh().account_status, "graduated")

    def test_staff_cannot_pick_the_student_only_reason(self):
        resp = self.staff_set_status("inactive", "self_deactivated")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(self.refresh().account_status, "active")

    def test_staff_reactivation_clears_the_flag(self):
        self.client.force_login(self.student)
        self.deactivate()
        self.assertEqual(self.staff_set_status("active").status_code, 200)
        p = self.refresh()
        self.assertEqual((p.account_status, p.self_deactivated), ("active", False))

    def test_staff_never_get_reactivated_by_the_signal(self):
        staff = User.objects.create_user("aya", "", self.PASSWORD, is_staff=True)
        self.client.post(reverse("login"), {"username": "aya", "password": self.PASSWORD})
        self.assertEqual(StudentStatusLog.objects.count(), 0)
        self.assertEqual(self.client.post(reverse("account_deactivate"),
                                          {"password": self.PASSWORD}).status_code, 302)
        self.assertTrue(User.objects.get(pk=staff.pk).is_staff)

    def test_archived_page_says_deactivated_for_self_deactivation(self):
        self.client.force_login(self.student)
        self.deactivate()
        self.client.force_login(self.student)      # a second device that was still signed in
        body = self.client.get(reverse("archived")).content.decode()
        self.assertIn("deactivated", body)
        UserProfile.objects.filter(pk=self.profile.pk).update(self_deactivated=False)
        body = self.client.get(reverse("archived")).content.decode()
        self.assertIn("Guidance Office", body)
