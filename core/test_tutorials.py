from django.contrib.auth.models import User
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone

from .models import AssessmentResponse, StaffProfile, UserProfile
from .views import CONSENT_VERSION


class StudentTutorialTests(TestCase):
    """First-visit dashboard tour and Mood Diary tour: shown once, students only."""

    def setUp(self):
        self.student = User.objects.create_user('tutorial_student', password='Str0ng!Pass1')
        # past the consent + assessment steps, so the dashboard itself renders
        self.profile = UserProfile.objects.create(user=self.student, consent_version=CONSENT_VERSION)
        AssessmentResponse.objects.create(user=self.student, completed_at=timezone.now())
        self.client.force_login(self.student)

    def test_new_student_sees_both_tutorials(self):
        response = self.client.get(reverse('student_dashboard'))
        self.assertTrue(response.context['show_tutorial'])
        self.assertTrue(response.context['show_mood_tutorial'])
        self.assertContains(response, 'id="tour-root"')

    def test_completing_dashboard_tutorial_hides_it_for_good(self):
        response = self.client.post(reverse('api_tutorial_complete'))
        self.assertEqual(response.status_code, 200)
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.has_seen_tutorial)
        response = self.client.get(reverse('student_dashboard'))
        self.assertFalse(response.context['show_tutorial'])

    def test_mood_tutorial_is_tracked_separately(self):
        self.client.post(reverse('api_tutorial_complete'), {'which': 'dashboard'})
        response = self.client.get(reverse('student_dashboard'))
        self.assertFalse(response.context['show_tutorial'])
        self.assertTrue(response.context['show_mood_tutorial'])

        self.client.post(reverse('api_tutorial_complete'), {'which': 'mood'})
        self.profile.refresh_from_db()
        self.assertTrue(self.profile.has_seen_mood_tutorial)
        response = self.client.get(reverse('student_dashboard'))
        self.assertFalse(response.context['show_mood_tutorial'])
        self.assertNotContains(response, 'id="tour-root"')

    def test_complete_endpoint_requires_post_and_a_known_name(self):
        self.assertEqual(self.client.get(reverse('api_tutorial_complete')).status_code, 405)
        response = self.client.post(reverse('api_tutorial_complete'), {'which': 'nonsense'})
        self.assertEqual(response.status_code, 400)

    def test_staff_never_get_either_tutorial(self):
        staff = User.objects.create_user('tutorial_staff', password='Str0ng!Pass1', is_staff=True)
        self.client.force_login(staff)
        response = self.client.get(reverse('student_dashboard'))
        self.assertFalse(response.context['show_tutorial'])
        self.assertFalse(response.context['show_mood_tutorial'])


class StudentReplayTutorialTests(TestCase):
    """The 'Replay Tutorial' button on the student's Account Settings page."""

    def setUp(self):
        self.student = User.objects.create_user('replay_student', password='Str0ng!Pass1')
        self.profile = UserProfile.objects.create(
            user=self.student, consent_version=CONSENT_VERSION,
            has_seen_tutorial=True, has_seen_mood_tutorial=True)
        AssessmentResponse.objects.create(user=self.student, completed_at=timezone.now())
        self.client.force_login(self.student)

    def test_account_settings_has_the_replay_button(self):
        response = self.client.get(reverse('account_settings'))
        self.assertContains(response, 'Replay Tutorial')
        self.assertContains(response, reverse('tutorial_reset'))

    def test_reset_brings_both_tours_back_and_returns_to_the_dashboard(self):
        response = self.client.post(reverse('tutorial_reset'))
        self.assertRedirects(response, reverse('student_dashboard'), fetch_redirect_response=False)
        self.profile.refresh_from_db()
        self.assertFalse(self.profile.has_seen_tutorial)
        self.assertFalse(self.profile.has_seen_mood_tutorial)
        dashboard = self.client.get(reverse('student_dashboard'))
        self.assertTrue(dashboard.context['show_tutorial'])
        self.assertTrue(dashboard.context['show_mood_tutorial'])
        self.assertContains(dashboard, 'id="tour-root"')

    def test_reset_requires_post(self):
        self.assertEqual(self.client.get(reverse('tutorial_reset')).status_code, 405)

    def test_reset_requires_sign_in(self):
        self.client.logout()
        response = self.client.post(reverse('tutorial_reset'))
        self.assertEqual(response.status_code, 302)
        self.assertIn(reverse('login'), response.url)

    def test_finishing_the_replay_hides_it_again(self):
        self.client.post(reverse('tutorial_reset'))
        self.client.post(reverse('api_tutorial_complete'), {'which': 'dashboard'})
        self.client.post(reverse('api_tutorial_complete'), {'which': 'mood'})
        dashboard = self.client.get(reverse('student_dashboard'))
        self.assertFalse(dashboard.context['show_tutorial'])
        self.assertFalse(dashboard.context['show_mood_tutorial'])


class StaffTutorialTests(TestCase):
    """First-visit Staff Console tour and its replay button."""

    def setUp(self):
        self.staff = User.objects.create_user('counselor_ann', password='Str0ng!Pass1', is_staff=True)
        self.client.force_login(self.staff)

    def test_new_staff_member_sees_the_tour(self):
        response = self.client.get(reverse('staff_dashboard'))
        self.assertTrue(response.context['show_staff_tutorial'])
        self.assertContains(response, 'id="tour-root"')
        self.assertContains(response, 'Replay tutorial')      # button in Settings -> My Profile

    def test_finishing_the_tour_hides_it_for_good(self):
        response = self.client.post(reverse('api_tutorial_complete'), {'which': 'staff'})
        self.assertEqual(response.status_code, 200)
        self.assertTrue(StaffProfile.objects.get(user=self.staff).has_seen_tutorial)
        response = self.client.get(reverse('staff_dashboard'))
        self.assertFalse(response.context['show_staff_tutorial'])
        self.assertNotContains(response, 'id="tour-root"')
        self.assertContains(response, 'Replay tutorial')      # the button is always there

    def test_the_label_for_staff_without_a_profile_does_not_change(self):
        before = self.client.get(reverse('staff_dashboard')).context['staff_role']
        self.client.post(reverse('api_tutorial_complete'), {'which': 'staff'})
        after = self.client.get(reverse('staff_dashboard')).context['staff_role']
        self.assertEqual(before, 'Guidance Staff')
        self.assertEqual(after, before)

    def test_an_existing_role_is_left_alone(self):
        StaffProfile.objects.update_or_create(user=self.staff, defaults={'role': 'psychologist'})
        self.client.post(reverse('api_tutorial_complete'), {'which': 'staff'})
        self.assertEqual(StaffProfile.objects.get(user=self.staff).role, 'psychologist')

    def test_reset_brings_the_tour_back(self):
        self.client.post(reverse('api_tutorial_complete'), {'which': 'staff'})
        response = self.client.post(reverse('tutorial_reset'))
        self.assertRedirects(response, reverse('staff_dashboard'), fetch_redirect_response=False)
        self.assertFalse(StaffProfile.objects.get(user=self.staff).has_seen_tutorial)
        self.assertTrue(self.client.get(reverse('staff_dashboard')).context['show_staff_tutorial'])

    def test_staff_do_not_get_student_tutorial_records(self):
        for which in ('dashboard', 'mood'):
            response = self.client.post(reverse('api_tutorial_complete'), {'which': which})
            self.assertEqual(response.status_code, 400)
        self.assertFalse(UserProfile.objects.filter(user=self.staff).exists())   # would break onboarding checks

    def test_students_cannot_complete_the_staff_tour(self):
        student = User.objects.create_user('some_student', password='Str0ng!Pass1')
        UserProfile.objects.create(user=student)
        self.client.force_login(student)
        response = self.client.post(reverse('api_tutorial_complete'), {'which': 'staff'})
        self.assertEqual(response.status_code, 400)
        self.assertFalse(StaffProfile.objects.filter(user=student).exists())

    def test_system_admin_does_not_get_the_staff_tour(self):
        admin = User.objects.create_superuser('sysadmin', 'admin@example.com', 'Str0ng!Pass1')
        self.client.force_login(admin)
        self.assertFalse(self.client.get(reverse('staff_dashboard')).context['show_staff_tutorial'])
        self.assertEqual(self.client.post(reverse('api_tutorial_complete'), {'which': 'staff'}).status_code, 400)
        response = self.client.post(reverse('tutorial_reset'))
        self.assertRedirects(response, reverse('admin_dashboard'), fetch_redirect_response=False)
