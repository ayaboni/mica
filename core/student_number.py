import re

_FORMATS = (
    re.compile(r"^\d{3}-\d{4}$"),        # 000-0000
    re.compile(r"^\d{2}-\d{4}-\d{3}$"),  # 00-0000-000
)


def student_number_problem(value):
    """None if the student number is fine, otherwise a message to show."""
    value = (value or "").strip()
    if not any(f.match(value) for f in _FORMATS):
        return "Student number must look like 000-0000 or 00-0000-000."
    return None