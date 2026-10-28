from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from .models import AuditEvent
import json


class AdminActionsInAuditLogTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("boss", "boss@example.com", "Admin-pass-123!")
        self.client.force_login(self.admin)

    def rows(self, **params):
        return self.client.get(reverse("admin_audit"), params).json()["rows"]

    def test_admin_changes_show_up_in_the_audit_log(self):
        AuditEvent.objects.create(actor=self.admin, event="settings_changed", detail="Semesters: added Test")
        rows = self.rows()
        self.assertEqual(rows[0]["action"], "Admin change")
        self.assertEqual(rows[0]["detail"], "Semesters: added Test")

    def test_filters_work_for_admin_changes(self):
        AuditEvent.objects.create(actor=self.admin, event="settings_changed", detail="x")
        self.assertEqual(len(self.rows(action="admin_change")), 1)
        self.assertEqual(len(self.rows(action="view_profile")), 0)
        self.assertEqual(len(self.rows(student="someone")), 0)

    def test_staff_suspend_and_student_unlock_are_logged(self):
        staff = User.objects.create_user("lena", "lena@example.com", "Sturdy-pass-9271!", is_staff=True)
        student = User.objects.create_user("juan", "juan@example.com", "Sturdy-pass-9271!")
        self.client.post(reverse("admin_staff_active", args=[staff.id]), json.dumps({"active": False}),
                         content_type="application/json")
        self.client.post(reverse("admin_student_unlock", args=[student.id]), "{}",
                         content_type="application/json")
        details = [e.detail for e in AuditEvent.objects.all()]
        self.assertTrue(any(d.startswith("Staff: suspended") for d in details))
        self.assertTrue(any(d.startswith("Students: unlocked") for d in details))