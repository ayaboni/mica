from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .models import EnrolledStudent, UserProfile


def make_student(i, status="active", last="Santos", course="BSIT"):
    u = User.objects.create_user(f"stu{i}", f"stu{i}@example.com", "Sturdy-pass-9271!",
                                 first_name=f"First{i}", last_name=last)
    UserProfile.objects.create(user=u, student_number=f"2022-{i:05d}", course=course,
                               year_level="2", account_status=status, needs_profile_setup=False)
    return u


class EnrollmentMissingEndpointTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("boss", "boss@example.com", "Admin-pass-123!")
        self.client.force_login(self.admin)
        EnrolledStudent.objects.create(student_number="2022-00001", last_name="Santos")  # list is ON

    def get(self, **params):
        return self.client.get(reverse("admin_enrollment_missing"), params).json()

    def test_nothing_is_suggested_while_no_list_is_loaded(self):
        EnrolledStudent.objects.all().delete()
        make_student(2)
        data = self.get()
        self.assertEqual((data["total"], data["all_total"]), (0, 0))

    def test_students_on_the_list_are_not_suggested(self):
        make_student(1)
        make_student(2)
        data = self.get()
        self.assertEqual([s["student_number"] for s in data["students"]], ["2022-00002"])

    def test_only_active_students_are_suggested(self):
        make_student(2, status="inactive")
        make_student(3, status="graduated")
        make_student(4)
        self.assertEqual(self.get()["all_total"], 1)

    def test_pages_hold_fifteen_and_the_last_page_has_the_rest(self):
        for i in range(2, 22):                       # 20 students missing
            make_student(i)
        first, second = self.get(page=1), self.get(page=2)
        self.assertEqual((first["total"], first["pages"], len(first["students"])), (20, 2, 15))
        self.assertEqual(len(second["students"]), 5)
        ids = {s["id"] for s in first["students"]} | {s["id"] for s in second["students"]}
        self.assertEqual(len(ids), 20)               # no student appears twice

    def test_an_out_of_range_page_does_not_crash(self):
        make_student(2)
        self.assertEqual(self.client.get(reverse("admin_enrollment_missing"), {"page": 99}).status_code, 200)
        self.assertEqual(self.client.get(reverse("admin_enrollment_missing"), {"page": "abc"}).status_code, 200)

    def test_search_works_on_name_number_and_course(self):
        make_student(2, last="Reyes", course="BSN")
        make_student(3, last="Lopez", course="BSIT")
        self.assertEqual([s["student_number"] for s in self.get(q="reyes")["students"]], ["2022-00002"])
        self.assertEqual([s["student_number"] for s in self.get(q="2022-00003")["students"]], ["2022-00003"])
        self.assertEqual(len(self.get(q="BSN")["students"]), 1)

    def test_search_keeps_all_total_and_matching_covers_every_page(self):
        for i in range(2, 22):
            make_student(i, last="Reyes" if i < 19 else "Lopez")   # 17 Reyes, 3 Lopez
        data = self.get(q="reyes")
        self.assertEqual((data["total"], data["all_total"]), (17, 20))
        self.assertEqual(len(data["matching"]), 17)  # what "Select all N matching" uses

    def test_non_admin_is_blocked(self):
        make_student(2)
        self.client.force_login(User.objects.create_user("lena", "l@example.com", "Sturdy-pass-9271!", is_staff=True))
        self.assertEqual(self.client.get(reverse("admin_enrollment_missing")).status_code, 302)