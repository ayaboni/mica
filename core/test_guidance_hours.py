"""Talk to Guidance "Heads up" box: after-hours advisory (6 PM to 7 AM) and the panic 24-hour advisory."""
from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse

from .models import UserProfile
from .views import GUIDANCE_CLOSE_HOUR, GUIDANCE_OPEN_HOUR, _clock_label


class ClockLabelTests(TestCase):
    def test_labels(self):
        self.assertEqual(_clock_label(7), "7:00 AM")
        self.assertEqual(_clock_label(18), "6:00 PM")
        self.assertEqual(_clock_label(12), "12:00 PM")
        self.assertEqual(_clock_label(0), "12:00 AM")

    def test_office_hours_are_7_to_6(self):
        self.assertEqual((GUIDANCE_OPEN_HOUR, GUIDANCE_CLOSE_HOUR), (7, 18))


class GuidanceAdvisoryMarkupTests(TestCase):
    def setUp(self):
        cache.clear()
        self.student = User.objects.create_user("sam", password="Student-pass-1!")
        UserProfile.objects.create(user=self.student)
        self.client.force_login(self.student)
        self.html = self.client.get(reverse("student_dashboard"), follow=True).content.decode()

    def test_after_hours_advisory_is_inside_the_heads_up_box_and_starts_hidden(self):
        note = self.html[self.html.index('class="guidance-note"'):]
        note = note[:note.index("</div>\n        </div>") + 20]
        self.assertIn('id="guidance-hours-advisory"', note)
        self.assertIn("hidden", note.split('id="guidance-hours-advisory"')[1].split(">")[0])
        self.assertIn("Heads up: the Guidance staff can read", note)

    def test_advisory_states_office_hours_and_follows_the_office_timezone(self):
        self.assertIn("Staff are available from 7:00 AM to 6:00 PM", self.html)
        self.assertIn("during their working hours", self.html)
        self.assertIn('data-tz="Asia/Manila"', self.html)
        self.assertIn('data-open="7"', self.html)
        self.assertIn('data-close="18"', self.html)

    def test_panic_advisories_mention_the_24_hour_response_time(self):
        self.assertGreaterEqual(self.html.count("up to 24 hours"), 3)    # tooltip, confirm dialog, sent dialog
