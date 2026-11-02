import re

MIN_DIGITS = 7   # change these two once you confirm the real ID length
MAX_DIGITS = 9

_ALLOWED = re.compile(r"^[0-9]+(?:-[0-9]+)*$")   # digits, with single dashes between groups


def student_number_problem(value):
    """None if the student number is fine, otherwise a message to show."""
    value = (value or "").strip()
    if not _ALLOWED.match(value):
        return "Student number can only have numbers and dashes."
    digits = len(value.replace("-", ""))   # dashes don't count
    if not MIN_DIGITS <= digits <= MAX_DIGITS:
        if MIN_DIGITS == MAX_DIGITS:
            return "Student number must have %d digits." % MIN_DIGITS
        return "Student number must have %d to %d digits." % (MIN_DIGITS, MAX_DIGITS)
    return None