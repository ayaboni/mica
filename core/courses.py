from .models import College, Course, COURSE_GROUPS as DEFAULT_GROUPS


def get_course_groups():
    """[(college name, [(code, name), ...]), ...] from the database, active only."""
    groups = []
    for college in College.objects.filter(is_active=True):
        items = list(
            Course.objects.filter(college=college, is_active=True)
            .values_list("code", "name")
        )
        if items:
            groups.append((college.name, items))
    return groups or DEFAULT_GROUPS   # safety net if the tables are empty


def get_courses():
    """Flat [(code, name), ...] list."""
    return [c for _college, items in get_course_groups() for c in items]


def get_course_labels():
    """{code: name} for ALL courses, hidden ones too."""
    labels = dict(Course.objects.values_list("code", "name"))
    return labels or dict(c for _college, items in DEFAULT_GROUPS for c in items)