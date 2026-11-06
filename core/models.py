from django.db import models
from django.db.models.functions import Lower
from django.contrib.auth.models import User
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone
from django.conf import settings
from django.core.exceptions import ValidationError

ACCOUNT_STATUS = [
    ("active", "Active"),
    ("inactive", "Inactive"),
    ("graduated", "Graduated"),
]

STATUS_REASONS = [
    ("not_enrolled", "Not enrolled this semester"),
    ("dropped", "Dropped out"),
    ("transferred", "Transferred"),
    ("graduated", "Graduated"),
    ("other", "Other"),
    # Never offered to staff: only set when a student deactivates their own account.
    ("self_deactivated", "Deactivated by the student"),
]

VERIFICATION_STATUS = [
    ("verified", "Verified"),
    ("unverified", "Unverified"),
    ("rejected", "Rejected"),
]

SERVICES = [
    ("information", "Information"),
    ("counseling", "Counseling"),
    ("testing", "Testing"),
    ("referral", "Referral"),
]

COURSE_GROUPS = [
    ("College of Arts, Sciences, and Education", [
        ("BAMASSCOM", "Mass Communication"),
        ("BAPOLSCI", "Political Science"),
        ("BAPSY", "Psychology"),
        ("BECED", "Early Childhood Education"),
        ("BEED", "Elementary Education"),
        ("BSED", "Secondary Education"),
        ("BPED", "Physical Education"),
        ("BSNED", "Special Needs Education"),
    ]),
    ("College of Computer Studies", [
        ("BSCS-DS", "Computer Science (Data Science)"),
        ("BSIT", "Information Technology (Game Development)"),
        ("BSLIS", "Library and Information Science"),
    ]),
    ("College of Engineering and Architecture", [
        ("BSARCH", "Architecture"),
        ("BSCE", "Civil Engineering"),
        ("BSCPE", "Computer Engineering"),
        ("BSEE", "Electrical Engineering"),
        ("BSECE", "Electronics and Communications Engineering"),
        ("BSIE", "Industrial Engineering"),
        ("BSME", "Mechanical Engineering"),
    ]),
    ("College of Business Administration and Accountancy", [
        ("BSA", "Accountancy"),
        ("BSBA", "Business Administration"),
        ("BSENT", "Entrepreneurship"),
        ("BSMGT", "Management"),
    ]),
    ("Medical and Allied Health Sciences", [
        ("BSN", "Nursing"),
        ("DENT", "Dentistry"),
        ("MD", "Medicine"),
        ("BSMEDTECH", "Medical Technology"),
        ("BSOT", "Occupational Therapy"),
        ("BSPT", "Physical Therapy"),
        ("BSPHARM", "Pharmacy"),
        ("BSRT", "Radiologic Technology"),
        ("BSRESP", "Respiratory Therapy"),
    ]),
    ("College of International Tourism and Hospitality Management", [
        ("BSHRM", "Hotel and Restaurant Management"),
        ("BSTM", "Tourism Management"),
    ]),
    ("College of Criminology", [
        ("BSCRIM", "Criminology"),
    ]),
    ("College of Aviation", [
        ("BSAMT", "Aircraft Maintenance Technology"),
        ("BSAVE", "Aviation Electronics Technology"),
    ]),
    ("College of Maritime Education", [
        ("BSMARE", "Marine Engineering"),
        ("BSMARTR", "Marine Transportation"),
        ("BSNAME", "Naval Architecture and Marine Engineering"),
    ]),
]
COURSES = [course for _college, items in COURSE_GROUPS for course in items]

class College(models.Model):
    """A college / department. Courses belong to one."""
    name = models.CharField(max_length=150, unique=True)
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return self.name


class Course(models.Model):
    college = models.ForeignKey(College, on_delete=models.PROTECT, related_name="courses")
    code = models.CharField(max_length=50, unique=True)   # e.g. BSIT (saved on UserProfile.course)
    name = models.CharField(max_length=150)               # e.g. Information Technology
    sort_order = models.PositiveIntegerField(default=0)
    is_active = models.BooleanField(default=True)

    class Meta:
        ordering = ["sort_order", "name"]

    def __str__(self):
        return "%s - %s" % (self.code, self.name)

YEAR_LEVELS = [
    ("1", "1st Year"), ("2", "2nd Year"), ("3", "3rd Year"),
    ("4", "4th Year"), ("5", "5th Year"),
]

class LoginThrottle(models.Model):
    """Failed password-login attempts per (lower-cased) username.

    Keyed on whatever username was typed, whether or not it exists, so the
    "attempts left" counter doesn't reveal which usernames are registered.
    """
    username_key = models.CharField(max_length=150, unique=True)
    failed_count = models.PositiveSmallIntegerField(default=0)
    last_failed_at = models.DateTimeField(null=True, blank=True)
    locked_until = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.username_key} ({self.failed_count} failed)"

class AssessmentResponse(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='assessment')
    conditions = models.JSONField(default=list, blank=True)
    conditions_other = models.CharField(max_length=255, blank=True)
    responses = models.JSONField(default=dict, blank=True)
    sought_help_before = models.BooleanField(null=True, blank=True)
    has_support_system = models.BooleanField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    skipped = models.BooleanField(default=False)

class AssessmentHistory(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='assessment_history')
    conditions = models.JSONField(default=list, blank=True)
    conditions_other = models.CharField(max_length=255, blank=True)
    responses = models.JSONField(default=dict, blank=True)
    sought_help_before = models.BooleanField(null=True, blank=True)
    has_support_system = models.BooleanField(null=True, blank=True)
    taken_at = models.DateTimeField()

    class Meta:
        ordering = ['-taken_at']


CHAT_THEMES = [
    ("mix", "Original"), ("love", "Love"), ("acads", "Acads"), ("food", "Food"),
    ("cats", "Cats"), ("dogs", "Dogs"), ("flowers", "Flowers"),
]


class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    student_number = models.CharField(max_length=50, blank=True)
    course = models.CharField(max_length=50, blank=True)
    year_level = models.CharField(max_length=10, blank=True)
    nickname = models.CharField(max_length=50, blank=True, null=True)
    # Optional. Students can add it at registration or later in account settings.
    birthday = models.DateField(null=True, blank=True)
    contact_number = models.CharField(max_length=20, blank=True, null=True)
    emergency_contact_name = models.CharField(max_length=100, blank=True, null=True)
    emergency_contact_number = models.CharField(max_length=20, blank=True, null=True)
    avatar = models.ImageField(upload_to='avatars/', blank=True, null=True)
    guidance_notifications = models.BooleanField(default=True)
    appointment_notifications = models.BooleanField(default=True)
    mood_notifications = models.BooleanField(default=False)
    mood_reminder_time = models.TimeField(default='20:00')
    sound_notifications = models.BooleanField(default=True)
    # Firebase UID of the linked Google account. Identity for Google sign-in is
    # keyed on this, never on username/email alone.
    firebase_uid = models.CharField(max_length=128, unique=True, null=True, blank=True)
    # True for brand-new Google sign-ups until they fill in the registration details.
    needs_profile_setup = models.BooleanField(default=False)
    # Set the first time the owner types the e-mailed one-time code correctly. Once set, this
    # e-mail address is taken: nobody can register with it again (Google or local).
    email_verified_at = models.DateTimeField(null=True, blank=True)
    consent_given_at = models.DateTimeField(null=True, blank=True)
    consent_version = models.CharField(max_length=10, blank=True)
    share_assessment_with_guidance = models.BooleanField(default=False)
    share_mica_topics = models.BooleanField(default=False)
    has_seen_tutorial = models.BooleanField(default=False)
    has_seen_mood_tutorial = models.BooleanField(default=False)
    account_status = models.CharField(max_length=10, choices=ACCOUNT_STATUS, default='active', db_index=True)
    status_reason = models.CharField(max_length=20, choices=STATUS_REASONS, blank=True)
    status_changed_at = models.DateTimeField(null=True, blank=True)
    # True only while the account is archived because the STUDENT deactivated it themselves (and
    # staff haven't touched it since). That is the one kind of archive a sign-in undoes. Every
    # staff archive/reactivate clears it, so a staff archive can never be undone by signing in.
    self_deactivated = models.BooleanField(default=False)
    affirmations_seeded = models.BooleanField(default=False)
    # Set once the "you hit your daily goal 2 days" evaluation-form pop-up has been shown and closed.
    goal_reward_shown_at = models.DateTimeField(null=True, blank=True)
    dark_mode = models.BooleanField(default=False)
    chat_theme = models.CharField(max_length=10, choices=CHAT_THEMES, default='mix')
    # Accounts made with a shared term code start "unverified": the student can use MICA, but is
    # invisible to Guidance until a staff member approves them. "rejected" = deactivated as junk.
    # Everyone else (single-use codes, admin-made, accounts from before this existed) is "verified".
    verification_status = models.CharField(max_length=10, choices=VERIFICATION_STATUS,
                                           default='verified', db_index=True)
    verified_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    verified_at = models.DateTimeField(null=True, blank=True)
    registered_code = models.ForeignKey('RegistrationCode', null=True, blank=True,
                                        on_delete=models.SET_NULL, related_name='registrants')

    class Meta:
        constraints = [
            # One student number per student (case-insensitive); blanks are exempt, and so are
            # rejected (deactivated junk) accounts, so a real student can still register with their number.
            models.UniqueConstraint(
                Lower('student_number'),
                condition=~models.Q(student_number='') & ~models.Q(verification_status='rejected'),
                name='unique_student_number_ci',
            ),
        ]

    def __str__(self):
        return f"{self.user.username} - {self.course} ({self.year_level})"

class MoodEntry(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='mood_entries')
    date = models.DateField(default=timezone.localdate)
    mood = models.CharField(max_length=50)
    note = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        unique_together = ('user', 'date')
        ordering = ['-date']

class BreatheSession(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='breathe_sessions')
    duration_seconds = models.PositiveIntegerField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

class Affirmation(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='affirmations')
    text = models.CharField(max_length=140)
    is_favorite = models.BooleanField(default=False)
    is_custom = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at', 'id']

class ExerciseSession(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='exercise_sessions')
    exercise = models.CharField(max_length=30)  # 'affirmations', 'grounding', 'pomodoro'...
    duration_seconds = models.PositiveIntegerField(default=0)
    count = models.PositiveIntegerField(default=0)  # e.g. affirmations said
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']
        indexes = [models.Index(fields=['exercise', 'created_at'])]

class Quest(models.Model):
    """A student's own quest. The default quests live in views.py."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='quests')
    text = models.CharField(max_length=80)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at', 'id']

class DailyQuest(models.Model):
    """One quest on one student's list for one day."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='daily_quests')
    date = models.DateField()
    text = models.CharField(max_length=80)
    is_custom = models.BooleanField(default=False)
    done = models.BooleanField(default=False)
    completed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['id']
        constraints = [models.UniqueConstraint(fields=['user', 'date', 'text'], name='uniq_daily_quest')]
        indexes = [models.Index(fields=['date'])]

class DailyGoalPoint(models.Model):
    """One point a student earned on one day (the daily goal).

    `key` is a feature: 'chat', 'mood', 'guidance' or 'ex:<exercise>'. The unique constraint is what
    caps every task at ONE point per day, however many times it is repeated."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='goal_points')
    date = models.DateField(db_index=True)
    key = models.CharField(max_length=40)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [models.UniqueConstraint(fields=['user', 'date', 'key'], name='uniq_goal_point')]


class ChatConversation(models.Model):
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='conversations')
    title = models.CharField(max_length=100, default="New Chat")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    topics = models.JSONField(default=list, blank=True)
    topics_updated_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-updated_at']

class ChatMessage(models.Model):
    conversation = models.ForeignKey(ChatConversation, on_delete=models.CASCADE, related_name='messages')
    sender = models.CharField(max_length=10, choices=[('user', 'User'), ('bot', 'Bot')])
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['created_at']

class Notification(models.Model):
    KIND_CHOICES = [('chat', 'Chat'), ('appointment', 'Appointment')]
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='notifications')
    message = models.CharField(max_length=255)
    link = models.CharField(max_length=255, blank=True)
    kind = models.CharField(max_length=20, choices=KIND_CHOICES, default='chat')   # <-- new
    is_read = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

class StaffSubclass(models.Model):
    """A guidance-office role the admin can create and customise, e.g. "Guidance Associate".

    The admin ticks what the role may do; `core.permissions` turns that into checks
    on the staff pages. Staff accounts point at one of these from StaffProfile.subclass.
    """
    name = models.CharField(max_length=60, unique=True)
    description = models.CharField(max_length=200, blank=True)

    # --- what this role may do ---------------------------------------------
    view_students = models.BooleanField(default=True)          # see students, mood, risk levels
    chat_students = models.BooleanField(default=True)          # chat with students
    # Lists of service keys from SERVICES ("information", "counseling", ...).
    schedule_services = models.JSONField(default=list, blank=True)    # appointments they may schedule
    write_note_services = models.JSONField(default=list, blank=True)  # notes they may write
    read_note_services = models.JSONField(default=list, blank=True)   # notes they may read
    notes_need_review = models.BooleanField(default=False)     # their notes are marked "needs review"
    review_notes = models.BooleanField(default=False)          # may mark another staff member's note reviewed
    flag_students = models.BooleanField(default=True)
    referrals = models.BooleanField(default=True)              # refer students to other staff and answer referrals sent to them
    take_cases = models.BooleanField(default=False)            # claim a chat thread / take a flag
    resolve_flags = models.BooleanField(default=False)
    view_assessments = models.BooleanField(default=False)
    archive_students = models.BooleanField(default=False)     # archive / reactivate
    export_records = models.BooleanField(default=False)
    generate_codes = models.BooleanField(default=False)       # make student registration codes
    verify_students = models.BooleanField(default=False)      # approve / deactivate unverified student accounts
    clear_avatars = models.BooleanField(default=False)        # remove a student's profile photo (e.g. an inappropriate one)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['name']

    def __str__(self):
        return self.name


class StaffProfile(models.Model):
    ROLE_CHOICES = [
        ('counselor', 'Registered Guidance Counselor'),
        ('psychometrician', 'Psychometrician'),
        ('associate', 'Guidance Associate'),
        ('other', 'Guidance Staff'),
    ]

    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='staff_profile')
    role = models.CharField(max_length=20, choices=ROLE_CHOICES, default='counselor')
    license_number = models.CharField(max_length=50, blank=True)
    # The admin-managed role that decides what this person can do. PROTECT, so deleting a
    # subclass can never silently turn its members into unrestricted staff.
    subclass = models.ForeignKey(StaffSubclass, null=True, blank=True, on_delete=models.PROTECT, related_name='members')
    # Set once the staff member finishes or skips the first-visit Staff Console tour.
    has_seen_tutorial = models.BooleanField(default=False)
    # True for an account the admin just created: it has no password yet. The staff member signs in
    # with their username + an e-mailed code, then must set a password and confirm their details.
    # Cleared when they finish that first-time setup.
    must_complete_setup = models.BooleanField(default=False)

    @property
    def role_label(self):
        return self.subclass.name if self.subclass_id else self.get_role_display()

    def __str__(self):
        return f"{self.user.get_full_name() or self.user.username} ({self.role_label})"

class GuidanceThread(models.Model):
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name='guidance_threads')
    counselor = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='assigned_threads')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    # Messages with an id up to this are a previous staff member's history. When a student is referred
    # to someone else the chat starts fresh: the new counselor never sees those messages (0 = show all).
    history_start_id = models.PositiveBigIntegerField(default=0)
    # The student has dismissed the "give Guidance 24 hours, here are hotlines" notice. Reset to False whenever
    # the student is handed to another counselor, so the notice shows again.
    grace_notice_ack = models.BooleanField(default=False)

    class Meta:
        ordering = ['-updated_at']

class GuidanceMessage(models.Model):
    thread = models.ForeignKey(GuidanceThread, on_delete=models.CASCADE, related_name='messages')
    sender = models.ForeignKey(User, on_delete=models.CASCADE)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    hidden_for_student = models.BooleanField(default=False)

    class Meta:
        ordering = ['created_at']

class Appointment(models.Model):
    thread = models.ForeignKey(GuidanceThread, on_delete=models.CASCADE, related_name='appointments')
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name='appointments')
    counselor = models.ForeignKey(User, on_delete=models.CASCADE, related_name='counselor_appointments')
    scheduled_time = models.DateTimeField()
    status = models.CharField(max_length=20, choices=[('pending', 'Pending'), ('confirmed', 'Confirmed'), ('declined', 'Declined'), ('completed', 'Completed'), ('cancelled', 'Cancelled')], default='pending')
    service = models.CharField(max_length=20, choices=SERVICES, default='counseling')
    reminded_24h = models.BooleanField(default=False)
    reminded_1h = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['scheduled_time']

class CounselorNote(models.Model):
    thread = models.ForeignKey(GuidanceThread, on_delete=models.CASCADE, related_name='notes')
    counselor = models.ForeignKey(User, on_delete=models.CASCADE)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

class StudentNote(models.Model):
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name='staff_notes')
    author = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='written_student_notes')
    topic = models.TextField(blank=True)
    realizations = models.TextField(blank=True)
    service = models.CharField(max_length=20, choices=SERVICES, default='counseling')
    service_delivered = models.BooleanField(default=True)
    is_private = models.BooleanField(default=False)
    session_date = models.DateField(default=timezone.localdate)
    # Notes written by a role that needs sign-off wait here until a reviewer marks them reviewed.
    needs_review = models.BooleanField(default=False)
    reviewed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='reviewed_student_notes')
    reviewed_at = models.DateTimeField(null=True, blank=True)
    referred_to = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL,
                                    related_name='referral_notes_received')
    referred_to_external = models.CharField(max_length=120, blank=True)
    referral_seen = models.BooleanField(default=False)
    # The staff member a referral is addressed to accepts or declines it. Only meaningful when referred_to is set.
    REFERRAL_STATUSES = [('pending', 'Pending'), ('accepted', 'Accepted'), ('declined', 'Declined')]
    referral_status = models.CharField(max_length=10, choices=REFERRAL_STATUSES, default='pending')
    referral_responded_at = models.DateTimeField(null=True, blank=True)
    referral_decline_reason = models.CharField(max_length=200, blank=True)
    referral_response_seen = models.BooleanField(default=True)   # False until the referring staff member has seen the answer
    student_informed = models.BooleanField(default=False)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ['-session_date', '-created_at']

class StudentFlag(models.Model):
    STATUS_CHOICES = [('new', 'New'), ('review', 'In Review'), ('resolved', 'Resolved')]
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name='flags')
    flagged_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='flags_raised')
    assigned_to = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='flags_assigned')
    reason = models.TextField()
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='new')
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

def _person_label(user):
    """Name kept on audit rows so they still say who they were about after the account is deleted."""
    return (user.get_full_name() or user.username)[:150]


class AccessLog(models.Model):
    staff = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='access_logs')
    # SET_NULL, not CASCADE: deleting a student account must not erase the record of who looked
    # at their data. `student_label` is a snapshot of the name so the row stays readable.
    student = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='access_logs_received')
    student_label = models.CharField(max_length=150, blank=True)
    action = models.CharField(max_length=30)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        if self.student_id and not self.student_label:
            self.student_label = _person_label(self.student)
        super().save(*args, **kwargs)


class LoginVerification(models.Model):
    """A single-use, e-mailed code (6 characters) for signing in or changing a password.

    Only a keyed hash of the code is stored, so a database leak can't be used to
    sign in. `used_at` is set when the code is used *or* replaced by a newer one.
    `attempts` counts wrong guesses; after a few the code is refused.
    """
    METHOD_CHOICES = [('password', 'Password'), ('google', 'Google')]
    PURPOSE_CHOICES = [('login', 'Sign-in'), ('password_change', 'Password change'), ('email_change', 'Email change')]

    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='login_verifications')
    # What the code is for. A sign-in code can't confirm a password change, and vice versa.
    purpose = models.CharField(max_length=20, choices=PURPOSE_CHOICES, default='login')
    code_hash = models.CharField(max_length=64)
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_at = models.DateTimeField(null=True, blank=True)
    attempts = models.PositiveSmallIntegerField(default=0)
    remember_me = models.BooleanField(default=False)
    method = models.CharField(max_length=10, choices=METHOD_CHOICES, default='password')
    request_ip = models.GenericIPAddressField(null=True, blank=True)

    def __str__(self):
        return f"{self.user} ({self.method}) {self.created_at:%Y-%m-%d %H:%M}"

class StudentStatusLog(models.Model):
    # SET_NULL + name snapshot, same reasoning as AccessLog.student.
    student = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True,
                                related_name='status_history')
    student_label = models.CharField(max_length=150, blank=True)
    changed_by = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, blank=True, related_name='status_changes_made')
    old_status = models.CharField(max_length=10)
    new_status = models.CharField(max_length=10)
    reason = models.CharField(max_length=20, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    note = models.CharField(max_length=200, blank=True)
    exit_interview_done = models.BooleanField(default=False)
    evaluation_done = models.BooleanField(default=False)
    class Meta:
        ordering = ['-created_at']

    def save(self, *args, **kwargs):
        if self.student_id and not self.student_label:
            self.student_label = _person_label(self.student)
        super().save(*args, **kwargs)

@receiver(post_save, sender=User)
def ensure_staff_profile(sender, instance, **kwargs):
    if instance.is_staff and not instance.is_superuser:
        StaffProfile.objects.get_or_create(user=instance, defaults={'role': 'other'})


class PasswordResetRequest(models.Model):
    """One "send me a reset link" request. Only used to rate-limit, per e-mail and per IP."""
    email_key = models.CharField(max_length=254, db_index=True)
    ip = models.GenericIPAddressField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.email_key} @ {self.created_at:%Y-%m-%d %H:%M}"


class Activity(models.Model):
    """A group activity: seminar, webinar, Guidance Month event, training..."""
    title = models.CharField(max_length=150)
    service = models.CharField(max_length=20, choices=SERVICES, default='information')
    date = models.DateField(default=timezone.localdate)
    audience = models.CharField(max_length=150, blank=True)       # e.g. "BSIT 2nd year", "Peer Facilitators Society"
    attendee_count = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='logged_activities')
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-date', '-created_at']


class Semester(models.Model):
    """A named date range (e.g. "1st Sem AY 2026-2027") used by the report period dropdowns."""
    name = models.CharField(max_length=80, unique=True)
    start_date = models.DateField()
    end_date = models.DateField()

    class Meta:
        ordering = ['-start_date']

    def clean(self):
        if self.start_date and self.end_date and self.end_date < self.start_date:
            raise ValidationError("The end date can't be before the start date.")

    def __str__(self):
        return self.name


class SiteSettings(models.Model):
    """One row only: the things the admin can edit in System Settings."""
    platform_name = models.CharField(max_length=80, default="MICA")
    institution_name = models.CharField(max_length=120, blank=True)
    support_email = models.EmailField(blank=True)
    registration_open = models.BooleanField(default=True)   # admin switch: can new students register?
    hotlines = models.JSONField(default=list, blank=True)   # used by the hotlines step
    updated_at = models.DateTimeField(auto_now=True)
    updated_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    maintenance_on = models.BooleanField(default=False)
    maintenance_message = models.CharField(max_length=300, blank=True, default="")

    @classmethod
    def get(cls):
        obj, _ = cls.objects.get_or_create(pk=1)
        return obj


class EnrolledStudent(models.Model):
    """The official enrollment list the admin uploads each semester. Registration is checked against it."""
    student_number = models.CharField(max_length=50)
    last_name = models.CharField(max_length=150)
    first_name = models.CharField(max_length=150, blank=True)
    course = models.CharField(max_length=50, blank=True)
    year_level = models.CharField(max_length=10, blank=True)
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(Lower('student_number'), name='unique_enrolled_number_ci'),
        ]

    def __str__(self):
        return "%s (%s)" % (self.student_number, self.last_name)
    

class RegistrationCode(models.Model):
    """A code a student types on /register/ to get an account.

    Two kinds:
      * "single": one code per student, works once. Made one at a time or in a printed batch
        (`batch` groups the codes made together). Staff handed it over in person, so the account
        it creates is verified straight away.
      * "shared": one "term code" announced to a whole class or year, with a cap on how many
        accounts it can create (`max_uses`) and an expiry. Accounts it creates are *unverified*
        until a staff member approves them.
    Either kind stops working at `expires_at`, when it is used up, or once `revoked_at` is set.
    """
    KIND_CHOICES = [("single", "Single-use"), ("shared", "Shared term code")]
    AUDIENCE_CHOICES = [("student", "Student"), ("staff", "Staff")]

    code = models.CharField(max_length=24, unique=True)
    kind = models.CharField(max_length=10, choices=KIND_CHOICES, default="single", db_index=True)
    # Who the code is for. A "staff" code is made by the System Admin only, is always single-use,
    # and creates a staff account with the role in `subclass`. It never works for a student, and a
    # student code never works for staff. Student codes keep everything above unchanged.
    audience = models.CharField(max_length=7, choices=AUDIENCE_CHOICES, default="student", db_index=True)
    subclass = models.ForeignKey(StaffSubclass, null=True, blank=True, on_delete=models.SET_NULL,
                                 related_name="+")   # staff codes: the role the new account gets
    label = models.CharField(max_length=80, blank=True)          # e.g. "BSN 1st years" or "1st Sem AY 2026-2027"
    batch = models.CharField(max_length=32, blank=True, db_index=True)
    max_uses = models.PositiveIntegerField(default=1)
    use_count = models.PositiveIntegerField(default=0)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='registration_codes')
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')   # single-use: who
    used_at = models.DateTimeField(null=True, blank=True)        # most recent use
    revoked_at = models.DateTimeField(null=True, blank=True)
    revoked_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')

    class Meta:
        ordering = ['-created_at']

    @property
    def uses_left(self):
        return max(0, self.max_uses - self.use_count)

    def __str__(self):
        return self.code


class AuditEvent(models.Model):
    """Admin-level events that aren't 'staff opened a student' (settings changed, logins...)."""
    actor = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='audit_events')
    event = models.CharField(max_length=40)          # e.g. "settings_changed"
    detail = models.CharField(max_length=300, blank=True)
    ip = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class SignInFailure(models.Model):
    """One failed sign-in, kept 90 days so the admin can tell a typo from an attack."""
    REASONS = [("password", "Wrong password"), ("code", "Wrong e-mail code"),
               ("totp", "Wrong authenticator code")]
    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.CASCADE, related_name="sign_in_failures")
    reason = models.CharField(max_length=10, choices=REASONS)
    ip = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]


class TotpDevice(models.Model):
    """A System Admin's authenticator app (time-based one-time passwords).

    `secret_enc` is the shared secret, encrypted with a key derived from SECRET_KEY (or
    TOTP_ENCRYPTION_KEY), so a copy of the database alone can't produce sign-in codes.
    The device counts only once `confirmed_at` is set, i.e. after the admin typed a valid code
    from the app. `last_used_step` is the newest 30-second step already accepted: a code from
    that step or an earlier one is refused, so a code can't be used twice.
    """
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name="totp_device")
    secret_enc = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)
    confirmed_at = models.DateTimeField(null=True, blank=True)
    last_used_step = models.BigIntegerField(default=0)

    def __str__(self):
        return f"{self.user} ({'on' if self.confirmed_at else 'not confirmed'})"


class RecoveryCode(models.Model):
    """One single-use backup code for an admin who has lost their authenticator.
    Only a keyed hash is stored; the code itself is shown once, when it is made."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name="recovery_codes")
    code_hash = models.CharField(max_length=64, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    used_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.user} ({'used' if self.used_at else 'unused'})"


class DataRequest(models.Model):
    TYPES = [
        ("access", "Access to their data"),
        ("correction", "Correction"),
        ("erasure", "Erasure"),
        ("other", "Other"),
    ]
    STATUSES = [("open", "Open"), ("done", "Done"), ("declined", "Declined")]

    student_name = models.CharField(max_length=150)
    student_number = models.CharField(max_length=30, blank=True)
    student = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                on_delete=models.SET_NULL, related_name="+")
    student_username = models.CharField(max_length=150, blank=True)
    request_type = models.CharField(max_length=12, choices=TYPES)
    request_detail = models.CharField(max_length=120, blank=True)
    status = models.CharField(max_length=10, choices=STATUSES, default="open")
    received_on = models.DateField()
    note = models.CharField(max_length=300, blank=True)
    outcome = models.CharField(max_length=300, blank=True)
    logged_by = models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True,
                                  on_delete=models.SET_NULL, related_name="+")
    handled_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-received_on", "-id"]

class ConsoleNotification(models.Model):
    """Bell notifications for the staff and admin consoles (students use Notification)."""
    recipient = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name='console_notifications'
    )
    kind = models.CharField(max_length=30)                 # 'message', 'flag', 'appointment', ...
    text = models.CharField(max_length=200)
    section = models.CharField(max_length=30, blank=True)  # sidebar tab to open on click, e.g. 'alerts'
    student_ref = models.PositiveIntegerField(null=True, blank=True)  # the student's user id (same as s.id in the Students list)
    ref_id = models.PositiveIntegerField(null=True, blank=True)       # thread / flag / appointment id
    urgent = models.BooleanField(default=False)            # urgent ones play a sound
    dedupe_key = models.CharField(max_length=80, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    read_at = models.DateTimeField(null=True, blank=True)
    dismissed_at = models.DateTimeField(null=True, blank=True)   # "deleted" by the user; row kept so auto-created ones don't come back

    class Meta:
        ordering = ['-created_at', '-id']
        indexes = [models.Index(fields=['recipient', 'read_at'])]
        constraints = [
            models.UniqueConstraint(
                fields=['recipient', 'dedupe_key'],
                condition=~models.Q(dedupe_key=''),
                name='uniq_console_notification_dedupe',
            )
        ]

    def __str__(self):
        return f'{self.recipient_id}: {self.text}'


class SignInLog(models.Model):
    """One successful sign-in, so first/last/all logins can be looked up."""
    user = models.ForeignKey(User, on_delete=models.CASCADE, related_name='sign_ins')
    ip = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


from django.contrib.auth.signals import user_logged_in
from django.dispatch import receiver


@receiver(user_logged_in)
def _record_sign_in(sender, request, user, **kwargs):
    from .net import client_ip
    try:
        SignInLog.objects.create(user=user, ip=client_ip(request) if request else None)
    except Exception:
        pass    # never block a login over logging