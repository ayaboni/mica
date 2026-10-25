"""Service reports: how many sessions / activities each guidance service delivered.

A "session" is counted like this (so one meeting is never counted twice):
  * every staff note ticked "a service was delivered", plus
  * every COMPLETED appointment that has no such note for the same student,
    same service and same day.
"""
import json
from collections import defaultdict
from datetime import datetime, time, timedelta

from django.contrib.auth.decorators import login_required, user_passes_test
from django.contrib.auth.models import User
from django.db.models import Count, Q
from django.http import JsonResponse
from django.shortcuts import get_object_or_404
from django.utils import timezone
from django.utils.dateparse import parse_date
from django.views.decorators.http import require_POST

from .models import Activity, Appointment, StudentFlag, StudentNote, COURSES, SERVICES
from .permissions import can, require_perm, staff_permissions

staff_only = user_passes_test(lambda u: u.is_staff or u.is_superuser, login_url='login')
SERVICE_KEYS = [k for k, _ in SERVICES]
COURSE_LABELS = dict(COURSES)
MAX_DAYS = 366


def aggregate_sessions(notes, appts):
    """Count sessions from plain tuples (easy to unit-test, no database needed).

    notes: iterable of (student_id, service, day, course, year)   - notes ticked "service delivered"
    appts: iterable of (student_id, service, day, course, year)   - completed appointments
    Returns {"by_service": {...}, "students": {...}, "by_course": {...}, "by_year": {...}}
    """
    by_service = defaultdict(int)
    students = defaultdict(set)
    by_course = defaultdict(lambda: defaultdict(int))
    by_year = defaultdict(lambda: defaultdict(int))
    covered = set()          # (student, service, day) that already has a delivered note

    def add(sid, service, course, year):
        by_service[service] += 1
        students[service].add(sid)
        by_course[course or ""][service] += 1
        by_year[year or ""][service] += 1

    for sid, service, day, course, year in notes:
        covered.add((sid, service, day))
        add(sid, service, course, year)
    for sid, service, day, course, year in appts:
        if (sid, service, day) in covered:
            continue                      # the note already counts this meeting
        add(sid, service, course, year)
    return {"by_service": by_service, "students": students, "by_course": by_course, "by_year": by_year}


def _allowed_services(user):
    """Services this staff member may see in reports: the ones whose notes their role can read."""
    readable = staff_permissions(user)["read_note_services"]
    return [k for k in SERVICE_KEYS if k in readable]


def _day_bounds(start, end):
    tz = timezone.get_current_timezone()
    return (timezone.make_aware(datetime.combine(start, time.min), tz),
            timezone.make_aware(datetime.combine(end, time.max), tz))


def _period(request):
    today = timezone.localdate()
    end = parse_date(request.GET.get("to") or "") or today
    start = parse_date(request.GET.get("from") or "") or (end - timedelta(days=29))
    if start > end:
        start, end = end, start
    if (end - start).days > MAX_DAYS:
        start = end - timedelta(days=MAX_DAYS)
    return start, end


@login_required(login_url='login')
@staff_only
@require_perm('view_students')
def service_report(request):
    start, end = _period(request)
    course = (request.GET.get("course") or "").strip()
    year = (request.GET.get("year") or "").strip()

    students = User.objects.filter(is_staff=False, is_superuser=False)     # archived students still count
    if course:
        students = students.filter(profile__course=course)
    if year:
        students = students.filter(profile__year_level=year)

    allowed = _allowed_services(request.user)
    t0, t1 = _day_bounds(start, end)

    notes = StudentNote.objects.filter(
        service__in=allowed, service_delivered=True, session_date__range=(start, end), student__in=students,
    ).values_list("student_id", "service", "session_date", "student__profile__course", "student__profile__year_level")
    appts = [
        (sid, service, timezone.localtime(when).date(), c, y)
        for sid, service, when, c, y in Appointment.objects.filter(
            service__in=allowed, status="completed", scheduled_time__range=(t0, t1), student__in=students,
        ).values_list("student_id", "service", "scheduled_time", "student__profile__course", "student__profile__year_level")
    ]
    agg = aggregate_sessions(notes, appts)

    acts = defaultdict(lambda: {"count": 0, "attendees": 0})
    for a in Activity.objects.filter(service__in=allowed, date__range=(start, end)):
        acts[a.service]["count"] += 1
        acts[a.service]["attendees"] += a.attendee_count

    services = [{
        "key": k, "label": l,
        "sessions": agg["by_service"].get(k, 0),
        "students": len(agg["students"].get(k, ())),
        "activities": acts[k]["count"],
        "attendees": acts[k]["attendees"],
    } for k, l in SERVICES if k in allowed]

    def counts(d):
        return {k: d.get(k, 0) for k in allowed}

    by_course = [{
        "code": c, "label": COURSE_LABELS.get(c, "Not set" if not c else c),
        "counts": counts(d), "total": sum(d.values()),
    } for c, d in sorted(agg["by_course"].items())]
    by_year = [{"year": y, "counts": counts(d), "total": sum(d.values())}
               for y, d in sorted(agg["by_year"].items())]

    return JsonResponse({
        "period": {"from": start.isoformat(), "to": end.isoformat()},
        "filters": {"course": course, "year": year},
        "services": services,
        "totals": {
            "sessions": sum(s["sessions"] for s in services),
            "students": len(set().union(*agg["students"].values())) if agg["students"] else 0,
            "activities": sum(s["activities"] for s in services),
            "attendees": sum(s["attendees"] for s in services),
        },
        "by_course": by_course,
        "by_year": by_year,
    })


# ---------------------------------------------------------------- group activities
def _activity_json(a, me):
    return {
        "id": a.id, "title": a.title, "service": a.service, "service_label": a.get_service_display(),
        "date": a.date.isoformat(), "audience": a.audience, "attendee_count": a.attendee_count,
        "by": (a.created_by.get_full_name() or a.created_by.username) if a.created_by else "Former staff",
        "mine": a.created_by_id == me.id,
    }


@login_required(login_url='login')
@staff_only
@require_perm('view_students')
def activities(request):
    """GET: activities in the period. POST: log a new one."""
    if request.method == "POST":
        try:
            data = json.loads(request.body or b"{}")
        except json.JSONDecodeError:
            return JsonResponse({"error": "Invalid request."}, status=400)
        title = " ".join(str(data.get("title") or "").split())[:150]
        service = data.get("service") or "information"
        day = parse_date(str(data.get("date") or ""))
        audience = " ".join(str(data.get("audience") or "").split())[:150]
        try:
            attendees = max(0, min(int(data.get("attendee_count") or 0), 100000))
        except (TypeError, ValueError):
            return JsonResponse({"error": "Attendees must be a number."}, status=400)
        if len(title) < 3:
            return JsonResponse({"error": "Give the activity a title."}, status=400)
        if service not in SERVICE_KEYS:
            return JsonResponse({"error": "Choose a service."}, status=400)
        if day is None:
            return JsonResponse({"error": "Choose the date."}, status=400)
        if not can(request.user, "write_note_services", service):
            return JsonResponse({"error": "Your staff role can't log %s activities." % service}, status=403)
        a = Activity.objects.create(title=title, service=service, date=day, audience=audience,
                                    attendee_count=attendees, created_by=request.user)
        return JsonResponse({"activity": _activity_json(a, request.user)}, status=201)

    start, end = _period(request)
    items = Activity.objects.select_related("created_by").filter(
        service__in=_allowed_services(request.user), date__range=(start, end))
    return JsonResponse({"activities": [_activity_json(a, request.user) for a in items]})


@login_required(login_url='login')
@staff_only
@require_POST
def activity_delete(request, activity_id):
    a = get_object_or_404(Activity, id=activity_id)
    if a.created_by_id != request.user.id and not request.user.is_superuser:
        return JsonResponse({"error": "Only the person who logged this can delete it."}, status=403)
    a.delete()
    return JsonResponse({"status": "ok"})


# ---------------------------------------------------------------- analytics
@login_required(login_url='login')
@staff_only
@require_perm('view_students')
def analytics(request):
    """System-usage numbers: appointments by status, risk flags, assessment completion.

    Only counts. Nothing a student wrote or answered is included.
    """
    start, end = _period(request)
    t0, t1 = _day_bounds(start, end)
    allowed = _allowed_services(request.user)

    by_status = dict(Appointment.objects.filter(service__in=allowed, scheduled_time__range=(t0, t1))
                     .values_list("status").annotate(n=Count("id")))
    appointments = [{"status": s, "count": by_status.get(s, 0)}
                    for s in ("pending", "confirmed", "completed", "declined", "cancelled")]

    flags = {
        "opened": StudentFlag.objects.filter(created_at__range=(t0, t1)).count(),
        "resolved": StudentFlag.objects.filter(resolved_at__range=(t0, t1)).count(),
        "open_now": StudentFlag.objects.exclude(status="resolved").count(),
    }

    students = User.objects.filter(is_staff=False, is_superuser=False).filter(
        Q(profile__account_status="active") | Q(profile__isnull=True))
    total = students.count()
    done = students.filter(assessment__completed_at__isnull=False, assessment__skipped=False).count()

    return JsonResponse({
        "period": {"from": start.isoformat(), "to": end.isoformat()},
        "appointments": appointments,
        "flags": flags,
        "assessment": {"completed": done, "total": total, "rate": round(100 * done / total) if total else None},
    })
