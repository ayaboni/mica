"""Validation for people's names (students, staff).

Names end up in dashboards, chat headers, emails, exports and notes, so they are kept to a
safe alphabet: letters (any language), combining marks, spaces and . , ' ’ -.
That covers names like "Ma. Cristina de la Cruz", "O'Brien", "Santos-Reyes", "Peña", "Jr.".
It deliberately rejects < > " & / \\ ` { } digits, emoji, control and zero-width characters.

This is defence in depth: the dashboards also escape names when rendering.
No Django imports on purpose, so it can be unit-tested on its own.
"""
import re
import unicodedata

NAME_MAX_LENGTH = 150          # matches User.first_name / User.last_name
_ALLOWED_PUNCTUATION = frozenset(" .,'\u2019-")


def normalize_name(value):
    """NFC-normalise and collapse every run of whitespace to one space."""
    text = unicodedata.normalize("NFC", str(value or ""))
    return " ".join(text.split())


def _char_allowed(ch):
    return unicodedata.category(ch)[0] in ("L", "M") or ch in _ALLOWED_PUNCTUATION


def validate_person_name(value, label="name", required=True):
    """Return an error message, or None if the name is acceptable."""
    text = normalize_name(value)
    if not text:
        return "Please enter your %s." % label if required else None
    if len(text) > NAME_MAX_LENGTH:
        return "Your %s is too long (%d characters max)." % (label, NAME_MAX_LENGTH)
    if not any(unicodedata.category(ch)[0] == "L" for ch in text):
        return "Your %s must contain at least one letter." % label
    if not all(_char_allowed(ch) for ch in text):
        return "Your %s can only contain letters, spaces and . , ' -" % label
    return None


def validate_person_names(first, last, require_last=True):
    """Check a first and last name together; return the first error found, or None."""
    return (validate_person_name(first, "first name", required=True)
            or validate_person_name(last, "last name", required=require_last))


def sanitize_person_name(value):
    """For names we don't get to choose (e.g. a Google profile): drop disallowed characters."""
    text = "".join(ch for ch in normalize_name(value) if _char_allowed(ch))
    return normalize_name(text)[:NAME_MAX_LENGTH].strip()


# -- Usernames ---------------------------------------------------------------
# One strict rule for everyone (it is the rule staff accounts already used):
# 3-30 characters, English letters, digits, dot, dash, underscore, starting with a letter or
# digit. Django's default also allows other alphabets (look-alike letters can impersonate
# another person) and "@" / "+" (which makes a username look like an email address).
USERNAME_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{2,29}")
USERNAME_RULES_MESSAGE = ("Username must be 3\u201330 characters: English letters, numbers, dots, "
                          "dashes or underscores, starting with a letter or number.")


def validate_username(value):
    """Return an error message, or None if `value` is an acceptable new username."""
    if not USERNAME_PATTERN.fullmatch(value or ""):
        return USERNAME_RULES_MESSAGE
    return None
