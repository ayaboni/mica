"""Crisis hotlines shown to students. The admin edits them in System Settings.
If nothing is saved (or the database can't be read) the built-in numbers are used,
so a student in crisis never gets an empty list."""
import re

from .models import SiteSettings

MAX_HOTLINES = 8
NUMBER_RE = re.compile(r"^[0-9A-Za-z+()\-.,/ ]{3,80}$")
NOTE_RE = re.compile(r"^[0-9A-Za-z+()\-.,/ ]{0,60}$")

DEFAULT_HOTLINES = [
    {"name": "NCMH Crisis Hotline", "number": "1553 (landline), 0917-899-8727 or 0919-057-1553", "note": "24/7"},
    {"name": "Hopeline PH", "number": "2919 (Globe/TM, toll-free) or 0917-558-4673", "note": "24/7"},
]


def saved_hotlines():
    try:
        row = SiteSettings.objects.filter(pk=1).first()
        rows = row.hotlines if row else []
    except Exception:
        return []
    good = []
    for h in rows if isinstance(rows, list) else []:
        if isinstance(h, dict) and str(h.get("name") or "").strip() and str(h.get("number") or "").strip():
            good.append({"name": str(h["name"]).strip(), "number": str(h["number"]).strip(),
                         "note": str(h.get("note") or "").strip()})
    return good


def get_hotlines():
    return saved_hotlines() or [dict(h) for h in DEFAULT_HOTLINES]


def crisis_resources_text():
    """The bullet list Mica shows in a crisis reply."""
    lines = []
    for h in get_hotlines():
        note = " (%s)" % h["note"] if h["note"] else ""
        lines.append("• %s%s: %s" % (h["name"], note, h["number"]))
    lines.append("• In immediate danger: call 911 or go to the nearest hospital")
    lines.append("• You can also message the Guidance Office through 'Talk to Guidance' here in MICA.")
    return "\n".join(lines)


def hotlines_inline():
    return "; ".join("%s %s" % (h["name"], h["number"]) for h in get_hotlines())