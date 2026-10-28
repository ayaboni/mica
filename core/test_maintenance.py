import json

from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse


class MaintenanceModeTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("boss", "boss@example.com", "Admin-pass-123!")

    def turn(self, on, message=""):
        self.client.force_login(self.admin)
        response = self.client.post(reverse("admin_maintenance"), json.dumps({"on": on, "message": message}),
                                   content_type="application/json")
        self.client.logout()
        return response

    def test_visitors_see_the_page_with_the_hotlines_while_it_is_on(self):
        self.turn(True, "Back at 3 PM")
        response = self.client.get(reverse("home"))
        self.assertContains(response, "Back at 3 PM", status_code=503)
        self.assertContains(response, "NCMH", status_code=503)

    def test_api_calls_get_a_json_503(self):
        self.turn(True)
        response = self.client.get("/api/keepalive/")
        self.assertEqual(response.status_code, 503)
        self.assertTrue(response.json()["maintenance"])

    def test_admin_and_login_page_still_work(self):
        self.turn(True)
        self.assertEqual(self.client.get(reverse("login")).status_code, 200)
        self.client.force_login(self.admin)
        self.assertEqual(self.client.get(reverse("admin_dashboard")).status_code, 200)

    def test_site_comes_back_when_turned_off(self):
        self.turn(True)
        self.turn(False)
        self.assertEqual(self.client.get(reverse("home")).status_code, 200)