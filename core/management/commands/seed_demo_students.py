"""Add clearly marked PLACEHOLDER students with mood-calendar logs (for demos), or remove them again.

Put this file at  core/management/commands/seed_demo_students.py  and run:

    python manage.py seed_demo_students --count 120 --days 90
    python manage.py seed_demo_students --purge

What it makes
-------------
* Accounts named demo.001, demo.002 ... with e-mail demo.001@demo.invalid. Both parts are the
  marker --purge uses, so a real student who happens to be called "demo.x" is never touched.
* The password is unusable and there is no Firebase link, so nobody can sign in as them and no
  e-mail is ever sent to them.
* Student numbers start with 99- (e.g. 99-0001-001). Any number already taken is skipped.
* Each student gets a registration date inside the period and a mood history from then on. Moods
  are stored as the app stores them: "0" great, "1" good, "2" okay, "3" low, "4" struggling.
  Most students are steady, some swing, a few drift downward so staff charts show something.
* One entry is written to the audit log ("Admin change") each time it seeds or purges.

On the live site
----------------
These rows are indistinguishable from real data in reports, CSV exports and counts. The command
refuses to run when DEBUG is off unless you pass --allow-production, and --purge removes
everything it made.
"""
import random
from datetime import datetime, time, timedelta

from django.conf import settings
from django.contrib.auth.hashers import make_password
from django.contrib.auth.models import User
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction
from django.utils import timezone

from core.courses import get_courses
from core.models import AuditEvent, MoodEntry, UserProfile
from core.views import CONSENT_VERSION

USERNAME_PREFIX = "demo."
EMAIL_DOMAIN = "demo.invalid"
NUMBER_PREFIX = "99-"

FIRST_NAMES = [
    "Ana", "Bea", "Carlo", "Dennis", "Elisa", "Franco", "Gina", "Hannah", "Ian", "Jasmine",
    "Kyle", "Lara", "Marco", "Nina", "Oscar", "Paula", "Quinn", "Rico", "Sofia", "Tomas",
    "Uma", "Vince", "Wendy", "Xavier", "Yna", "Zeke", "Angela", "Brian", "Carmela", "Daniel",
    "Eliza", "Gabriel", "Isabel", "Jericho", "Katrina", "Luis", "Mika", "Nathan", "Patricia", "Rafael",
]
LAST_NAMES = [
    "Aquino", "Bautista", "Castillo", "Dela Cruz", "Estrada", "Flores", "Garcia", "Hernandez",
    "Ignacio", "Jimenez", "Lopez", "Mendoza", "Navarro", "Ocampo", "Pascual", "Quiambao",
    "Ramos", "Santos", "Torres", "Uy", "Valdez", "Villanueva", "Zamora", "Reyes", "Cruz",
    "Domingo", "Fernandez", "Gonzales", "Lim", "Tan",
]
# A few generic notes so the calendar's note view isn't empty. Most entries have none.
NOTES = [
    "Busy day.", "Long commute.", "Quiz went well.", "Tired.", "Hung out with friends.",
    "Group project stress.", "Slept late.", "Good day overall.", "Deadlines this week.", "Rested at home.",
]


def _clamp(value):
    return max(0, min(4, int(round(value))))


class Command(BaseCommand):
    help = "Create (or with --purge, remove) clearly marked placeholder students with mood logs."

    def add_arguments(self, parser):
        parser.add_argument("--count", type=int, default=120, help="How many students to add (default 120).")
        parser.add_argument("--days", type=int, default=90, help="How many days of history (default 90).")
        parser.add_argument("--seed", type=int, default=None, help="Fix the random seed for repeatable data.")
        parser.add_argument("--purge", action="store_true", help="Delete every placeholder student and exit.")
        parser.add_argument("--allow-production", action="store_true",
                            help="Needed to run when DEBUG is off (for example on Render).")

    # ------------------------------------------------------------------ entry point
    def handle(self, *args, **options):
        if settings.PRODUCTION and not options["allow_production"]:
            raise CommandError(
                "DEBUG is off, so this looks like the live site. Placeholder students would show up in "
                "real reports and exports. Run it against a separate demo database, or pass "
                "--allow-production if you really mean it (and --purge afterwards).")
        if options["purge"]:
            return self._purge()
        count, days = options["count"], options["days"]
        if not 1 <= count <= 2000:
            raise CommandError("--count must be between 1 and 2000.")
        if not 7 <= days <= 365:
            raise CommandError("--days must be between 7 and 365.")
        self._seed(count, days, random.Random(options["seed"]))

    # ------------------------------------------------------------------ purge
    def _demo_users(self):
        return User.objects.filter(username__startswith=USERNAME_PREFIX, email__iendswith="@" + EMAIL_DOMAIN,
                                   is_staff=False, is_superuser=False)

    @transaction.atomic
    def _purge(self):
        users = self._demo_users()
        n = users.count()
        if not n:
            self.stdout.write("No placeholder students found. Nothing to remove.")
            return
        users.delete()      # profiles and mood entries go with them (cascade)
        AuditEvent.objects.create(actor=None, event="admin_action",
                                  detail="Removed %d placeholder students from the server" % n)
        self.stdout.write(self.style.SUCCESS("Removed %d placeholder students and their mood logs." % n))

    # ------------------------------------------------------------------ seed
    def _seed(self, count, days, rng):
        today = timezone.localdate()
        first_day = today - timedelta(days=days - 1)
        courses = [code for code, _name in get_courses()] or ["BSIT"]

        existing = list(User.objects.filter(username__startswith=USERNAME_PREFIX).values_list("username", flat=True))
        next_index = 1 + max((int(u.rsplit(".", 1)[1]) for u in existing if u.rsplit(".", 1)[1].isdigit()), default=0)
        taken_numbers = set(UserProfile.objects.filter(student_number__startswith=NUMBER_PREFIX)
                            .values_list("student_number", flat=True))
        unusable = make_password(None)

        plan = []       # one dict per student: everything needed to build rows
        for i in range(count):
            n = next_index + i
            number = None
            while number is None or number in taken_numbers:
                number = "%s%04d-%03d" % (NUMBER_PREFIX, rng.randint(0, 9999), rng.randint(0, 999))
            taken_numbers.add(number)
            joined = first_day + timedelta(days=int(rng.triangular(0, days - 1, 0)))     # most joined earlier
            plan.append({
                "username": "%s%03d" % (USERNAME_PREFIX, n),
                "first": rng.choice(FIRST_NAMES), "last": rng.choice(LAST_NAMES),
                "number": number, "course": rng.choice(courses), "year": str(rng.randint(1, 4)),
                "joined": joined,
                # mood personality: baseline on the 0 (great) to 4 (struggling) scale, and how often they log
                "kind": rng.choices(["steady", "swingy", "declining"], weights=[72, 20, 8])[0],
                "baseline": rng.choice([1, 1, 1.5, 2, 2, 2.5]),
                "log_rate": rng.choice([0.9, 0.7, 0.5, 0.3, 0.15]),
            })

        with transaction.atomic():
            User.objects.bulk_create([
                User(username=p["username"], email="%s@%s" % (p["username"], EMAIL_DOMAIN),
                     first_name=p["first"], last_name=p["last"], password=unusable, is_active=True,
                     date_joined=timezone.make_aware(datetime.combine(p["joined"], time(9, 0))))
                for p in plan])
            ids = dict(User.objects.filter(username__in=[p["username"] for p in plan]).values_list("username", "id"))

            UserProfile.objects.bulk_create([
                UserProfile(user_id=ids[p["username"]], student_number=p["number"], course=p["course"],
                            year_level=p["year"], account_status="active", verification_status="verified",
                            consent_given_at=timezone.make_aware(datetime.combine(p["joined"], time(9, 5))),
                            consent_version=CONSENT_VERSION, has_seen_tutorial=True, has_seen_mood_tutorial=True,
                            email_verified_at=timezone.make_aware(datetime.combine(p["joined"], time(9, 1))))
                for p in plan])

            entries = []
            for p in plan:
                span = max(1, (today - p["joined"]).days)
                for offset in range((today - p["joined"]).days + 1):
                    day = p["joined"] + timedelta(days=offset)
                    if rng.random() > p["log_rate"]:
                        continue
                    score = p["baseline"] + rng.gauss(0, 0.6 if p["kind"] != "swingy" else 1.1)
                    if p["kind"] == "declining":
                        score += 1.6 * offset / span             # drifts toward "low" / "struggling"
                    score += {0: 0.3, 4: -0.3, 5: -0.3}.get(day.weekday(), 0)     # Monday worse, Fri/Sat better
                    entries.append(MoodEntry(user_id=ids[p["username"]], date=day, mood=str(_clamp(score)),
                                             note=rng.choice(NOTES) if rng.random() < 0.06 else ""))
            MoodEntry.objects.bulk_create(entries, batch_size=1000)

            AuditEvent.objects.create(
                actor=None, event="admin_action",
                detail="Added %d placeholder students with %d mood entries from the server" % (count, len(entries)))

        self.stdout.write(self.style.SUCCESS(
            "Added %d placeholder students (demo.%03d to demo.%03d) with %d mood entries over %d days."
            % (count, next_index, next_index + count - 1, len(entries), days)))
        self.stdout.write("Remove them any time with:  python manage.py seed_demo_students --purge")
