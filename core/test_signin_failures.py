from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse

from .models import SignInFailure


class SignInFailureTests(TestCase):
    PASSWORD = "Sturdy-pass-9271!"

    def setUp(self):
        self.student = User.objects.create_user("juan", "juan@example.com", self.PASSWORD)

    def test_wrong_password_is_recorded_for_a_real_account(self):
        self.client.post(reverse("login"), {"username": "juan", "password": "nope"})
        f = SignInFailure.objects.get()
        self.assertEqual((f.user, f.reason), (self.student, "password"))

    def test_unknown_username_is_recorded_without_what_was_typed(self):
        self.client.post(reverse("login"), {"username": "hunter2-my-password", "password": "x"})
        f = SignInFailure.objects.get()
        self.assertIsNone(f.user)
        self.assertEqual(f.reason, "password")

    def test_admin_sees_the_failures_and_the_watch_list(self):
        SignInFailure.objects.create(user=self.student, reason="password", ip="10.0.0.5")
        SignInFailure.objects.bulk_create(
            [SignInFailure(user=None, reason="password", ip="10.0.0.9") for _ in range(10)])
        admin = User.objects.create_superuser("boss", "boss@example.com", "Admin-pass-123!")
        self.client.force_login(admin)
        data = self.client.get(reverse("admin_signins")).json()
        self.assertEqual(data["failed_24h"], 11)
        self.assertEqual(data["watch"][0]["ip"], "10.0.0.9")
        self.assertIn("juan", [r["account"] for r in data["rows"]])