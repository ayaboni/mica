from django.contrib import admin
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

@admin.register(StaffProfile)
class StaffProfileAdmin(admin.ModelAdmin):
    list_display = ('user', 'role', 'license_number')
    list_filter = ('role',)

@admin.register(AccessLog)
class AccessLogAdmin(admin.ModelAdmin):
    list_display = ('created_at', 'staff', 'student', 'action')
    list_filter = ('action',)

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False