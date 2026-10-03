import unittest

from application_agent.form_engine import detect_blocker


class FakeElement:
    def __init__(self, visible=True):
        self._visible = visible

    def is_visible(self):
        return self._visible


class FakeLocator:
    def __init__(self, visible=False):
        self._visible = visible

    @property
    def first(self):
        return self

    def is_visible(self, timeout=0):
        return self._visible


class FakeFrame:
    def __init__(self, controls=0):
        self._controls = controls

    def query_selector_all(self, selector):
        if selector:
            return [FakeElement(True) for _ in range(self._controls)]
        return []


class FakePage:
    def __init__(self, body="", visible_selectors=None, controls=0):
        self._body = body
        self._visible_selectors = set(visible_selectors or [])
        self.frames = [FakeFrame(controls)]

    def inner_text(self, selector, timeout=0):
        return self._body

    def locator(self, selector):
        return FakeLocator(selector in self._visible_selectors)


class BlockerDetectionTests(unittest.TestCase):
    def test_captcha_words_in_content_do_not_block_visible_form(self):
        page = FakePage(
            body="This page mentions recaptcha, hcaptcha and turnstile in policy text.",
            controls=4,
        )

        self.assertIsNone(detect_blocker(page))

    def test_passive_recaptcha_badge_does_not_block_visible_form(self):
        page = FakePage(
            body="Apply now",
            visible_selectors={"iframe[src*='recaptcha']"},
            controls=4,
        )

        self.assertIsNone(detect_blocker(page))

    def test_active_captcha_challenge_blocks(self):
        page = FakePage(
            body="Apply now",
            visible_selectors={"iframe[src*='recaptcha/api2/bframe']"},
            controls=4,
        )

        self.assertEqual(detect_blocker(page), "captcha")

    def test_login_text_does_not_block_visible_form(self):
        page = FakePage(body="Login or create account optional footer text.", controls=3)

        self.assertIsNone(detect_blocker(page))

    def test_visible_password_blocks(self):
        page = FakePage(
            body="Sign in",
            visible_selectors={"input[type='password']"},
            controls=0,
        )

        self.assertEqual(detect_blocker(page), "login_required")


if __name__ == "__main__":
    unittest.main()
