import unittest
from unittest.mock import Mock, patch
import scraper


class CompanyEnrichmentTests(unittest.TestCase):
    def setUp(self):
        scraper._public_company_text.cache_clear()

    def test_only_employer_top_card_is_followed_and_cached(self):
        response = Mock(status_code=200, text='<section data-test-id="about-us">Company size\n201-500 employees</section>')
        html = '<a class="topcard__org-name-link" href="https://cl.linkedin.com/company/example?tracking=1">Example</a>'
        with patch.object(scraper.requests, 'get', return_value=response) as get:
            self.assertIn('201-500 employees', scraper.linkedin_company_text(html))
            scraper.linkedin_company_text(html)
        get.assert_called_once_with('https://www.linkedin.com/company/example',
                headers=scraper.HEADERS, timeout=10, allow_redirects=False)

    def test_unrelated_links_login_redirects_and_missing_about_are_unknown(self):
        for url in ('http://127.0.0.1/company/x', 'https://linkedin.com.evil.test/company/x',
                    'https://www.linkedin.com/jobs/view/123'):
            with patch.object(scraper.requests, 'get') as get:
                self.assertEqual(scraper.linkedin_company_text(f'<a class="topcard__org-name-link" href="{url}">X</a>'), '')
                get.assert_not_called()
        for status, html in ((302, ''), (200, '<html>Sign in</html>')):
            scraper._public_company_text.cache_clear()
            with patch.object(scraper.requests, 'get', return_value=Mock(status_code=status, text=html)):
                self.assertEqual(scraper._public_company_text('https://www.linkedin.com/company/example'), '')
