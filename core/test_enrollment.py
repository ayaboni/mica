from django.test import TestCase

from .models import EnrolledStudent
from .views import _enrollment_check


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