"""The "System" tab on the staff and admin dashboards: what this sign-in looks like from the
server's side (browser, operating system, address, session, two-factor).

Only things about the *viewer's own* connection and account are returned. Nothing about the
server's software, database or environment, so a staff account never learns anything about the
infrastructure. The browser adds a few more details in the page itself (screen, language, time
zone) from `static/core/js/system-info.js`, which never leave the browser.
"""
import re

from django.utils import timezone

from . import twofactor
from .middleware import STAFF_IDLE_SECONDS
from .net import client_ip

# Checked in order: Edge/Opera/Samsung/Firefox/Chrome all contain "Chrome" or "Safari" too.
_BROWSERS = [
    ("Edge", r"Edg(?:e|A|iOS)?/([\d.]+)"),
    ("Opera", r"OPR/([\d.]+)"),
    ("Samsung Internet", r"SamsungBrowser/([\d.]+)"),
    ("Firefox", r"(?:Firefox|FxiOS)/([\d.]+)"),
    ("Chrome", r"(?:Chrome|CriOS)/([\d.]+)"),
    ("Safari", r"Version/([\d.]+).*Safari/"),
]

_SYSTEMS = [
    ("Windows", r"Windows NT ([\d.]+)"),
    ("Android", r"Android ([\d.]+)"),
    ("iOS", r"(?:iPhone|iPad|iPod).*? OS ([\d_]+)"),
    ("macOS", r"Mac OS X ([\d_]+)"),
    ("ChromeOS", r"CrOS"),
    ("Linux", r"Linux|X11"),
]

_WINDOWS_NAMES = {"10.0": "10 / 11", "6.3": "8.1", "6.2": "8", "6.1": "7"}
_UNKNOWN = "Unknown"


def _major(version):
    return version.split(".")[0] if version else ""


def parse_user_agent(ua):
    """(browser, operating system, device type) as short readable strings. Never raises."""
    ua = (ua or "")[:512]

    browser = _UNKNOWN
    for name, pattern in _BROWSERS:
        m = re.search(pattern, ua)
        if m:
            browser = "%s %s" % (name, _major(m.group(1))) if m.group(1) else name
            break

    system = _UNKNOWN
    for name, pattern in _SYSTEMS:
        m = re.search(pattern, ua)
        if not m:
            continue
        version = m.group(1) if m.groups() else ""
        if name == "Windows":
            version = _WINDOWS_NAMES.get(version, version)
        system = ("%s %s" % (name, version.replace("_", "."))).strip()
        break

    if re.search(r"iPad|Tablet", ua) or (re.search(r"Android", ua) and not re.search(r"Mobile", ua)):
        device = "Tablet"
    elif re.search(r"Mobi|iPhone|iPod|Android", ua):
        device = "Phone"
    elif ua:
        device = "Computer"
    else:
        device = _UNKNOWN
    return browser, system, device


def _role(user):
    if user.is_superuser:
        return "System Admin"
    sp = getattr(user, "staff_profile", None)
    try:
        return sp.get_role_display() if sp else "Guidance Staff"
    except Exception:
        return "Guidance Staff"


def system_info(request):
    """Rows for the System tab, as a list of (label, value) pairs grouped into sections."""
    user = request.user
    browser, system, device = parse_user_agent(request.META.get("HTTP_USER_AGENT"))

    if user.is_superuser:
        if not twofactor.required():
            two_factor = "Not required"
        else:
            two_factor = "On (authenticator app)" if twofactor.has_confirmed_device(user) else "Not set up yet"
    else:
        two_factor = "Not used for staff accounts"

    last_login = user.last_login
    session_ends = None
    try:
        session_ends = request.session.get_expiry_date()
    except Exception:
        pass

    def when(dt):
        return timezone.localtime(dt).strftime("%b %d, %Y, %I:%M %p").replace(" 0", " ") if dt else "—"

    return {
        "sections": [
            {"title": "This device", "rows": [
                ("Browser", browser),
                ("Operating system", system),
                ("Device type", device),
                ("IP address", client_ip(request) or _UNKNOWN),
            ]},
            {"title": "Your account", "rows": [
                ("Username", user.get_username()),
                ("Role", _role(user)),
                ("Two-factor sign-in", two_factor),
                ("Last sign-in", when(last_login)),
            ]},
            {"title": "This session", "rows": [
                ("Server time", when(timezone.now())),
                ("Signs out after idle", "%d minutes" % (STAFF_IDLE_SECONDS // 60)),
                ("Session cookie expires", when(session_ends)),
            ]},
        ],
        "user_agent": (request.META.get("HTTP_USER_AGENT") or "")[:512],
    }
