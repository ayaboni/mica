"""Student numbers typed on an iPhone number pad, which has no "-" key: digits only must work."""
from django.contrib.auth.models import User
from django.test import SimpleTestCase, TestCase
from django.urls import reverse

from .models import UserProfile
from .student_number import normalize_student_number, student_number_problem


class NormalizeStudentNumberTests(SimpleTestCase):
    def test_seven_digits_become_000_0000(self):
        self.assertEqual(normalize_student_number("1234567"), "123-4567")

    def test_nine_digits_become_00_0000_000(self):
        self.assertEqual(normalize_student_number("123456789"), "12-3456-789")

    def test_already_formatted_numbers_are_left_alone(self):
        self.assertEqual(normalize_student_number("123-4567"), "123-4567")
        self.assertEqual(normalize_student_number("12-3456-789"), "12-3456-789")

    def test_spaces_and_pasted_dash_lookalikes_are_tidied(self):
        self.assertEqual(normalize_student_number("  123 4567 "), "123-4567")
        self.assertEqual(normalize_student_number("123\u20134567"), "123-4567")          # en dash
        self.assertEqual(normalize_student_number("12\u20143456\u2014789"), "12-3456-789")  # em dashes

    def test_wrong_lengths_and_junk_are_not_made_valid(self):
        for bad in ("123456", "12345678", "1234567890", "12-34567", "abc-defg", "", None, "2024-1111",
                    "\u0661\u0662\u0663\u0664\u0665\u0666\u0667"):               # Arabic-Indic digits
            self.assertIsNotNone(student_number_problem(normalize_student_number(bad)), repr(bad))

    def test_valid_numbers_pass_after_normalizing(self):
        for raw in ("1234567", "123456789", "123-4567", "12-3456-789", " 1234567\n"):
            self.assertIsNone(student_number_problem(normalize_student_number(raw)), repr(raw))

    def test_normalizing_twice_changes_nothing(self):
        for raw in ("1234567", "123456789", "123 4567", "junk"):
            self.assertEqual(normalize_student_number(normalize_student_number(raw)), normalize_student_number(raw))


class CompleteProfileDigitsOnlyTests(TestCase):
    """The Google first-sign-in form: the server accepts digits only and shows the dashed number back."""

    def setUp(self):
        self.user = User.objects.create_user("gsam", password="Student-pass-1!", first_name="Sam", last_name="Cruz")
        UserProfile.objects.create(user=self.user, needs_profile_setup=True)
        self.client.force_login(self.user)

    def post(self, number):
        # An invalid course on purpose: the form stops after the student number check, so nothing is saved.
        return self.client.post(reverse("complete_profile"), {
            "first_name": "Sam", "last_name": "Cruz", "student_number": number,
            "course": "not-a-course", "year_level": "not-a-year", "username": "gsam2"})

    def test_digits_only_is_not_rejected_as_a_bad_format(self):
        r = self.post("1234567")
        self.assertEqual(r.status_code, 200)
        self.assertNotContains(r, "Student number must look like")
        self.assertContains(r, "Please choose a valid course and year level.")   # got past the number check
        self.assertContains(r, 'value="123-4567"')                                 # shown back with its dashes

    def test_nine_digits_too(self):
        r = self.post("123456789")
        self.assertNotContains(r, "Student number must look like")
        self.assertContains(r, 'value="12-3456-789"')

    def test_a_wrong_length_is_still_refused(self):
        r = self.post("123456")
        self.assertContains(r, "Student number must look like")

    def test_the_form_accepts_digits_and_adds_the_dashes_on_submit(self):
        html = self.client.get(reverse("complete_profile")).content.decode()
        self.assertIn('inputmode="numeric"', html)
        self.assertIn("pattern=\"[0-9]{7}|[0-9]{9}|[0-9]{3}-[0-9]{4}|[0-9]{2}-[0-9]{4}-[0-9]{3}\"", html)
        self.assertIn("addEventListener('submit'", html)
