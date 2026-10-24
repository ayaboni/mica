from django.contrib import admin
from django.contrib.auth.admin import UserAdmin as BaseUserAdmin
from django.contrib.auth.models import User
from .models import StaffProfile, AccessLog, StudentStatusLog, StaffSubclass
from .models import UserProfile, MoodEntry, ChatConversation, ChatMessage, StaffProfile, GuidanceThread, GuidanceMessage, AccessLog

admin.site.register(UserProfile)
admin.site.register(ChatConversation)
admin.site.register(GuidanceThread)
admin.site.register(GuidanceMessage)

@admin.register(MoodEntry)
class MoodEntryAdmin(admin.ModelAdmin):
    exclude = ('note',)
    list_display = ('user', 'date', 'mood')

@admin.register(ChatMessage)
class ChatMessageAdmin(admin.ModelAdmin):
    exclude = ('content',)
    list_display = ('conversation', 'sender', 'created_at')

admin.site.register(StaffSubclass)

@admin.register(StaffProfile)
class StaffProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'role', 'subclass', 'license_number')
    list_filter = ('role', 'subclass')

class StaffProfileInline(admin.StackedInline):
    model = StaffProfile
    can_delete = False
    extra = 0
    max_num = 1
    fields = ('subclass', 'role', 'license_number')
    verbose_name_plural = 'Guidance office role'


admin.site.unregister(User)

@admin.register(User)
class UserAdmin(BaseUserAdmin):
    inlines = [StaffProfileInline]
    list_display = ('username', 'first_name', 'last_name', 'email',
                    'is_staff', 'is_superuser', 'guidance_role')

    @admin.display(description='Guidance role')
    def guidance_role(self, obj):
        sp = getattr(obj, 'staff_profile', None)
        return sp.get_role_display() if sp else '-'

    def get_queryset(self, request):
        return super().get_queryset(request).select_related('staff_profile')

    def get_inline_instances(self, request, obj=None):
        # role box only shows for existing staff accounts (not students, not superusers)
        if obj is None or not obj.is_staff or obj.is_superuser:
            return []
        return super().get_inline_instances(request, obj)


class LockedLogAdmin(admin.ModelAdmin):
    def has_add_permission(self, request):
        return False
    def has_change_permission(self, request, obj=None):
        return False


@admin.register(AccessLog)
class AccessLogAdmin(LockedLogAdmin):
    list_display = ('created_at', 'staff', 'student', 'action')
    list_filter = ('action',)
    date_hierarchy = 'created_at'

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False

@admin.register(StudentStatusLog)
class StudentStatusLogAdmin(LockedLogAdmin):
    list_display = ('created_at', 'student', 'changed_by', 'old_status', 'new_status', 'reason')