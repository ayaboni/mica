from django.contrib.auth.models import User
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import AssessmentResponse, UserProfile


@override_settings(ADMIN_TWO_FACTOR_REQUIRED=False)
class WrongDashboardTests(TestCase):
    """Opening another account type's dashboard sends you to your own, not to the login page."""
    PASSWORD = "Pass-word-123!"

    def setUp(self):
        self.admin = User.objects.create_superuser("boss", "boss@example.com", self.PASSWORD)
        self.staff = User.objects.create_user("msantos", "m@example.com", self.PASSWORD, is_staff=True)
        self.student = User.objects.create_user("juan", "j@example.com", self.PASSWORD)
        UserProfile.objects.get_or_create(user=self.student)
        AssessmentResponse.objects.update_or_create(
            user=self.student, defaults={"completed_at": "2026-01-01T00:00:00Z"})

    def go(self, user, name):
        self.client.force_login(user)
        return self.client.get(reverse(name))

    def test_student_is_sent_back_from_staff_and_admin_dashboards(self):
        for name in ("staff_dashboard", "admin_dashboard"):
            self.assertRedirects(self.go(self.student, name), reverse("student_dashboard"),
                                 fetch_redirect_response=False)

    def test_staff_is_sent_back_from_student_and_admin_dashboards(self):
        for name in ("student_dashboard", "admin_dashboard"):
            r = self.go(self.staff, name)
            self.assertEqual(r.status_code, 302)
            self.assertNotEqual(r.url, reverse(name))
            self.assertNotIn(reverse("login"), r.url)

    def test_admin_is_sent_back_from_student_and_staff_dashboards(self):
        for name in ("student_dashboard", "staff_dashboard"):
            self.assertRedirects(self.go(self.admin, name), reverse("admin_dashboard"),
                                 fetch_redirect_response=False)

    def test_own_dashboard_still_opens(self):
        self.assertEqual(self.go(self.student, "student_dashboard").status_code, 200)
        self.assertEqual(self.go(self.staff, "staff_dashboard").status_code, 200)
        self.assertEqual(self.go(self.admin, "admin_dashboard").status_code, 200)

    def test_signed_out_visitors_still_go_to_login(self):
        for name in ("student_dashboard", "staff_dashboard", "admin_dashboard"):
            r = self.client.get(reverse(name))
            self.assertEqual(r.status_code, 302)
            self.assertIn(reverse("login"), r.url)


class SignInFormTests(TestCase):
    def test_sign_in_forms_use_the_no_resubmit_script(self):
        r = self.client.get(reverse("login"))
        self.assertContains(r, "data-no-resubmit")
        self.assertContains(r, "core/js/no-resubmit.js")
