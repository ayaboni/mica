import json
from datetime import timedelta
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import AuditEvent, DataRequest, UserProfile


class DataRequestTests(TestCase):
    PASSWORD = "Sturdy-pass-9271!"

    def setUp(self):
        self.admin = User.objects.create_superuser("boss", "boss@example.com", "Admin-pass-123!")
        self.student = User.objects.create_user(
            "juan", "juan@example.com", self.PASSWORD, first_name="Juan", last_name="Cruz")
        UserProfile.objects.create(user=self.student, student_number="2022-00123",
                                   needs_profile_setup=False)
        self.client.force_login(self.admin)

    # -- helpers --
    def post(self, name, data, args=None):
        return self.client.post(reverse(name, args=args or []), json.dumps(data),
                                content_type="application/json")

    def create(self, **overrides):
        data = {"student_id": self.student.id, "request_type": "access",
                "received_on": (timezone.localdate() - timedelta(days=1)).isoformat(), "note": "", "request_detail": ""}
        data.update(overrides)
        return self.post("admin_data_request_create", data)

    def close(self, pk, status="done", outcome=""):
        return self.post("admin_data_request_close", {"status": status, "outcome": outcome}, [pk])

    def make(self, **kw):
        defaults = dict(student_name="Juan Cruz", student=self.student, student_username="juan",
                    student_number="2022-00123", request_type="access",
                    received_on=timezone.localdate() - timedelta(days=1))
        defaults.update(kw)
        return DataRequest.objects.create(**defaults)

    def assertRejected(self, response):
        self.assertEqual(response.status_code, 400)

    def admin_change_rows(self):
        rows = self.client.get(reverse("admin_audit")).json()["rows"]
        return [r for r in rows if r["action"] == "Admin change"]

    # -- create --
    def test_create_saves_an_open_request_with_the_student_details(self):
        response = self.create(note="Asked at the office")
        self.assertEqual(response.status_code, 200)
        dr = DataRequest.objects.get()
        self.assertEqual(dr.status, "open")
        self.assertEqual(dr.student, self.student)
        self.assertEqual(dr.student_username, "juan")
        self.assertEqual(dr.student_number, "2022-00123")
        self.assertEqual(dr.logged_by, self.admin)

    def test_create_needs_a_real_student(self):
        self.assertRejected(self.create(student_id=999999))
        self.assertRejected(self.create(student_id=None))
        self.assertFalse(DataRequest.objects.exists())

    def test_create_rejects_an_unknown_type_and_a_bad_date(self):
        self.assertRejected(self.create(request_type="nonsense"))
        self.assertRejected(self.create(received_on="not-a-date"))
        self.assertFalse(DataRequest.objects.exists())

    def test_other_needs_a_detail(self):
        self.assertRejected(self.create(request_type="other", request_detail="   "))
        self.assertFalse(DataRequest.objects.exists())
        self.assertIn(self.create(request_type="other", request_detail="Copy of my chat").status_code, (200, 201))
        self.assertEqual(DataRequest.objects.get().request_detail, "Copy of my chat")

    def test_detail_is_ignored_for_the_fixed_types(self):
        self.create(request_type="access", request_detail="should not be kept")
        self.assertEqual(DataRequest.objects.get().request_detail, "")

    def test_over_long_note_is_trimmed_to_200_characters(self):
        self.create(note="x" * 500)
        self.assertEqual(len(DataRequest.objects.get().note), 200)

    def test_a_future_date_is_refused(self):
        tomorrow = (timezone.localdate() + timedelta(days=1)).isoformat()
        self.assertRejected(self.create(received_on=tomorrow))
        self.assertFalse(DataRequest.objects.exists())

    # -- close --
    def test_done_closes_the_request_and_stamps_the_time(self):
        dr = self.make()
        self.assertEqual(self.close(dr.id, "done", "Sent a copy").status_code, 200)
        dr.refresh_from_db()
        self.assertEqual((dr.status, dr.outcome), ("done", "Sent a copy"))
        self.assertIsNotNone(dr.handled_at)

    def test_decline_works_too(self):
        dr = self.make()
        self.close(dr.id, "declined", "Not the account owner")
        dr.refresh_from_db()
        self.assertEqual(dr.status, "declined")

    def test_a_request_can_only_be_closed_once(self):
        dr = self.make()
        self.close(dr.id, "done", "first")
        stamped = DataRequest.objects.get(pk=dr.id).handled_at
        self.assertRejected(self.close(dr.id, "declined", "second"))
        dr.refresh_from_db()
        self.assertEqual((dr.status, dr.outcome, dr.handled_at), ("done", "first", stamped))

    def test_close_only_accepts_done_or_declined(self):
        dr = self.make()
        for bad in ("open", "nonsense", ""):
            self.assertRejected(self.close(dr.id, bad))
        dr.refresh_from_db()
        self.assertEqual(dr.status, "open")

    def test_closing_an_unknown_request_is_404(self):
        self.assertEqual(self.close(999999).status_code, 404)

    # -- list --
    def test_list_filters_by_status_and_counts_open_ones(self):
        self.make()
        done = self.make()
        self.close(done.id)
        data = self.client.get(reverse("admin_data_requests"), {"status": "open"}).json()
        self.assertEqual(data["total"], 1)
        self.assertEqual(data["open_count"], 1)
        self.assertEqual(self.client.get(reverse("admin_data_requests")).json()["total"], 2)

    def test_list_survives_a_deleted_student(self):
        self.make()
        self.student.delete()
        row = self.client.get(reverse("admin_data_requests")).json()["rows"][0]
        self.assertEqual(row["student_name"], "Juan Cruz")

    # -- audit --
    def test_creating_and_closing_are_logged_as_admin_changes(self):
        before = len(self.admin_change_rows())
        self.create()
        self.assertEqual(len(self.admin_change_rows()), before + 1)
        self.close(DataRequest.objects.get().id)
        self.assertEqual(len(self.admin_change_rows()), before + 2)

    def test_the_audit_log_does_not_copy_the_private_note(self):
        self.create(note="PRIVATE-NOTE-TEXT")
        self.assertFalse(AuditEvent.objects.filter(detail__contains="PRIVATE-NOTE-TEXT").exists())

    def test_a_rejected_request_leaves_no_audit_entry(self):
        before = AuditEvent.objects.count()
        self.create(request_type="nonsense")
        self.assertEqual(AuditEvent.objects.count(), before)

    # -- permissions --
    def test_staff_and_students_are_blocked(self):
        staff = User.objects.create_user("lena", "lena@example.com", self.PASSWORD, is_staff=True)
        dr = self.make()
        for user in (staff, self.student):
            self.client.force_login(user)
            for response in (
                self.client.get(reverse("admin_data_requests")),
                self.create(),
                self.close(dr.id),
            ):
                self.assertIn(response.status_code, (302, 403))
        self.assertEqual(DataRequest.objects.count(), 1)
        dr.refresh_from_db()
        self.assertEqual(dr.status, "open")

    def test_signed_out_visitors_are_blocked(self):
        self.client.logout()
        self.assertIn(self.create().status_code, (302, 403))
        self.assertFalse(DataRequest.objects.exists())