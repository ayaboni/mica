from django.test import TestCase
from django.contrib.auth.models import User
from .models import EnrolledStudent, UserProfile
from .views import _enrollment_check
from .admin_api import _missing_students, _sync_year_levels


class EnrollmentCheckTests(TestCase):
    def test_check_is_off_while_the_list_is_empty(self):
        self.assertIsNone(_enrollment_check("2022-00001", "Anything"))

    def test_matches_number_and_last_name_loosely(self):
        EnrolledStudent.objects.create(student_number="2022-00123", last_name="De la Peña")
        self.assertIsNone(_enrollment_check("2022-00123", "dela pena"))
        self.assertIsNone(_enrollment_check(" 2022-00123 ", "DE LA PEÑA"))

    def test_wrong_last_name_or_number_is_refused(self):
        EnrolledStudent.objects.create(student_number="2022-00123", last_name="Santos")
        self.assertTrue(_enrollment_check("2022-00123", "Reyes"))
        self.assertTrue(_enrollment_check("2022-99999", "Santos"))
        self.assertTrue(_enrollment_check("", ""))


def make_student(username, number, year="1"):
    u = User.objects.create_user(username, username + "@example.com", "Sturdy-pass-9271!")
    return UserProfile.objects.create(user=u, student_number=number, year_level=year, needs_profile_setup=False)


class EnrollmentSyncTests(TestCase):
    def test_year_levels_follow_the_list(self):
        a = make_student("a", "2022-00001", "1")
        b = make_student("b", "2022-00002", "2")
        rows = [EnrolledStudent(student_number="2022-00001", last_name="A", year_level="2"),
                EnrolledStudent(student_number="2022-00002", last_name="B", year_level="")]
        self.assertEqual(_sync_year_levels(rows), 1)
        a.refresh_from_db(); b.refresh_from_db()
        self.assertEqual((a.year_level, b.year_level), ("2", "2"))

    def test_students_missing_from_the_list_are_suggested(self):
        make_student("a", "2022-00001")
        make_student("b", "2022-00002")
        self.assertEqual(_missing_students(), [])          # no list loaded: no suggestions
        EnrolledStudent.objects.create(student_number="2022-00001", last_name="A")
        self.assertEqual([p.student_number for p in _missing_students()], ["2022-00002"])