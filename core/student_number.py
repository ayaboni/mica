import re

# [0-9], not \d: in Python \d also matches digits from other scripts, which are not real student numbers.
_FORMATS = (
    re.compile(r"^[0-9]{3}-[0-9]{4}$"),        # 000-0000
    re.compile(r"^[0-9]{2}-[0-9]{4}-[0-9]{3}$"),  # 00-0000-000
)

# Dash look-alikes that arrive when a number is pasted from a PDF, a chat or a spreadsheet.
_DASHES = dict.fromkeys(map(ord, "\u2010\u2011\u2012\u2013\u2014\u2212\uff0d"), "-")
_DIGITS_ONLY = re.compile(r"^[0-9]+$")


def normalize_student_number(value):
    """Put a typed student number into its standard dashed form.

    The iPhone number pad has no "-" key, so students can only type digits: 7 digits become
    000-0000 and 9 digits become 00-0000-000. Spaces are dropped and dash look-alikes become
    "-". Anything else comes back cleaned but unchanged, so student_number_problem() still
    rejects it. Call this on the raw input before validating or saving."""
    value = re.sub(r"\s+", "", (value or "").translate(_DASHES))
    if _DIGITS_ONLY.match(value):
        if len(value) == 7:
            return value[:3] + "-" + value[3:]
        if len(value) == 9:
            return value[:2] + "-" + value[2:6] + "-" + value[6:]
    return value


def student_number_problem(value):
    """None if the student number is fine, otherwise a message to show."""
    value = (value or "").strip()
    if not any(f.match(value) for f in _FORMATS):
        return "Student number must look like 000-0000 or 00-0000-000."
    return None
