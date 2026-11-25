import json
from datetime import timedelta

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import Semester


class SemesterListTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("boss", "boss@example.com", "Admin-pass-123!")
        self.client.force_login(self.admin)
        self.today = timezone.localdate()

    def make(self, name, start_offset, length=100):
        start = self.today + timedelta(days=start_offset)
        return Semester.objects.create(name=name, start_date=start, end_date=start + timedelta(days=length))

    def get(self, **params):
        return self.client.get(reverse("admin_semesters"), params).json()

    def test_pages_the_list_and_reports_totals(self):
        for i in range(23):
            self.make("Term %02d" % i, -i * 200)
        d = self.get(size=10)
        self.assertEqual((d["page"], d["pages"], d["total"], d["size"]), (1, 3, 23, 10))
        self.assertEqual(len(d["semesters"]), 10)
        self.assertEqual(len(self.get(size=10, page=3)["semesters"]), 3)

    def test_out_of_range_page_and_odd_size_fall_back(self):
        self.make("Only one", 0)
        self.assertEqual(self.get(page=99)["page"], 1)
        self.assertEqual(self.get(size=7)["size"], 10)

    def test_status_filter_and_counts(self):
        self.make("Past", -400)
        self.make("Now", -10)
        self.make("Later", 60)
        d = self.get(status="current")
        self.assertEqual([s["name"] for s in d["semesters"]], ["Now"])
        self.assertEqual(d["counts"], {"all": 3, "current": 1, "upcoming": 1, "past": 1})
        self.assertEqual(self.get(status="past")["semesters"][0]["status"], "past")
        self.assertEqual(self.get(status="upcoming")["semesters"][0]["status"], "upcoming")

    def test_search_matches_name_but_counts_stay_global(self):
        self.make("1st Sem AY 2026-2027", 0)
        self.make("2nd Sem AY 2026-2027", 200)
        d = self.get(q="2nd")
        self.assertEqual(d["total"], 1)
        self.assertEqual(d["counts"]["all"], 2)

    def test_newest_start_date_comes_first(self):
        self.make("Old", -500)
        self.make("New", 30)
        self.assertEqual([s["name"] for s in self.get()["semesters"]], ["New", "Old"])

    def test_create_and_delete_still_work(self):
        body = json.dumps({"name": "Fresh", "start_date": self.today.isoformat(),
                           "end_date": (self.today + timedelta(days=90)).isoformat()})
        r = self.client.post(reverse("admin_semester_create"), body, content_type="application/json")
        self.assertEqual(r.status_code, 201)
        self.assertEqual(r.json()["semester"]["status"], "current")
        sid = r.json()["semester"]["id"]
        self.assertEqual(self.client.post(reverse("admin_semester_delete", args=[sid])).status_code, 200)
        self.assertFalse(Semester.objects.exists())

    def test_non_admin_is_turned_away(self):
        self.client.logout()
        self.assertNotEqual(self.client.get(reverse("admin_semesters")).status_code, 200)
