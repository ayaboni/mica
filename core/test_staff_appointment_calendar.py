"""The staff appointments calendar: the list API gives each appointment its local day and 24h time."""
from datetime import datetime, timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Appointment, GuidanceThread, UserProfile


class StaffAppointmentCalendarTests(TestCase):
    def setUp(self):
        self.student = User.objects.create_user("sam", password="Student-pass-1!", first_name="Sam", last_name="Reyes")
        UserProfile.objects.create(user=self.student, course="BSIT", year_level="2")
        self.alice = User.objects.create_user("alice", password="Staff-pass-123!", is_staff=True)
        self.carol = User.objects.create_user("carol", password="Staff-pass-123!", is_staff=True)
        self.thread = GuidanceThread.objects.create(student=self.student, counselor=self.alice)

    def make(self, counselor, when, status="confirmed"):
        return Appointment.objects.create(
            thread=self.thread, student=self.student, counselor=counselor,
            scheduled_time=when, status=status,
        )

    def fetch(self, user):
        self.client.force_login(user)
        return self.client.get(reverse("staff_appointments_list")).json()["appointments"]

    def test_includes_course_and_year_for_the_filters(self):
        self.make(self.alice, timezone.now() + timedelta(days=1))
        self.client.force_login(self.alice)
        data = self.client.get(reverse("staff_appointments_list")).json()
        self.assertEqual((data["appointments"][0]["course"], data["appointments"][0]["year"]), ("BSIT", "2"))
        self.assertTrue(data["years"])            # option lists for the filter dropdowns
        self.assertIn("courses", data)

    def test_students_without_a_course_or_year_come_back_blank(self):
        UserProfile.objects.filter(user=self.student).update(course="", year_level="")
        self.make(self.alice, timezone.now() + timedelta(days=1))
        row = self.fetch(self.alice)[0]
        self.assertEqual((row["course"], row["year"]), ("", ""))

    def test_gives_local_date_time_and_ids_for_the_calendar(self):
        # 11:30pm local time: in UTC this is already the next day, so a naive UTC date would land on the wrong cell.
        local = timezone.make_aware(datetime(2030, 3, 14, 23, 30))
        appt = self.make(self.alice, local)
        row = self.fetch(self.alice)[0]
        self.assertEqual(row["id"], appt.id)
        self.assertEqual(row["date_iso"], "2030-03-14")
        self.assertEqual(row["time24"], "23:30")
        self.assertEqual(row["student_id"], self.student.id)
        self.assertEqual(row["thread_id"], self.thread.id)
        # The list view's existing fields are unchanged.
        self.assertEqual(row["status"], "Confirmed")
        self.assertEqual(row["student_name"], "Sam Reyes")

    def test_only_the_counselors_own_appointments_are_listed(self):
        when = timezone.now() + timedelta(days=2)
        self.make(self.alice, when)
        self.make(self.carol, when + timedelta(hours=1))
        self.assertEqual(len(self.fetch(self.alice)), 1)
        self.assertEqual(len(self.fetch(self.carol)), 1)

    def test_students_cannot_use_the_staff_endpoint(self):
        self.client.force_login(self.student)
        self.assertNotEqual(self.client.get(reverse("staff_appointments_list")).status_code, 200)
