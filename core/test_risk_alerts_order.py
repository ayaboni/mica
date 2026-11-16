"""Risk Alerts (staff): panic button cases are always first, then urgent, watch, stable, resolved."""
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from .models import StudentFlag, UserProfile


class RiskAlertsOrderTests(TestCase):
    def setUp(self):
        cache.clear()
        self.staff = User.objects.create_user("alice", password="Staff-pass-123!", is_staff=True)

    def student(self, username):
        u = User.objects.create_user(username, password="Student-pass-1!", first_name=username.title())
        UserProfile.objects.create(user=u)
        return u

    def flags(self):
        self.client.force_login(self.staff)
        return self.client.get(reverse("staff_flags")).json()["flags"]

    def test_open_panic_flags_come_first_even_if_older(self):
        old_panic = StudentFlag.objects.create(student=self.student("pam"), reason="PANIC", is_panic=True)
        regular = StudentFlag.objects.create(student=self.student("rob"), flagged_by=self.staff, reason="Low mood")
        flags = self.flags()
        self.assertEqual([f["id"] for f in flags], [old_panic.id, regular.id])
        self.assertTrue(flags[0]["panic"])
        self.assertIn("created_ts", flags[0])

    def test_resolved_flags_sink_to_the_bottom_even_if_panic(self):
        resolved_panic = StudentFlag.objects.create(student=self.student("old"), reason="PANIC", is_panic=True,
                                                    status="resolved")
        regular = StudentFlag.objects.create(student=self.student("new"), flagged_by=self.staff, reason="Low mood")
        flags = self.flags()
        self.assertEqual([f["id"] for f in flags], [regular.id, resolved_panic.id])

    def test_unassigned_before_in_review_within_a_tier(self):
        taken = StudentFlag.objects.create(student=self.student("tam"), flagged_by=self.staff, reason="a",
                                           status="review", assigned_to=self.staff)
        fresh = StudentFlag.objects.create(student=self.student("fay"), flagged_by=self.staff, reason="b")
        flags = self.flags()
        self.assertEqual([f["id"] for f in flags], [fresh.id, taken.id])
