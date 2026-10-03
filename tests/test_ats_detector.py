import unittest

from application_agent.ats_detector import detect_from_text


class ATSDetectorTests(unittest.TestCase):
    def test_greenhouse_url_detection(self):
        result = detect_from_text("https://boards.greenhouse.io/acme/jobs/123", "")
        self.assertEqual(result.ats, "greenhouse")
        self.assertGreaterEqual(result.confidence, 0.5)

    def test_workday_html_detection(self):
        result = detect_from_text("https://careers.example.com", "<script src='wd-careers.js'></script>")
        self.assertEqual(result.ats, "workday")
        self.assertGreaterEqual(result.confidence, 0.25)


    def test_current_feed_url_families(self):
        cases = [
            ("https://jobs.ashbyhq.com/babbel/abc/application?utm_source=LinkedInPosting", "", "ashby"),
            ("https://account.amazon.jobs/en-US/applicant/jobs/10465026/apply", "", "amazonjobs"),
            ("https://career55.sapsf.eu/careers?company=bestsecret", "", "successfactors"),
            ("https://career4.successfactors.com/careers?company=CNGPROD", "", "successfactors"),
            ("https://join.com/companies/bsimerch/16659386/apply/cv", "", "join"),
            ("https://contorion.jobs.personio.de/job/2791024/apply", "", "personio"),
            ("https://canadiansolar.wd5.myworkdayjobs.com/eStorage/job/x/apply", "", "workday"),
            ("https://job-boards.greenhouse.io/commercetools/jobs/7893961003", "", "greenhouse"),
            ("https://heydata.teamtailor.com/jobs/8322861-gtm-operations-specialist", "", "teamtailor"),
            ("https://kleinanzeigendegmbh.careers.hibob.com/jobs/abc/apply", "", "hibob"),
            ("https://de.linkedin.com/jobs/view/product-manager-at-ai-republic-4463975295", "", "linkedin"),
            ("https://www.lucanet.com/en/careers/jobs/?gh_jid=4965834101&gh_src=x", "", "greenhouse"),
            (
                "https://careers.intric.ai/jobs/7230344-customer-engagement-manager-ai-adoption",
                "<script src='https://cdn.teamtailor.com/assets/application.js'></script>",
                "teamtailor",
            ),
        ]
        for url, html, expected in cases:
            with self.subTest(url=url):
                self.assertEqual(detect_from_text(url, html).ats, expected)

    def test_unknown_detection(self):
        result = detect_from_text("https://example.com/jobs", "<html></html>")
        self.assertEqual(result.ats, "unknown")


if __name__ == "__main__":
    unittest.main()
