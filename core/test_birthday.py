"""Optional student birthday: validator, registration, Google first-time setup, and account settings."""
import datetime

from django.contrib.auth.models import User
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone

from .models import COURSES, UserProfile, YEAR_LEVELS
from .name_validation import birthday_bounds, validate_birthday
from .regcode_testing import grant_registration_code
from .test_registration_codes import FORM   # a known-good student registration payload

TODAY = datetime.date(2026, 11, 4)


class ValidateBirthdayTests(SimpleTestCase):
    def test_blank_is_fine(self):
        for blank in ("", "   ", None):
            self.assertEqual(validate_birthday(blank, TODAY), (None, None))

    def test_a_normal_birthday_is_accepted(self):
        self.assertEqual(validate_birthday("2005-03-14", TODAY), (datetime.date(2005, 3, 14), None))

    def test_wrong_formats_and_impossible_dates_are_refused(self):
        for bad in ("03/14/2005", "2005-3-14", "20050314", "2005-02-30", "2005-13-01", "yesterday", "<b>"):
            date, problem = validate_birthday(bad, TODAY)
            self.assertIsNone(date, bad)
            self.assertIsNotNone(problem, bad)

    def test_future_too_young_and_too_old_are_refused(self):
        for bad in ("2026-11-05", "2020-01-01", "1900-01-01"):
            self.assertIsNotNone(validate_birthday(bad, TODAY)[1], bad)

    def test_the_edges_of_the_range(self):
        earliest, latest = birthday_bounds(TODAY)
        self.assertIsNone(validate_birthday(earliest.isoformat(), TODAY)[1])
        self.assertIsNone(validate_birthday(latest.isoformat(), TODAY)[1])
        self.assertIsNotNone(validate_birthday((earliest - datetime.timedelta(days=1)).isoformat(), TODAY)[1])
        self.assertIsNotNone(validate_birthday((latest + datetime.timedelta(days=1)).isoformat(), TODAY)[1])

    def test_leap_day_today_does_not_crash(self):
        earliest, latest = birthday_bounds(datetime.date(2024, 2, 29))
        self.assertEqual(latest, datetime.date(2014, 2, 28))


class RegisterBirthdayTests(TestCase):
    def setUp(self):
        cache.clear()
        grant_registration_code(self.client)

    def profile(self):
        return UserProfile.objects.get(user__username='sam')

    def test_birthday_is_optional(self):
        self.client.post(reverse('register'), FORM)
        self.assertIsNone(self.profile().birthday)

    def test_birthday_is_saved(self):
        self.client.post(reverse('register'), {**FORM, 'birthday': '2005-03-14'})
        self.assertEqual(self.profile().birthday, datetime.date(2005, 3, 14))

    def test_a_bad_birthday_stops_registration_and_is_kept_in_the_form(self):
        response = self.client.post(reverse('register'), {**FORM, 'birthday': '2999-01-01'})
        self.assertContains(response, 'birthday')
        self.assertFalse(User.objects.filter(username='sam').exists())
        self.assertContains(response, 'value="2999-01-01"')

    def test_the_form_offers_the_field(self):
        response = self.client.get(reverse('register'))
        self.assertContains(response, 'name="birthday"')


class AccountSettingsBirthdayTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user('lena', 'lena@example.com', 'Pw-12345', first_name='Lena', last_name='Cruz')
        UserProfile.objects.create(user=self.user, course=COURSES[0][0], year_level=YEAR_LEVELS[0][0])
        self.client.force_login(self.user)

    def post(self, **fields):
        data = {'username': 'lena', 'first_name': 'Lena', 'last_name': 'Cruz', **fields}
        return self.client.post(reverse('account_settings'), data)

    def birthday(self):
        return UserProfile.objects.get(user=self.user).birthday

    def test_page_shows_the_field_with_the_saved_value(self):
        UserProfile.objects.filter(user=self.user).update(birthday=datetime.date(2004, 7, 9))
        response = self.client.get(reverse('account_settings'))
        self.assertContains(response, 'name="birthday"')
        self.assertContains(response, 'value="2004-07-09"')

    def test_it_can_be_added_changed_and_cleared(self):
        self.post(birthday='2004-07-09')
        self.assertEqual(self.birthday(), datetime.date(2004, 7, 9))
        self.post(birthday='2003-01-02')
        self.assertEqual(self.birthday(), datetime.date(2003, 1, 2))
        self.post(birthday='')
        self.assertIsNone(self.birthday())

    def test_a_bad_birthday_is_refused_and_nothing_else_is_saved(self):
        for bad in ('2999-01-01', 'not-a-date', '2005-02-30'):
            response = self.post(birthday=bad, first_name='Changed')
            self.assertEqual(response.status_code, 302, bad)
            self.assertIsNone(self.birthday(), bad)
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, 'Lena')

    def test_a_form_that_does_not_send_it_leaves_it_alone(self):
        UserProfile.objects.filter(user=self.user).update(birthday=datetime.date(2004, 7, 9))
        self.post()
        self.assertEqual(self.birthday(), datetime.date(2004, 7, 9))

    def test_an_older_out_of_range_value_does_not_block_saving_the_page(self):
        odd = timezone.localdate() - datetime.timedelta(days=365 * 5)
        UserProfile.objects.filter(user=self.user).update(birthday=odd)
        self.post(birthday=odd.isoformat(), nickname='Len')
        self.assertEqual(UserProfile.objects.get(user=self.user).nickname, 'Len')
