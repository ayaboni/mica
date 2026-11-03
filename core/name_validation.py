"""Validation for people's names (students, staff).

Names end up in dashboards, chat headers, emails, exports and notes, so they are kept to a
safe alphabet: letters (any language), combining marks, spaces and . , ' ’ -.
That covers names like "Ma. Cristina de la Cruz", "O'Brien", "Santos-Reyes", "Peña", "Jr.".
It deliberately rejects < > " & / \\ ` { } digits, emoji, control and zero-width characters.

This is defence in depth: the dashboards also escape names when rendering.
No Django imports on purpose, so it can be unit-tested on its own.
"""
import datetime
import re
import unicodedata

NAME_MAX_LENGTH = 150          # matches User.first_name / User.last_name
NICKNAME_MAX_LENGTH = 50       # matches UserProfile.nickname
EMERGENCY_NAME_MAX_LENGTH = 100    # matches UserProfile.emergency_contact_name
PHONE_MAX_LENGTH = 20          # matches UserProfile.contact_number / emergency_contact_number
_ALLOWED_PUNCTUATION = frozenset(" .,'\u2019-")


def normalize_name(value):
    """NFC-normalise and collapse every run of whitespace to one space."""
    text = unicodedata.normalize("NFC", str(value or ""))
    return " ".join(text.split())


def _char_allowed(ch):
    return unicodedata.category(ch)[0] in ("L", "M") or ch in _ALLOWED_PUNCTUATION


def validate_person_name(value, label="name", required=True, max_length=NAME_MAX_LENGTH):
    """Return an error message, or None if the name is acceptable."""
    text = normalize_name(value)
    if not text:
        return "Please enter your %s." % label if required else None
    if len(text) > max_length:
        return "Your %s is too long (%d characters max)." % (label, max_length)
    if not any(unicodedata.category(ch)[0] == "L" for ch in text):
        return "Your %s must contain at least one letter." % label
    if not all(_char_allowed(ch) for ch in text):
        return "Your %s can only contain letters, spaces and . , ' -" % label
    if has_blocked_term(text):
        return "Please use your real %s." % label
    return None


def validate_person_names(first, last, require_last=True):
    """Check a first and last name together; return the first error found, or None."""
    return (validate_person_name(first, "first name", required=True)
            or validate_person_name(last, "last name", required=require_last))


_PHONE_PUNCTUATION = frozenset(" +-().")


def validate_phone_number(value, label="phone number", required=False, max_length=PHONE_MAX_LENGTH):
    """Return an error message, or None if `value` looks like a phone number.

    Deliberately loose (numbers are typed in many ways: 0917 123 4567, +63 917 123 4567, (02) 8123-4567):
    only digits, spaces and + - ( ) . are allowed, with at least 7 digits."""
    text = normalize_name(value)
    if not text:
        return "Please enter your %s." % label if required else None
    if len(text) > max_length:
        return "Your %s is too long (%d characters max)." % (label, max_length)
    if not all(ch.isascii() and (ch.isdigit() or ch in _PHONE_PUNCTUATION) for ch in text):
        return "Your %s can only contain digits, spaces and + - ( ) ." % label
    if sum(ch.isdigit() for ch in text) < 7:
        return "Your %s looks too short." % label
    return None


# -- Birthdays ---------------------------------------------------------------
# Optional everywhere. The browser's date picker sends YYYY-MM-DD; that is the only format accepted.
BIRTHDAY_MIN_AGE = 10          # younger than this is almost certainly a typo
BIRTHDAY_MAX_AGE = 100
_BIRTHDAY_FORMAT = re.compile(r"^\d{4}-\d{2}-\d{2}$")
BIRTHDAY_PROBLEM = "Please check your birthday: that date doesn't look right."


def _years_before(day, years):
    try:
        return day.replace(year=day.year - years)
    except ValueError:              # 29 February in a non-leap year
        return day.replace(year=day.year - years, day=28)


def birthday_bounds(today=None):
    """(earliest, latest) allowed birthdays as dates, for the date picker's min/max and the checks."""
    today = today or datetime.date.today()
    return _years_before(today, BIRTHDAY_MAX_AGE), _years_before(today, BIRTHDAY_MIN_AGE)


def validate_birthday(value, today=None):
    """Return (date, None) for a good birthday, (None, None) for blank (it is optional), or
    (date_or_None, message) for a bad one. The date is only filled in when the text was a real date
    that is out of range, so a caller can let an already-saved old value through unchanged."""
    text = str(value or "").strip()
    if not text:
        return None, None
    if not _BIRTHDAY_FORMAT.match(text):
        return None, BIRTHDAY_PROBLEM
    try:
        day = datetime.date.fromisoformat(text)
    except ValueError:
        return None, BIRTHDAY_PROBLEM
    earliest, latest = birthday_bounds(today)
    if day < earliest or day > latest:
        return day, BIRTHDAY_PROBLEM
    return day, None


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
    if has_blocked_term(value):
        return "Please choose a different username."
    return None


# -- Blocked words ------------------------------------------------------------
# Used for names, nicknames, usernames and email addresses. Keep each word in lowercase letters only.
#
# BLOCKED_ANYWHERE: blocked even when buried inside a longer run of letters (so "xxfuckxx" or "f.u.c.k" are caught).
#   Only put words here that never appear inside a real name or word.
# BLOCKED_WHOLE: blocked only as a separate word ("Shit" is blocked, "Shitara" and "Essex" are not).
#   Use this for anything that can sit inside a real name (Chinkee, Fukuda, Kikuchi...).
#   Note: doubled letters are collapsed before matching, so never add a word like "ass" (it would become "as").
BLOCKED_ANYWHERE = [
    # profanity (English)
    "fuck", "bitch", "asshole", "motherfucker", "bullshit", "whore", "dumbass", "jackass", "cocksucker",
    "blowjob", "dickhead", "shithead", "shitty", "pedophile", "paedophile", "killyourself",
    # profanity (Tagalog)
    "putangina", "tangina", "pakyu", "pakshet", "bwisit", "buwisit", "tarantado", "kantot", "jakol", "pekpek",
    "pokpok", "punyeta", "hinayupak", "potangina", "kupal", "gunggong",
    # racist, homophobic, ableist and hateful slurs
    "nigger", "nigga", "faggot", "wetback", "towelhead", "raghead", "chingchong", "whitetrash", "retarded",
    # hate figures
    "hitler",
]
BLOCKED_WHOLE = [
    # profanity
    "shit", "dick", "cock", "cunt", "pussy", "slut", "penis", "vagina", "dildo", "nazi", "rape", "rapist", "porn", "sex",
    "fuk", "fuq", "fvck", "fck", "fcuk", "phuck", "phuk", "fuking", "kys", "bastard", "wanker", "twat", "tits", "horny", "milf",
    "hentai", "orgasm", "masturbate", "molester", "pedo", "anus", "boner", "handjob", "nude", "nudes",
    "gago", "bobo", "tanga", "ulol", "engot", "puta", "puki", "burat", "bayag", "tamod", "hindot", "hayop", "demonyo", "bastos",
    "pota", "iyot", "ungas",
    # racist, homophobic, ableist and hateful slurs
    "fag", "fags", "faggy", "retard", "tranny", "chink", "chinks", "gook", "gooks", "spic", "spick", "kike", "kikes",
    "paki", "pakis", "beaner", "beaners", "darkie", "darky", "intsik", "bumbay", "heil", "terrorist",
]

try:    # optional: pip install better-profanity (test it against real surnames first)
    from better_profanity import profanity as _profanity
    _profanity.load_censor_words()
except ImportError:
    _profanity = None

_LEET = str.maketrans({"0": "o", "1": "i", "3": "e", "4": "a", "5": "s",
                       "7": "t", "8": "b", "9": "g", "@": "a", "$": "s", "!": "i", "+": "t"})


def _plain(value):
    """Lowercase, accents removed."""
    text = unicodedata.normalize("NFKD", str(value or "")).lower()
    return "".join(ch for ch in text if not unicodedata.combining(ch))


def _fold(value):
    """Like _plain, and swaps look-alike symbols for letters (f4ck -> fack, $hit -> shit)."""
    return _plain(value).translate(_LEET)


def _collapse(word):
    return re.sub(r"(.)\1+", r"\1", word)


# "n+i+g+...", so stretched spellings (niiigger) are caught too
_ANYWHERE = [re.compile("".join(re.escape(c) + "+" for c in w)) for w in BLOCKED_ANYWHERE]
_WHOLE = {_collapse(w) for w in BLOCKED_WHOLE}


def has_blocked_term(value):
    """True if the text contains an offensive word, even with symbols, spaces or repeated letters."""
    words = re.findall(r"[a-z]+", _fold(value))
    # The same words read without the symbol swaps, so "dick123" or "sex!" are still seen as "dick" and "sex"
    # (the swaps would turn them into "dicki" and "sexi").
    plain_words = re.findall(r"[a-z]+", _plain(value))
    joined = "".join(words)
    if any(p.search(joined) for p in _ANYWHERE):
        return True
    if any(_collapse(w) in _WHOLE for w in words + plain_words):
        return True
    return bool(_profanity and _profanity.contains_profanity(" ".join(words)))


# -- Email addresses ------------------------------------------------------------
EMAIL_BLOCKED_MESSAGE = "Please use a different email address."


def email_has_blocked_term(email):
    """True if an email address contains an offensive word, in the part before the @ or in the domain."""
    text = str(email or "").strip()
    local, at, domain = text.rpartition("@")
    if not at:
        local, domain = text, ""
    return has_blocked_term(local) or has_blocked_term(domain)


def validate_email_wording(email):
    """Return an error message if the email address contains offensive wording, otherwise None.
    (Whether the address is well formed is checked separately, with Django's validate_email.)"""
    return EMAIL_BLOCKED_MESSAGE if email_has_blocked_term(email) else None
