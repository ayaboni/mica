from django.test import SimpleTestCase

from .admin_api import _csv_safe


class CsvSafeTests(SimpleTestCase):
    def test_formula_prefixes_are_neutralised(self):
        for bad in ("=1+1", "+1", "-1", "@SUM(A1)", "\tcmd", "\rcmd"):
            self.assertEqual(_csv_safe(bad), "'" + bad)

    def test_normal_text_is_unchanged(self):
        self.assertEqual(_csv_safe("Santos, Ralph Geo"), "Santos, Ralph Geo")
        self.assertEqual(_csv_safe(None), "")
