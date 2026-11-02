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

from .models import Activity, Appointment, StudentFlag, StudentNote, Semester, MoodEntry, ExerciseSession, DailyQuest, SERVICES
from .courses import get_course_labels
from .permissions import can, require_perm, staff_permissions
from .verification import visible_students

staff_only = user_passes_test(lambda u: u.is_staff or u.is_superuser, login_url='login')
SERVICE_KEYS = [k for k, _ in SERVICES]
MAX_DAYS = 366

EXERCISE_LABELS = {
    "breathing": "Breathing",
    "affirmations": "Affirmations",
    "grounding": "5-4-3-2-1 Grounding",
    "pomodoro": "Pomodoro",
    "micapop": "Mica Pop",
}
EXERCISE_COUNT_LABELS = {"affirmations": "said", "grounding": "finished", "micapop": "popped", "pomodoro": "focus rounds"}


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
def semesters(request):
    return JsonResponse({"semesters": [
        {"id": s.id, "name": s.name, "from": s.start_date.isoformat(), "to": s.end_date.isoformat()}
        for s in Semester.objects.all()
    ]})


@login_required(login_url='login')
@staff_only
@require_perm('view_students')
def service_report(request):
    COURSE_LABELS = get_course_labels()
    start, end = _period(request)
    course = (request.GET.get("course") or "").strip()
    year = (request.GET.get("year") or "").strip()

    students = visible_students()     # archived students still count; unverified ones don't
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

    students = visible_students().filter(
        Q(profile__account_status="active") | Q(profile__isnull=True))
    total = students.count()
    done = students.filter(assessment__completed_at__isnull=False, assessment__skipped=False).count()

    active_ids = set(MoodEntry.objects.filter(
        user__in=students, date__range=(start, end)).values_list("user_id", flat=True))
    totals, hit = {}, {}
    for uid, course in students.values_list("id", "profile__course"):
        totals[course or ""] = totals.get(course or "", 0) + 1
        if uid in active_ids:
            hit[course or ""] = hit.get(course or "", 0) + 1
    checkin_by_course = [
        {"code": k or "Not set", "rate": round(100 * hit.get(k, 0) / v), "students": v}
        for k, v in sorted(totals.items())
    ]

    return JsonResponse({
        "period": {"from": start.isoformat(), "to": end.isoformat()},
        "appointments": appointments,
        "flags": flags,
        "assessment": {"completed": done, "total": total, "rate": round(100 * done / total) if total else None},
        "checkin_by_course": checkin_by_course,
    })


@login_required(login_url='login')
@staff_only
@require_perm('view_students')
def exercise_stats(request):
    """How students use the exercises. Counts and time only, never anything a student wrote."""
    start, end = _period(request)
    t0, t1 = _day_bounds(start, end)
    course = (request.GET.get("course") or "").strip()
    year = (request.GET.get("year") or "").strip()
    student = (request.GET.get("student") or "").strip()

    students = visible_students().filter(
        Q(profile__account_status="active") | Q(profile__isnull=True))
    if course:
        students = students.filter(profile__course=course)
    if year:
        students = students.filter(profile__year_level=year)
    if student.isdigit():
        students = students.filter(id=int(student))
    total_students = students.count()

    ex_filter = (request.GET.get("exercise") or "").strip()
    if ex_filter not in EXERCISE_LABELS:
        ex_filter = ""

    sessions = ExerciseSession.objects.filter(user__in=students, created_at__range=(t0, t1))
    if ex_filter:
        sessions = sessions.filter(exercise=ex_filter)
    rows = sessions.values_list("user_id", "exercise", "duration_seconds", "count", "created_at")

    def blank():
        return {"sessions": 0, "seconds": 0, "count": 0, "users": set(), "last": None}

    by_ex = {k: blank() for k in EXERCISE_LABELS if not ex_filter or k == ex_filter}
    days_by_user = defaultdict(set)
    daily = defaultdict(lambda: defaultdict(int))
    by_hour = [0] * 24
    by_weekday = [0] * 7

    for uid, ex, secs, cnt, when in rows:
        local = timezone.localtime(when)
        day = local.date()
        e = by_ex.setdefault(ex, blank())
        e["sessions"] += 1
        e["seconds"] += secs
        e["count"] += cnt
        e["users"].add(uid)
        if e["last"] is None or day > e["last"]:
            e["last"] = day
        days_by_user[uid].add(day)
        daily[ex][day] += 1
        by_hour[local.hour] += 1
        by_weekday[local.weekday()] += 1

    days = []
    d = start
    while d <= end:
        days.append(d)
        d += timedelta(days=1)

    all_sessions = sum(e["sessions"] for e in by_ex.values())
    all_seconds = sum(e["seconds"] for e in by_ex.values())
    active = len(days_by_user)
    n_days = [len(v) for v in days_by_user.values()]

    q_rows = DailyQuest.objects.filter(
        user__in=students, date__range=(start, end)).values_list("user_id", "date", "done")
    q_assigned, q_done = defaultdict(int), defaultdict(int)
    q_opened, q_finished = set(), set()
    for uid, qday, qdone in q_rows:
        q_assigned[qday] += 1
        q_opened.add(uid)
        if qdone:
            q_done[qday] += 1
            q_finished.add(uid)
    q_assigned_total = sum(q_assigned.values())
    q_done_total = sum(q_done.values())
    quests = {
        "assigned": q_assigned_total,
        "done": q_done_total,
        "rate": round(100 * q_done_total / q_assigned_total) if q_assigned_total else None,
        "students_opened": len(q_opened),
        "students_done": len(q_finished),
        "labels": [x.isoformat() for x in days],
        "done_per_day": [q_done.get(x, 0) for x in days],
        "open_per_day": [q_assigned.get(x, 0) - q_done.get(x, 0) for x in days],
    }

    return JsonResponse({
        "period": {"from": start.isoformat(), "to": end.isoformat()},
        "totals": {
            "sessions": all_sessions,
            "minutes": round(all_seconds / 60, 1),
            "avg_seconds": round(all_seconds / all_sessions) if all_sessions else 0,
            "active_students": active,
            "total_students": total_students,
            "adoption": round(100 * active / total_students) if total_students else None,
        },
        "exercises": [{
            "key": k, "label": EXERCISE_LABELS.get(k, k),
            "sessions": e["sessions"], "students": len(e["users"]),
            "minutes": round(e["seconds"] / 60, 1),
            "avg_seconds": round(e["seconds"] / e["sessions"]) if e["sessions"] else 0,
            "count": e["count"], "count_label": EXERCISE_COUNT_LABELS.get(k, ""),
            "last_used": e["last"].isoformat() if e["last"] else None,
        } for k, e in by_ex.items()],
        "daily": {
            "labels": [x.isoformat() for x in days],
            "series": [{"key": k, "label": EXERCISE_LABELS.get(k, k),
                        "data": [daily[k].get(x, 0) for x in days]} for k in by_ex],
        },
        "frequency": [
            {"label": "Not used", "students": total_students - active},
            {"label": "1 day", "students": sum(1 for n in n_days if n == 1)},
            {"label": "2–3 days", "students": sum(1 for n in n_days if 2 <= n <= 3)},
            {"label": "4+ days", "students": sum(1 for n in n_days if n >= 4)},
        ],
        "by_hour": by_hour,
        "by_weekday": by_weekday,
        "exercise": ex_filter,
        "options": [{"key": k, "label": v} for k, v in EXERCISE_LABELS.items()],
        "quests": quests,
    })

# ---------------------------------------------------------------- period report (export)
@login_required(login_url='login')
@staff_only
@require_perm('view_students')
@require_perm('export_records')
def period_report(request):
    COURSE_LABELS = get_course_labels()
    """Everything the Analytics Excel/PDF needs for one period, except service sessions
    (those come from service_report). No notes, diary text, chats or assessment answers."""
    from .views import _student_risk          # imported late: views imports a lot

    start, end = _period(request)
    t0, t1 = _day_bounds(start, end)
    today = timezone.localdate()
    allowed = _allowed_services(request.user)

    students = visible_students().filter(
        Q(profile__account_status="active") | Q(profile__isnull=True))
    total = students.count()

    # mood + check-ins in the period
    scores, checked, by_course = [], set(), {}
    for uid, mood, course in MoodEntry.objects.filter(
            user__in=students, date__range=(start, end)
    ).values_list("user_id", "mood", "user__profile__course"):
        try:
            s = 5 - int(mood)
        except (TypeError, ValueError):
            continue
        scores.append(s)
        checked.add(uid)
        c = by_course.setdefault(course or "", {"scores": [], "students": set()})
        c["scores"].append(s)
        c["students"].add(uid)

    done = students.filter(assessment__completed_at__isnull=False, assessment__skipped=False).count()

    flags_in_period = StudentFlag.objects.filter(
        Q(created_at__range=(t0, t1)) | Q(resolved_at__range=(t0, t1))
    ).select_related("student", "student__profile", "flagged_by", "assigned_to").order_by("created_at")

    def person(u):
        return (u.get_full_name() or u.username) if u else ""

    def when(dt):
        return timezone.localtime(dt).strftime("%Y-%m-%d %H:%M") if dt else ""

    flag_rows = []
    for f in flags_in_period:
        p = getattr(f.student, "profile", None)
        flag_rows.append({
            "student": person(f.student),
            "student_number": (p.student_number if p and p.student_number else "No ID"),
            "course": COURSE_LABELS.get(p.course, p.course) if p and p.course else "Not set",
            "year": p.year_level if p else "",
            "risk_now": _student_risk(f.student, today),
            "status": f.get_status_display(),
            "opened": when(f.created_at),
            "resolved": when(f.resolved_at),
            "flagged_by": person(f.flagged_by),
            "handled_by": person(f.assigned_to),
            "reason": f.reason,
        })

    appt_rows = []
    for a in Appointment.objects.filter(
            service__in=allowed, scheduled_time__range=(t0, t1)
    ).select_related("student", "student__profile", "counselor").order_by("scheduled_time"):
        p = getattr(a.student, "profile", None)
        local = timezone.localtime(a.scheduled_time)
        appt_rows.append({
            "date": local.strftime("%Y-%m-%d"),
            "time": local.strftime("%I:%M %p"),
            "student": person(a.student),
            "student_number": (p.student_number if p and p.student_number else "No ID"),
            "service": a.get_service_display(),
            "counselor": person(a.counselor),
            "status": a.get_status_display(),
        })

    course_rows = [{
        "course": COURSE_LABELS.get(c, "Not set" if not c else c),
        "avg_mood": round(sum(v["scores"]) / len(v["scores"]), 2),
        "checkins": len(v["scores"]),
        "students": len(v["students"]),
    } for c, v in sorted(by_course.items()) if len(v["students"]) >= 3]    # same 3+ rule as the Mood tab

    return JsonResponse({
        "period": {"from": start.isoformat(), "to": end.isoformat()},
        "as_of": today.isoformat(),
        "summary": {
            "students": total,
            "avg_mood": round(sum(scores) / len(scores), 1) if scores else None,
            "checkin_rate": round(100 * len(checked) / total) if total else None,
            "assessment_done": done,
            "assessment_rate": round(100 * done / total) if total else None,
            "flags_opened": StudentFlag.objects.filter(created_at__range=(t0, t1)).count(),
            "flags_resolved": StudentFlag.objects.filter(resolved_at__range=(t0, t1)).count(),
            "flags_open_now": StudentFlag.objects.exclude(status="resolved").count(),
        },
        "flags": flag_rows,
        "appointments": appt_rows,
        "mood_by_course": course_rows,
    })