"""Password-reset e-mails sent by Firebase, finished on MICA's own page.

How it fits together
--------------------
1. A student asks for a reset. We make sure the e-mail address exists as a
   Firebase Authentication user (a throwaway mirror of the local account), then
   ask Firebase to send its "Password reset" e-mail to that address.
2. The link in that e-mail opens MICA's own page. Either Firebase sends the e-mail and
   its "Password reset" template is set to link to MICA's page (customize action URL
   in the Firebase console), or, when that setting can't be changed, MICA sends the
   e-mail itself with a code Firebase generated (PASSWORD_RESET_EMAIL_VIA = "mica").
   The page asks for the new password twice.
3. We hand the one-time code and the new password to Firebase, which checks the
   code and tells us which e-mail it belongs to. Only then do we change the
   password of the matching local account(s).

All calls to Firebase's REST API go through ``_post`` so errors come back as a
single ``FirebaseResetError`` carrying Firebase's error code (for example
``EXPIRED_OOB_CODE``).
"""
import json
import logging
import secrets
import urllib.error
import urllib.parse
import urllib.request

from django.conf import settings
from firebase_admin import auth as firebase_auth

logger = logging.getLogger(__name__)

API_ROOT = getattr(settings, "FIREBASE_IDENTITY_TOOLKIT_URL", "https://identitytoolkit.googleapis.com/v1")
REQUEST_TIMEOUT = 10  # seconds


class FirebaseResetError(Exception):
    """Something went wrong talking to Firebase. ``code`` is Firebase's error code when it gave one."""

    def __init__(self, code, detail=""):
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


def _post(endpoint, body, referer=None):
    """POST JSON to a Firebase Identity Toolkit endpoint and return the parsed reply."""
    url = f"{API_ROOT}/{endpoint}?key={urllib.parse.quote(settings.FIREBASE_WEB_API_KEY)}"
    headers = {"Content-Type": "application/json"}
    if referer:
        # Web API keys are often restricted to certain sites; sending the site's own
        # address as the referrer keeps those restrictions working for server calls.
        headers["Referer"] = referer
    request = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=REQUEST_TIMEOUT) as response:
            return json.loads(response.read().decode() or "{}")
    except urllib.error.HTTPError as exc:
        try:
            message = json.loads(exc.read().decode()).get("error", {}).get("message", "")
        except (ValueError, AttributeError):
            message = ""
        # Firebase messages look like "WEAK_PASSWORD : Password should be at least 6 characters"
        code = (message.split(":")[0].strip() or f"HTTP_{exc.code}")
        raise FirebaseResetError(code, message) from exc
    except (urllib.error.URLError, TimeoutError, OSError) as exc:
        raise FirebaseResetError("NETWORK", str(exc)) from exc


def ensure_firebase_user(email):
    """Return the Firebase user for this e-mail, creating a mirror user if there isn't one.

    Firebase only sends reset e-mails to addresses it knows. The mirror gets a long
    random password nobody knows; it exists only so Firebase has someone to e-mail.
    """
    try:
        return firebase_auth.get_user_by_email(email)
    except firebase_auth.UserNotFoundError:
        pass
    except Exception as exc:
        raise FirebaseResetError("LOOKUP_FAILED", str(exc)) from exc
    try:
        return firebase_auth.create_user(email=email, password=secrets.token_urlsafe(32), email_verified=False)
    except Exception as exc:
        raise FirebaseResetError("PROVISION_FAILED", str(exc)) from exc


def send_password_reset_email(email, referer=None):
    """Have Firebase e-mail a password-reset link to ``email``."""
    ensure_firebase_user(email)
    _post("accounts:sendOobCode", {"requestType": "PASSWORD_RESET", "email": email}, referer)


def generate_reset_code(email):
    """Have Firebase create a one-time reset code *without* e-mailing anyone.

    Used when MICA sends the e-mail itself (settings.PASSWORD_RESET_EMAIL_VIA = "mica").
    That way the link can point straight at MICA's reset page, and nothing has to be
    changed in the Firebase console's e-mail template settings.
    """
    ensure_firebase_user(email)
    try:
        link = firebase_auth.generate_password_reset_link(email)
    except Exception as exc:
        raise FirebaseResetError("GENERATE_FAILED", str(exc)) from exc
    code = urllib.parse.parse_qs(urllib.parse.urlparse(link).query).get("oobCode", [""])[0]
    if not code:
        raise FirebaseResetError("NO_CODE", "Firebase's reset link did not contain a code.")
    return code


def confirm_password_reset(oob_code, new_password, referer=None):
    """Spend the one-time code and set the Firebase password. Returns the e-mail the code was for.

    Raises FirebaseResetError("INVALID_OOB_CODE") / ("EXPIRED_OOB_CODE") for bad links.
    """
    reply = _post("accounts:resetPassword", {"oobCode": oob_code, "newPassword": new_password}, referer)
    email = (reply.get("email") or "").strip()
    if not email:
        raise FirebaseResetError("NO_EMAIL", "Firebase did not say which account the code was for.")
    return email


def scramble_password(email):
    """Best effort: replace the mirror's password with a random one.

    The real password lives in MICA's database; Firebase has no reason to hold a
    second working copy of it.
    """
    try:
        user = firebase_auth.get_user_by_email(email)
        firebase_auth.update_user(user.uid, password=secrets.token_urlsafe(32))
    except Exception:
        logger.warning("Could not scramble the Firebase mirror password for a reset account.", exc_info=True)
