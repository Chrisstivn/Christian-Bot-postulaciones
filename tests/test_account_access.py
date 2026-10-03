import os
import tempfile
import unittest
from unittest.mock import patch

from application_agent import account_access


class VerificationParsingTests(unittest.TestCase):
    def test_extracts_verification_code_near_keyword(self):
        self.assertEqual(
            account_access._extract_verification_code(
                "Your verification code is 482731. It expires in 10 minutes."
            ),
            "482731",
        )

    def test_extracts_magic_link(self):
        link = account_access._extract_verification_link(
            "Confirm your email: https://careers.example.com/verify?token=abc123"
        )
        self.assertEqual(
            link,
            "https://careers.example.com/verify?token=abc123",
        )


class PasswordStrategyTests(unittest.TestCase):
    class FakePage:
        def __init__(self, url):
            self.url = url

    def test_existing_linkedin_account_requires_explicit_password(self):
        with patch.dict(
            os.environ,
            {
                "LINKEDIN_PASSWORD": "",
                "APPLICATION_ACCOUNT_PASSWORD": "",
                "APPLICATION_SITE_PASSWORDS_JSON": "",
            },
            clear=False,
        ):
            self.assertEqual(
                account_access._site_password(
                    self.FakePage("https://www.linkedin.com/jobs/view/123")
                ),
                "",
            )

    def test_new_sites_get_stable_domain_password(self):
        with tempfile.TemporaryDirectory() as tmp:
            secret_path = os.path.join(tmp, "secret")
            with patch.object(account_access, "_SECRET_PATH", account_access.Path(secret_path)):
                with patch.dict(
                    os.environ,
                    {
                        "APPLICATION_ACCOUNT_MASTER_SECRET": "test-master-secret",
                        "APPLICATION_ACCOUNT_PASSWORD": "",
                        "APPLICATION_SITE_PASSWORDS_JSON": "",
                    },
                    clear=False,
                ):
                    page = self.FakePage("https://careers.example.com/apply/123")
                    first = account_access._site_password(page)
                    second = account_access._site_password(page)

        self.assertEqual(first, second)
        self.assertGreaterEqual(len(first), 12)
        self.assertTrue(any(ch.isupper() for ch in first))
        self.assertTrue(any(ch.islower() for ch in first))
        self.assertTrue(any(ch.isdigit() for ch in first))


if __name__ == "__main__":
    unittest.main()
