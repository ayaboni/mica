"""The student daily goal: use MICA's features, earn points, hit the target two days, get the survey.

Every task is worth at most ONE point per day (a DailyGoalPoint row is unique per user/day/task),
so repeating an exercise can't farm points. The target resets every (local) day. When a student has
reached the target on REWARD_DAYS different days, the dashboard shows the evaluation-form pop-up once.
"""
from django.db import IntegrityError, transaction
from django.db.models import Count
from django.utils import timezone

from .models import DailyGoalPoint

DAILY_TARGET = 5
REWARD_DAYS = 2
EVALUATION_FORM_URL = "https://forms.gle/gnMxQpZA32McDLX28"

# Exercise keys must match reports.EXERCISE_LABELS.
TASK_LABELS = {
    "chat": "Chat with Mica",
    "mood": "Log your mood",
    "guidance": "Ask Guidance for help",
    "ex:breathing": "Breathing exercise",
    "ex:affirmations": "Affirmations",
    "ex:grounding": "5-4-3-2-1 Grounding",
    "ex:pomodoro": "Pomodoro",
    "ex:micapop": "Mica Pop",
}


def _is_student(user):
    return bool(user and user.is_authenticated and not (user.is_staff or user.is_superuser))


def award(user, key, day=None):
    """Give `user` the point for task `key` today. Safe to call any number of times; never raises."""
    if key not in TASK_LABELS or not _is_student(user):
        return False
    try:
        with transaction.atomic():
            _, created = DailyGoalPoint.objects.get_or_create(
                user=user, date=day or timezone.localdate(), key=key)
        return created
    except IntegrityError:      # two requests raced: the point exists, which is all we wanted
        return False
    except Exception:           # the goal must never break the feature it is attached to
        return False


def days_reached(user):
    return (DailyGoalPoint.objects.filter(user=user).values('date')
            .annotate(n=Count('id')).filter(n__gte=DAILY_TARGET).count())


def student_name(user):
    profile = getattr(user, 'profile', None)
    return (user.first_name or (profile.nickname if profile and profile.nickname else '') or user.get_username()).strip()


def summary(user):
    today = timezone.localdate()
    done = set(DailyGoalPoint.objects.filter(user=user, date=today).values_list('key', flat=True))
    reached = days_reached(user)
    profile = getattr(user, 'profile', None)
    pending = bool(profile and reached >= REWARD_DAYS and profile.goal_reward_shown_at is None)
    return {
        "points": min(len(done), DAILY_TARGET),
        "target": DAILY_TARGET,
        "tasks": [{"key": k, "label": v, "done": k in done} for k, v in TASK_LABELS.items()],
        "days_reached": reached,
        "days_needed": REWARD_DAYS,
        "reward_pending": pending,
        "name": student_name(user),
        "form_url": EVALUATION_FORM_URL,
    }
