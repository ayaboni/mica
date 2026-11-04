from django.contrib.auth.models import User
from django.test import RequestFactory, SimpleTestCase, TestCase
from django.urls import reverse

from .models import StaffProfile
from .system_info import parse_user_agent, system_info

CHROME_WIN = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
              "Chrome/126.0.0.0 Safari/537.36")
EDGE_WIN = CHROME_WIN + " Edg/126.0.2592.81"
SAFARI_IPHONE = ("Mozilla/5.0 (iPhone; CPU iPhone OS 17_5 like Mac OS X) AppleWebKit/605.1.15 "
                 "(KHTML, like Gecko) Version/17.5 Mobile/15E148 Safari/604.1")
FIREFOX_LINUX = "Mozilla/5.0 (X11; Linux x86_64; rv:127.0) Gecko/20100101 Firefox/127.0"
CHROME_ANDROID_TABLET = ("Mozilla/5.0 (Linux; Android 14; SM-X710) AppleWebKit/537.36 "
                         "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")


class ParseUserAgentTests(SimpleTestCase):
    def test_chrome_on_windows(self):
        self.assertEqual(parse_user_agent(CHROME_WIN), ("Chrome 126", "Windows 10 / 11", "Computer"))

    def test_edge_is_not_reported_as_chrome(self):
        self.assertEqual(parse_user_agent(EDGE_WIN)[0], "Edge 126")

    def test_safari_on_iphone(self):
        self.assertEqual(parse_user_agent(SAFARI_IPHONE), ("Safari 17", "iOS 17.5", "Phone"))

    def test_firefox_on_linux(self):
        self.assertEqual(parse_user_agent(FIREFOX_LINUX), ("Firefox 127", "Linux", "Computer"))

    def test_android_without_mobile_is_a_tablet(self):
        self.assertEqual(parse_user_agent(CHROME_ANDROID_TABLET)[2], "Tablet")

    def test_missing_or_junk_user_agent_never_raises(self):
        self.assertEqual(parse_user_agent(None), ("Unknown", "Unknown", "Unknown"))
        self.assertEqual(parse_user_agent("curl/8.0")[0], "Unknown")


class SystemTabTests(TestCase):
    PASSWORD = "Staff-pass-123!"

    def setUp(self):
        self.admin = User.objects.create_superuser("boss", "boss@example.com", self.PASSWORD)
        self.staff = User.objects.create_user("msantos", "m@example.com", self.PASSWORD, is_staff=True)
        StaffProfile.objects.get_or_create(user=self.staff, defaults={"role": "counselor"})

    def get(self, user, name):
        self.client.force_login(user)
        return self.client.get(reverse(name), HTTP_USER_AGENT=CHROME_WIN, REMOTE_ADDR="203.0.113.9")

    def test_staff_dashboard_shows_system_tab(self):
        r = self.get(self.staff, "staff_dashboard")
        self.assertContains(r, 'id="tab-system"')
        self.assertContains(r, "Chrome 126")
        self.assertContains(r, "Windows 10 / 11")
        self.assertContains(r, "203.0.113.9")
        self.assertContains(r, "Registered Guidance Counselor")

    def test_admin_dashboard_shows_system_tab(self):
        r = self.get(self.admin, "admin_dashboard")
        self.assertContains(r, 'id="tab-system"')
        self.assertContains(r, "System Admin")
        self.assertContains(r, "203.0.113.9")

    def test_nothing_about_the_server_is_exposed(self):
        rows = [label for s in system_info(self._request())["sections"] for label, _ in s["rows"]]
        for leaked in ("Python", "Django", "Database", "Hostname", "Server software"):
            self.assertNotIn(leaked, rows)

    def test_user_agent_is_escaped(self):
        self.client.force_login(self.staff)
        r = self.client.get(reverse("staff_dashboard"), HTTP_USER_AGENT="<script>alert(1)</script>")
        self.assertNotContains(r, "<script>alert(1)</script>")
        self.assertContains(r, "&lt;script&gt;alert(1)&lt;/script&gt;")

    def test_students_still_cannot_open_staff_dashboard(self):
        student = User.objects.create_user("stu", "s@example.com", self.PASSWORD)
        self.client.force_login(student)
        r = self.client.get(reverse("staff_dashboard"))
        self.assertEqual(r.status_code, 302)

    def _request(self):
        r = RequestFactory().get("/", HTTP_USER_AGENT=CHROME_WIN)
        r.user = self.staff
        from django.contrib.sessions.backends.db import SessionStore
        r.session = SessionStore()
        return r
