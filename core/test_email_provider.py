from django.core.mail import EmailMultiAlternatives
from django.test import SimpleTestCase, override_settings

from . import views


class LogoUrlTests(SimpleTestCase):
    def message(self):
        return EmailMultiAlternatives("s", "b", "a@example.com", ["b@example.com"])

    @override_settings(EMAIL_PROVIDER="", SITE_URL="https://mica.test")
    def test_smtp_embeds_the_logo(self):
        msg = self.message()
        self.assertEqual(views._logo_url(msg), "cid:mica-logo")
        self.assertEqual(len(msg.attachments), 1)

    @override_settings(EMAIL_PROVIDER="resend", SITE_URL="https://mica.test/")
    def test_https_provider_links_the_hosted_logo(self):
        msg = self.message()
        url = views._logo_url(msg)
        self.assertEqual(url, "https://mica.test/static/core/images/mica-logo/mica-logo-email.png")
        self.assertEqual(msg.attachments, [])

    @override_settings(EMAIL_PROVIDER="resend", SITE_URL="")
    def test_provider_without_site_url_falls_back_to_embedding(self):
        self.assertEqual(views._logo_url(self.message()), "cid:mica-logo")


class PlainTextEmailTests(SimpleTestCase):
    def test_every_plain_text_email_also_has_an_html_part(self):
        msg = views._plain_text_email("Hi", "Line one\n<b>two</b> & three", ["a@example.com"],
                                      reply_to=["r@example.com"])
        self.assertEqual(msg.body, "Line one\n<b>two</b> & three")
        self.assertEqual(msg.reply_to, ["r@example.com"])
        html, mimetype = msg.alternatives[0][:2]
        self.assertEqual(mimetype, "text/html")
        self.assertIn("&lt;b&gt;two&lt;/b&gt; &amp; three", html)    # escaped, not injected
        self.assertNotIn("<b>", html)
