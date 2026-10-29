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
]

SERVICES = [
    ("information", "Information"),
    ("counseling", "Counseling"),
    ("testing", "Testing"),
    ("referral", "Referral"),
]

COURSES = [
    ("BSCS-DS", "BS Computer Science - Data Science"),
    ("BSIT", "BS Information Technology - Game Development"),
    ("BSME", "BS Mechanical Engineering"),
    ("BSEE", "BS Electrical Engineering"),
    ("BAPSY", "BA Psychology"),
    ("BSBA", "BS Business Administration"),
    ("BSN", "BS Nursing"),
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

class UserProfile(models.Model):
    user = models.OneToOneField(User, on_delete=models.CASCADE, related_name='profile')
    student_number = models.CharField(max_length=50, blank=True)
    course = models.CharField(max_length=50, blank=True)
    year_level = models.CharField(max_length=10, blank=True)
    nickname = models.CharField(max_length=50, blank=True, null=True)
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
    consent_given_at = models.DateTimeField(null=True, blank=True)
    consent_version = models.CharField(max_length=10, blank=True)
    share_assessment_with_guidance = models.BooleanField(default=False)
    share_mica_topics = models.BooleanField(default=False)
    has_seen_tutorial = models.BooleanField(default=False)
    has_seen_mood_tutorial = models.BooleanField(default=False)
    account_status = models.CharField(max_length=10, choices=ACCOUNT_STATUS, default='active', db_index=True)
    status_reason = models.CharField(max_length=20, choices=STATUS_REASONS, blank=True)
    status_changed_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        constraints = [
            # One student number per student (case-insensitive); blanks are exempt.
            models.UniqueConstraint(
                Lower('student_number'),
                condition=~models.Q(student_number=''),
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
    take_cases = models.BooleanField(default=False)            # claim a chat thread / take a flag
    resolve_flags = models.BooleanField(default=False)
    view_assessments = models.BooleanField(default=False)
    archive_students = models.BooleanField(default=False)     # archive / reactivate
    export_records = models.BooleanField(default=False)
    generate_codes = models.BooleanField(default=False)       # make student registration codes

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

    class Meta:
        ordering = ['-updated_at']

class GuidanceMessage(models.Model):
    thread = models.ForeignKey(GuidanceThread, on_delete=models.CASCADE, related_name='messages')
    sender = models.ForeignKey(User, on_delete=models.CASCADE)
    content = models.TextField()
    created_at = models.DateTimeField(auto_now_add=True)

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

class AccessLog(models.Model):
    staff = models.ForeignKey(User, on_delete=models.SET_NULL, null=True, related_name='access_logs')
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name='access_logs_received')
    action = models.CharField(max_length=30)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ['-created_at']


class LoginVerification(models.Model):
    """A single-use, e-mailed code (6 characters) for signing in or changing a password.

    Only a keyed hash of the code is stored, so a database leak can't be used to
    sign in. `used_at` is set when the code is used *or* replaced by a newer one.
    `attempts` counts wrong guesses; after a few the code is refused.
    """
    METHOD_CHOICES = [('password', 'Password'), ('google', 'Google')]
    PURPOSE_CHOICES = [('login', 'Sign-in'), ('password_change', 'Password change')]

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
    student = models.ForeignKey(User, on_delete=models.CASCADE, related_name='status_history')
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
    """A short code (6 characters) a staff member hands to a student so they can register.

    Single-use, and it stops working `expires_at`. Staff may generate one code a minute.
    """
    code = models.CharField(max_length=6, unique=True)
    created_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='registration_codes')
    created_at = models.DateTimeField(auto_now_add=True)
    expires_at = models.DateTimeField()
    used_by = models.ForeignKey(User, null=True, blank=True, on_delete=models.SET_NULL, related_name='+')
    used_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ['-created_at']

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
    REASONS = [("password", "Wrong password"), ("code", "Wrong e-mail code")]
    user = models.ForeignKey(User, null=True, blank=True, on_delete=models.CASCADE, related_name="sign_in_failures")
    reason = models.CharField(max_length=10, choices=REASONS)
    ip = models.GenericIPAddressField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        ordering = ["-created_at"]


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