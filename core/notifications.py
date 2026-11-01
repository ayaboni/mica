import logging

from django.contrib.auth.models import User
from django.db import IntegrityError

from datetime import timedelta

from django.utils import timezone

from .models import ConsoleNotification, Appointment
from .permissions import can

logger = logging.getLogger(__name__)


def staff_with(perm, exclude=None):
    """Active staff (not the system admin) whose role has this permission."""
    users = User.objects.filter(is_staff=True, is_superuser=False, is_active=True)
    if exclude is not None:
        users = users.exclude(pk=exclude.pk)
    return [u for u in users if can(u, perm)]


def notify(users, kind, text, *, section='', student_ref=None, ref_id=None,
           urgent=False, dedupe_key='', collapse_unread=False):
    """Create one console notification per user.

    dedupe_key      -> never create the same key twice for a user (reminders).
    collapse_unread -> skip if that user already has an UNREAD one with the same
                       kind and ref_id (so 10 chat messages = 1 notif until it's read).
    A failure here must never break the real action, so errors are only logged.
    """
    try:
        for user in users:
            if collapse_unread and ConsoleNotification.objects.filter(
                    recipient=user, kind=kind, ref_id=ref_id, read_at__isnull=True).exists():
                continue
            data = dict(kind=kind, text=text[:200], section=section,
                        student_ref=student_ref, ref_id=ref_id, urgent=urgent)
            if dedupe_key:
                try:
                    ConsoleNotification.objects.get_or_create(
                        recipient=user, dedupe_key=dedupe_key, defaults=data)
                except IntegrityError:
                    pass
            else:
                ConsoleNotification.objects.create(recipient=user, **data)
    except Exception:
        logger.exception("Could not create a console notification")


def refresh_derived(user):
    """Notifications that come from the clock or from counts, not from a click.
    Runs each time the bell polls, so no cron job is needed."""
    try:
        now = timezone.now()

        # 1) Upcoming appointments (confirmed ones only)
        appts = (Appointment.objects
                 .filter(counselor=user, status='confirmed', scheduled_time__gt=now,
                         scheduled_time__lte=now + timedelta(hours=24))
                 .select_related('student'))
        for a in appts:
            local = timezone.localtime(a.scheduled_time)
            name = a.student.get_full_name() or a.student.username
            if a.scheduled_time - now <= timedelta(hours=1):
                notify([user], 'appointment',
                       f"Starting soon: appointment with {name} at {local:%I:%M %p}",
                       section='appointments', student_ref=a.student_id, ref_id=a.id,
                       urgent=True, dedupe_key=f'appt-{a.id}-1h')
            else:
                day = 'today' if local.date() == timezone.localdate() else 'tomorrow'
                notify([user], 'appointment',
                       f"Upcoming: appointment with {name} {day} at {local:%I:%M %p}",
                       section='appointments', student_ref=a.student_id, ref_id=a.id,
                       dedupe_key=f'appt-{a.id}-24h')

        # 2) Accounts waiting for approval
        if can(user, 'verify_students'):
            from . import verification
            pending = list(verification.pending_students())
            pending_ids = {p.id for p in pending}
            existing = set(ConsoleNotification.objects
                           .filter(recipient=user, dedupe_key__startswith='pending-')
                           .values_list('dedupe_key', flat=True))
            for p in pending[:50]:
                if f'pending-{p.id}' not in existing:
                    notify([user], 'pending',
                           f"{p.get_full_name() or p.username} is waiting for account approval",
                           section='pending', student_ref=p.id, dedupe_key=f'pending-{p.id}')
            # Once someone is approved or deactivated, their notif is done.
            ConsoleNotification.objects.filter(
                recipient=user, kind='pending', read_at__isnull=True
            ).exclude(student_ref__in=pending_ids).update(read_at=now)
    except Exception:
        logger.exception("Could not refresh derived console notifications")