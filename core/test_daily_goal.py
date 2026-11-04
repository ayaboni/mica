from datetime import timedelta
from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from . import daily_goal
from .models import DailyGoalPoint, UserProfile


def make_student(name="stu", first="Ana"):
    u = User.objects.create_user(name, f"{name}@example.com", "pw-Aa1!xyz", first_name=first)
    UserProfile.objects.create(user=u, consent_given_at=timezone.now())
    return u


class DailyGoalTests(TestCase):
    def setUp(self):
        self.user = make_student()
        self.client.force_login(self.user)

    def test_exercise_scores_once_per_day(self):
        for _ in range(3):
            self.client.post(reverse('api_exercise_log'), {"exercise": "grounding", "count": 1},
                             content_type="application/json")
        self.assertEqual(DailyGoalPoint.objects.filter(user=self.user, key="ex:grounding").count(), 1)

    def test_different_exercises_each_score(self):
        for ex in ("grounding", "pomodoro", "micapop"):
            self.client.post(reverse('api_exercise_log'), {"exercise": ex, "count": 1},
                             content_type="application/json")
        self.assertEqual(self.client.get(reverse('api_daily_goal')).json()["points"], 3)

    def test_skipped_exercise_scores_nothing(self):
        self.client.post(reverse('api_exercise_log'), {"exercise": "grounding", "duration_seconds": 1},
                         content_type="application/json")
        self.assertEqual(DailyGoalPoint.objects.count(), 0)

    def test_mood_today_scores_but_backfill_does_not(self):
        self.client.post(reverse('api_mood_save'), {"mood": "1", "date": str(timezone.localdate() - timedelta(days=2))},
                         content_type="application/json")
        self.assertEqual(DailyGoalPoint.objects.count(), 0)
        self.client.post(reverse('api_mood_save'), {"mood": "1"}, content_type="application/json")
        self.assertTrue(DailyGoalPoint.objects.filter(key="mood").exists())

    def test_guidance_scores(self):
        self.client.post(reverse('guidance_request'))
        self.client.post(reverse('guidance_send'), {"message": "hello"}, content_type="application/json")
        self.assertEqual(DailyGoalPoint.objects.filter(key="guidance").count(), 1)

    def test_staff_never_score(self):
        staff = User.objects.create_user("st", "st@example.com", "pw-Aa1!xyz", is_staff=True)
        self.assertFalse(daily_goal.award(staff, "chat"))

    def _day(self, offset):
        d = timezone.localdate() - timedelta(days=offset)
        for key in list(daily_goal.TASK_LABELS)[:5]:
            DailyGoalPoint.objects.create(user=self.user, date=d, key=key)

    def test_reward_after_two_days_then_only_once(self):
        self._day(0)
        s = self.client.get(reverse('api_daily_goal')).json()
        self.assertEqual((s["points"], s["reward_pending"]), (5, False))
        self._day(3)
        s = self.client.get(reverse('api_daily_goal')).json()
        self.assertTrue(s["reward_pending"])
        self.assertEqual(s["name"], "Ana")
        self.assertEqual(s["form_url"], "https://forms.gle/gnMxQpZA32McDLX28")
        self.client.post(reverse('api_daily_goal_reward_seen'))
        self.assertFalse(self.client.get(reverse('api_daily_goal')).json()["reward_pending"])

    def test_four_points_is_not_enough(self):
        for key in list(daily_goal.TASK_LABELS)[:4]:
            for off in (0, 1):
                DailyGoalPoint.objects.create(user=self.user, date=timezone.localdate() - timedelta(days=off), key=key)
        self.assertEqual(daily_goal.days_reached(self.user), 0)
