import io

from django.contrib.auth.models import User
from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase, override_settings
from django.urls import reverse

from .models import AuditEvent, StaffProfile, StaffSubclass, UserProfile
from .permissions import can


def _png():
    from PIL import Image
    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(buf, "PNG")
    return SimpleUploadedFile("a.png", buf.getvalue(), content_type="image/png")


@override_settings(MEDIA_ROOT="/tmp/mica_test_media")
class AvatarVerificationTests(TestCase):
    def _student(self, name, status):
        u = User.objects.create_user(name, f"{name}@x.com", "pw12345!")
        UserProfile.objects.create(user=u, verification_status=status)
        return u

    def test_unverified_and_rejected_cannot_set_avatar(self):
        for status in ("unverified", "rejected"):
            u = self._student("s_" + status, status)
            self.client.force_login(u)
            r = self.client.post(reverse("staff_avatar_update"), {"avatar": _png()})
            self.assertEqual(r.status_code, 403, status)
            u.profile.refresh_from_db()
            self.assertFalse(u.profile.avatar)

    def test_verified_student_can_set_and_remove(self):
        u = self._student("ok", "verified")
        self.client.force_login(u)
        r = self.client.post(reverse("staff_avatar_update"), {"avatar": _png()})
        self.assertEqual(r.status_code, 200)
        u.profile.refresh_from_db()
        self.assertTrue(u.profile.avatar)
        r = self.client.post(reverse("staff_avatar_update"), {"remove": "1"})
        self.assertEqual(r.status_code, 200)
        u.profile.refresh_from_db()
        self.assertFalse(u.profile.avatar)

    def test_unverified_can_still_remove_existing(self):
        u = self._student("old", "unverified")
        u.profile.avatar.save("old.png", _png(), save=True)
        self.client.force_login(u)
        self.client.post(reverse("staff_avatar_update"), {"remove": "1"})
        u.profile.refresh_from_db()
        self.assertFalse(u.profile.avatar)

    def test_account_settings_form_cannot_bypass(self):
        u = self._student("sneaky", "unverified")
        self.client.force_login(u)
        self.client.post(reverse("account_settings"), {
            "first_name": "A", "last_name": "B", "username": "sneaky", "avatar": _png()})
        u.profile.refresh_from_db()
        self.assertFalse(u.profile.avatar)

    def test_staff_can_always_set(self):
        s = User.objects.create_user("staff1", "s@x.com", "pw12345!", is_staff=True)
        self.client.force_login(s)
        r = self.client.post(reverse("staff_avatar_update"), {"avatar": _png()})
        self.assertEqual(r.status_code, 200)


@override_settings(MEDIA_ROOT="/tmp/mica_test_media")
class ClearAvatarTests(TestCase):
    def setUp(self):
        self.student = User.objects.create_user("stu", "stu@x.com", "pw12345!")
        self.profile = UserProfile.objects.create(user=self.student)
        self.profile.avatar.save("bad.png", _png(), save=True)

    def _staff(self, name, **perm):
        sub = StaffSubclass.objects.create(name=name, **perm)
        u = User.objects.create_user(name, f"{name}@x.com", "pw12345!", is_staff=True)
        StaffProfile.objects.filter(user=u).update(subclass=sub)   # a signal already made the profile
        return u

    def _clear(self, user, student=None):
        self.client.force_login(user)
        return self.client.post(reverse("staff_student_avatar_clear", args=[(student or self.student).id]))

    def test_permission_defaults_off_and_is_listed(self):
        self.assertFalse(StaffSubclass.objects.create(name="x").clear_avatars)
        from .permissions import PERMISSION_FIELDS
        self.assertIn("clear_avatars", [f for f, _, _ in PERMISSION_FIELDS])

    def test_staff_with_permission_clears_and_is_audited(self):
        staff = self._staff("mod", clear_avatars=True)
        self.assertTrue(can(staff, "clear_avatars"))
        r = self._clear(staff)
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["cleared"])
        self.profile.refresh_from_db()
        self.assertFalse(self.profile.avatar)
        self.assertTrue(AuditEvent.objects.filter(event="avatar_cleared", actor=staff).exists())

    def test_staff_without_permission_is_refused(self):
        staff = self._staff("nope", clear_avatars=False)
        self.assertEqual(self._clear(staff).status_code, 403)
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.avatar)

    def test_student_cannot_clear(self):
        other = User.objects.create_user("other", "o@x.com", "pw12345!")
        self.assertIn(self._clear(other).status_code, (302, 403))
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.avatar)

    @override_settings(ADMIN_TWO_FACTOR_REQUIRED=False)
    def test_superuser_can_clear(self):
        admin = User.objects.create_superuser("root", "r@x.com", "pw12345!")
        self.assertEqual(self._clear(admin).status_code, 200)

    def test_staff_cannot_touch_staff_or_hidden_students(self):
        staff = self._staff("mod2", clear_avatars=True)
        self.assertEqual(self._clear(staff, student=staff).status_code, 404)
        self.profile.verification_status = "unverified"
        self.profile.save()
        self.assertEqual(self._clear(staff).status_code, 404)
