import unittest
from unittest.mock import Mock, patch

import scraper


class LinkedInSingleJobScrapeGuardTests(unittest.TestCase):
    def test_extracts_numeric_job_id_from_slugged_url(self):
        url = (
            "https://de.linkedin.com/jobs/view/"
            "product-owner-berlin-mobility-startup-at-getaway-pro-4435966257"
        )
        self.assertEqual(scraper._linkedin_job_id(url), "4435966257")
        self.assertTrue(scraper._is_linkedin_job_url(url))

    def test_guest_job_endpoint_does_not_follow_redirects(self):
        response = Mock()
        response.status_code = 200
        response.text = "<html>single job</html>"
        response.raise_for_status.return_value = None

        with patch.object(scraper.requests, "get", return_value=response) as get:
            html = scraper._fetch_linkedin_guest_job_html(
                "https://de.linkedin.com/jobs/view/example-role-4435966257"
            )

        self.assertEqual(html, response.text)
        args, kwargs = get.call_args
        self.assertEqual(
            args[0],
            "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/4435966257",
        )
        self.assertFalse(kwargs["allow_redirects"])

    def test_direct_job_rejects_redirect_to_search_page(self):
        response = Mock()
        response.status_code = 200
        response.url = "https://de.linkedin.com/jobs/search/?keywords=product"
        response.text = "<html>" + ("many jobs " * 200) + "</html>"
        response.raise_for_status.return_value = None

        with patch.object(scraper.requests, "get", return_value=response):
            with self.assertRaisesRegex(ValueError, "redirigió el job"):
                scraper._fetch_linkedin_direct_job_html(
                    "https://de.linkedin.com/jobs/view/example-role-4435966257"
                )



    def test_extracts_work_format_from_linkedin_structured_metadata(self):
        cases = [
            ('<script>{"workPlaceTypes":["On-Site"]}</script>', "On-site"),
            ('<script>{"workplaceTypes":["Hybrid"]}</script>', "Hybrid"),
            ('<script>{"workplaceType":"Remote"}</script>', "Remote"),
            ('<script>{"jobLocationType":"TELECOMMUTE"}</script>', "Remote"),
            ('<div>#LI-Onsite</div>', "On-site"),
            ('<div>#LI-Hybrid</div>', "Hybrid"),
            ('<div>#LI-Remote</div>', "Remote"),
            ('<span data-workplace-type="true">On-site</span>', "On-site"),
        ]
        for html, expected in cases:
            with self.subTest(html=html):
                self.assertEqual(
                    scraper.extract_linkedin_work_format_from_html(html),
                    expected,
                )

    def test_linkedin_metadata_unknown_does_not_guess(self):
        html = "<html><body><div>Berlin, Germany</div><div>Full-time</div></body></html>"
        self.assertEqual(
            scraper.extract_linkedin_work_format_from_html(html),
            "Unknown",
        )

    def test_linkedin_scrape_enriches_format_from_verified_direct_page(self):
        guest_html = (
            "<html><body>"
            + ("Exact single job description without workplace metadata. " * 20)
            + "</body></html>"
        )
        direct_html = (
            '<html><body><script>{"workPlaceTypes":["On-Site"]}</script>'
            + ("Exact single job description without workplace metadata. " * 20)
            + "</body></html>"
        )

        with patch.object(
            scraper,
            "_fetch_linkedin_guest_job_html",
            return_value=guest_html,
        ), patch.object(
            scraper,
            "_fetch_linkedin_direct_job_html",
            return_value=direct_html,
        ) as direct:
            result = scraper.scrape_job_posting(
                "https://de.linkedin.com/jobs/view/example-role-4435966257"
            )

        self.assertEqual(result["work_format"], "On-site")
        self.assertIn("Exact single job description", result["text"])
        direct.assert_called_once()

    def test_linkedin_scrape_enriches_format_from_rendered_badge(self):
        html = (
            "<html><body>"
            + ("Exact single job description without workplace metadata. " * 20)
            + "</body></html>"
        )

        with patch.object(
            scraper,
            "_fetch_linkedin_guest_job_html",
            return_value=html,
        ), patch.object(
            scraper,
            "_fetch_linkedin_direct_job_html",
            return_value=html,
        ), patch.object(
            scraper,
            "_extract_linkedin_rendered_work_format",
            return_value="On-site",
        ) as rendered:
            result = scraper.scrape_job_posting(
                "https://de.linkedin.com/jobs/view/example-role-4435966257"
            )

        self.assertEqual(result["work_format"], "On-site")
        rendered.assert_called_once()

    def test_linkedin_guest_metadata_avoids_extra_direct_request(self):
        guest_html = (
            '<html><body><script>{"workPlaceTypes":["Hybrid"]}</script>'
            + ("Exact single job description. " * 30)
            + "</body></html>"
        )

        with patch.object(
            scraper,
            "_fetch_linkedin_guest_job_html",
            return_value=guest_html,
        ), patch.object(
            scraper,
            "_fetch_linkedin_direct_job_html",
        ) as direct:
            result = scraper.scrape_job_posting(
                "https://de.linkedin.com/jobs/view/example-role-4435966257"
            )

        self.assertEqual(result["work_format"], "Hybrid")
        direct.assert_not_called()

    def test_detects_explicit_closed_application_marker(self):
        text = (
            "Growth Strategy Manager\n"
            "Numa\n"
            "No longer accepting applications\n"
            "About the role"
        )
        self.assertEqual(
            scraper.find_closed_application_marker(text),
            "no longer accepting applications",
        )

    def test_open_job_text_has_no_closed_marker(self):
        text = (
            "SEO, AI Search and Website Manager\n"
            "Apply now\n"
            "About the role"
        )
        self.assertEqual(scraper.find_closed_application_marker(text), "")

    def test_linkedin_scrape_uses_verified_single_job_html(self):
        html = "<html><body>" + ("Exact single job description. " * 40) + "</body></html>"

        with patch.object(
            scraper,
            "_fetch_linkedin_guest_job_html",
            return_value=html,
        ), patch.object(
            scraper,
            "_fetch_linkedin_direct_job_html",
            return_value=html,
        ), patch.object(
            scraper,
            "_extract_linkedin_rendered_work_format",
            return_value="Unknown",
        ), patch.object(
            scraper,
            "scrape_dynamic_playwright",
        ) as playwright:
            result = scraper.scrape_job_posting(
                "https://de.linkedin.com/jobs/view/example-role-4435966257"
            )

        self.assertIn("Exact single job description.", result["text"])
        playwright.assert_not_called()

    def test_linkedin_scrape_never_accepts_generic_playwright_fallback(self):
        with patch.object(
            scraper,
            "_fetch_linkedin_guest_job_html",
            side_effect=ValueError("guest unavailable"),
        ), patch.object(
            scraper,
            "_fetch_linkedin_direct_job_html",
            side_effect=ValueError("redirected to search"),
        ), patch.object(
            scraper,
            "_extract_linkedin_rendered_work_format",
            return_value="Unknown",
        ), patch.object(
            scraper,
            "scrape_dynamic_playwright",
        ) as playwright:
            with self.assertRaisesRegex(ValueError, "de forma segura"):
                scraper.scrape_job_posting(
                    "https://de.linkedin.com/jobs/view/example-role-4435966257"
                )

        playwright.assert_not_called()


if __name__ == "__main__":
    unittest.main()
