import logging
import re
from django.db.models import Count, Q

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
        if user.is_superuser:
            _refresh_admin(user, now)
            return

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


def _admin_sync(user, kind, wanted, now):
    """Create the notifications that are missing. Once a problem is gone,
    its unread notification is marked read by itself."""
    keys = set()
    for w in wanted:
        keys.add(w['key'])
        notify([user], kind, w['text'], section=w['section'], ref_id=w.get('ref_id'),
               urgent=w.get('urgent', False), dedupe_key=w['key'])
    ConsoleNotification.objects.filter(
        recipient=user, kind=kind, read_at__isnull=True
    ).exclude(dedupe_key__in=keys).update(read_at=now)


def _refresh_admin(user, now):
    from .models import SignInFailure, DataRequest, SiteSettings, EnrolledStudent
    today = timezone.localdate()
    day = f"{today:%Y%m%d}"

    # 1) IPs under "Worth a look" (one per IP per day)
    rows = (SignInFailure.objects.filter(created_at__gte=now - timedelta(hours=24))
            .exclude(ip=None).values('ip')
            .annotate(n=Count('id'), accounts=Count('user', distinct=True))
            .order_by('-n'))
    wanted = [dict(key=f"ip-{r['ip']}-{day}", section='signins', urgent=True,
                   text=f"Worth a look: {r['ip']} had {r['n']} failed sign-ins "
                        f"on {r['accounts']} account(s) in the last 24 hours")
              for r in rows[:20] if r['n'] >= 10 or r['accounts'] >= 3][:5]
    _admin_sync(user, 'security', wanted, now)

    # 2) Open data requests (urgent once 7 days old)
    wanted = []
    for d in DataRequest.objects.filter(status='open'):
        age = (today - d.received_on).days
        if age >= 7:
            wanted.append(dict(key=f"dr-{d.id}-old", section='privacy', ref_id=d.id, urgent=True,
                               text=f"Data request from {d.student_name} has been open for {age} days"))
        else:
            wanted.append(dict(key=f"dr-{d.id}", section='privacy', ref_id=d.id,
                               text=f"New data request from {d.student_name} "
                                    f"({d.get_request_type_display().lower()})"))
    _admin_sync(user, 'data_request', wanted, now)

    # 3) Staff who never signed in or never finished setup (after 1 day)
    pending = (User.objects.filter(is_staff=True, is_superuser=False, is_active=True,
                                   date_joined__lte=now - timedelta(days=1))
               .filter(Q(last_login__isnull=True) | Q(staff_profile__must_complete_setup=True)))
    wanted = [dict(key=f"staff-setup-{u.id}", section='accounts',
                   text=f"{u.get_full_name() or u.username} has not finished account setup yet")
              for u in pending[:20]]
    _admin_sync(user, 'staff_setup', wanted, now)

    # 4) A service in Service Health is down (once per day)
    from .admin_api import _service_health
    wanted = [dict(key=f"svc-{re.sub(r'\\W+', '-', s['name'].lower())[:40]}-{day}",
                   section='settings:integrations', urgent=True,
                   text=f"{s['name']} is down: {s['meta']}")
              for s in _service_health() if s['status'] == 'down']
    _admin_sync(user, 'service', wanted, now)

    # 5) Maintenance mode left on for more than 1 hour (once per day)
    s = SiteSettings.get()
    wanted = []
    if s.maintenance_on and s.updated_at <= now - timedelta(hours=1):
        wanted.append(dict(key=f"maint-{day}", section='settings',
                           text="Maintenance mode is still ON"))
    _admin_sync(user, 'maintenance', wanted, now)

    # 6) No enrollment list uploaded (once per week)
    wanted = []
    if not EnrolledStudent.objects.exists():
        y, w, _ = today.isocalendar()
        wanted.append(dict(key=f"enroll-empty-{y}-w{w}", section='enrollment',
                           text="No enrollment list is uploaded yet"))
    _admin_sync(user, 'enrollment', wanted, now)

    # 7) This admin account has no email, so no sign-in codes
    wanted = []
    if not (user.email or '').strip():
        wanted.append(dict(key='admin-email', section='settings:account',
                           text="Your admin account has no email, so it can't get sign-in codes"))
    _admin_sync(user, 'account', wanted, now)