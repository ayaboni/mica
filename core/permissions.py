"""What a staff member may do, decided by the StaffSubclass the admin gave them.

* System admins (is_superuser) can do everything.
* Staff with no subclass keep full access, as before subclasses existed.
* Everyone else gets exactly what their subclass allows.
"""
from functools import wraps

from django.http import JsonResponse

from .models import SERVICES

SERVICE_KEYS = [key for key, _ in SERVICES]

# (field on StaffSubclass, label shown to the admin, kind)
#   kind "bool"     -> a switch
#   kind "services" -> a list of services (Information, Counseling, ...)
PERMISSION_FIELDS = [
    ("view_students", "See students, mood & risk levels", "bool"),
    ("chat_students", "Chat with students", "bool"),
    ("schedule_services", "Schedule appointments for", "services"),
    ("write_note_services", "Write notes for", "services"),
    ("read_note_services", "Read notes for", "services"),
    ("notes_need_review", "Their notes are marked \u201cneeds review\u201d", "bool"),
    ("review_notes", "Mark another staff member\u2019s note reviewed", "bool"),
    ("flag_students", "Flag a student", "bool"),
    ("referrals", "Refer students to other staff & receive referrals", "bool"),
    ("take_cases", "Take a case", "bool"),
    ("resolve_flags", "Resolve a flag", "bool"),
    ("view_assessments", "View student assessments", "bool"),
    ("archive_students", "Archive / reactivate students", "bool"),
    ("export_records", "Export records", "bool"),
    ("generate_codes", "Generate student registration codes", "bool"),
    ("verify_students", "Approve or deactivate unverified student accounts", "bool"),
    ("clear_avatars", "Remove a student\u2019s profile photo", "bool"),
]
BOOL_FIELDS = [f for f, _, k in PERMISSION_FIELDS if k == "bool"]
SERVICE_FIELDS = [f for f, _, k in PERMISSION_FIELDS if k == "services"]


def staff_permissions(user):
    """Return {permission: bool or [services]} for this user."""
    full = {f: True for f in BOOL_FIELDS}
    full.update({f: list(SERVICE_KEYS) for f in SERVICE_FIELDS})
    full["notes_need_review"] = False          # sign-off is only ever *required* of a role that asks for it

    if not user.is_authenticated:
        return {f: ([] if f in SERVICE_FIELDS else False) for f in BOOL_FIELDS + SERVICE_FIELDS}
    if user.is_superuser:
        return full
    sp = getattr(user, "staff_profile", None)
    sub = sp.subclass if sp and sp.subclass_id else None
    if sub is None:
        return full if user.is_staff else {f: ([] if f in SERVICE_FIELDS else False) for f in BOOL_FIELDS + SERVICE_FIELDS}

    perms = {f: bool(getattr(sub, f)) for f in BOOL_FIELDS}
    for f in SERVICE_FIELDS:
        perms[f] = [s for s in (getattr(sub, f) or []) if s in SERVICE_KEYS]
    return perms


def can(user, permission, service=None):
    """True if `user` has `permission` (and, for service lists, covers `service`)."""
    value = staff_permissions(user).get(permission)
    if isinstance(value, list):
        return service in value if service else bool(value)
    return bool(value)


def require_perm(permission):
    """View decorator: answer 403 JSON unless the staff member has this permission."""
    def decorator(view):
        @wraps(view)
        def wrapped(request, *args, **kwargs):
            if not can(request.user, permission):
                return JsonResponse({"error": "Your staff role doesn't allow this."}, status=403)
            return view(request, *args, **kwargs)
        return wrapped
    return decorator


def subclass_json(sub, member_count=None):
    data = {"id": sub.id, "name": sub.name, "description": sub.description,
            "members": sub.members.count() if member_count is None else member_count}
    for f in BOOL_FIELDS:
        data[f] = bool(getattr(sub, f))
    for f in SERVICE_FIELDS:
        data[f] = [s for s in (getattr(sub, f) or []) if s in SERVICE_KEYS]
    return data
