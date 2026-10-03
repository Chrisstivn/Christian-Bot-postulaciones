import unittest
from unittest.mock import patch

import main


class ExistingStatusSafetyTests(unittest.TestCase):
    def test_10_ready_for_review_status_is_untouched(self):
        existing = {"status": "ready_for_review", "job_url": "https://example.com/job"}
        with patch.object(main.queue_service, "get_by_url", return_value=existing), \
             patch.object(main.queue_service, "upsert_triage_result") as upsert, \
             patch.object(main.scraper, "scrape_job_posting") as scrape:
            result = main._triage_job_url(existing["job_url"])

        self.assertEqual(result["status"], "duplicate")
        self.assertEqual(result["previous_status"], "ready_for_review")
        upsert.assert_not_called()
        scrape.assert_not_called()

    def test_11_done_status_is_untouched(self):
        existing = {"status": "done", "job_url": "https://example.com/job"}
        with patch.object(main.queue_service, "get_by_url", return_value=existing), \
             patch.object(main.queue_service, "upsert_triage_result") as upsert, \
             patch.object(main.scraper, "scrape_job_posting") as scrape:
            result = main._triage_job_url(existing["job_url"])

        self.assertEqual(result["status"], "duplicate")
        self.assertEqual(result["previous_status"], "done")
        upsert.assert_not_called()
        scrape.assert_not_called()

    def test_12_db_seen_job_is_not_returned_as_new_even_if_sheet_deleted(self):
        url = "https://de.linkedin.com/jobs/view/data-analyst-at-example-4430000000"
        with patch.object(
            main.scraper,
            "scrape_linkedin_search_results",
            return_value=[url],
        ), patch.object(
            main.queue_service,
            "get_by_url",
            return_value={"status": "ready_for_review", "job_url": url},
        ):
            payload = main.LinkedInSearchInput(search_url="https://de.linkedin.com/jobs/search/")
            result = main.linkedin_search(payload)

        self.assertEqual(result["new_job_urls"], [])
        self.assertEqual(result["new_count"], 0)

    def test_hybrid_non_senior_analyst_rejects_without_fit_or_salary_llm(self):
        from models import JobExtraction

        url = "https://de.linkedin.com/jobs/view/data-analyst-at-example-4430000001"
        job_text = "Permanent contract. Hybrid. Salary: €85,000 EUR per year."
        extraction = JobExtraction(
            company="Example",
            job_title="Data Analyst",
            location="Berlin",
            german_required="NO",
            detected_language="EN",
        )
        with patch.object(main.queue_service, "get_by_url", return_value=None), \
             patch.object(main.scraper, "scrape_job_posting", return_value={"text": job_text, "work_format": "Hybrid"}), \
             patch.object(main.gemini_service, "extract_job_info", return_value=extraction), \
             patch.object(main.gemini_service, "evaluate_role_upgrade_semantics") as upgrade_llm, \
             patch.object(main.gemini_service, "evaluate_candidate_fit") as fit, \
             patch.object(main.queue_service, "upsert_triage_result") as upsert:
            result = main._triage_job_url(url)

        self.assertEqual(result["status"], "discarded")
        self.assertEqual(result["rejected_reason"], "analyst_not_remote")
        upgrade_llm.assert_not_called()
        fit.assert_not_called()
        self.assertEqual(upsert.call_args.kwargs["status"], "discarded")


    def test_senior_hybrid_goes_ready_without_candidate_fit(self):
        from models import JobExtraction

        url = "https://de.linkedin.com/jobs/view/senior-data-analyst-at-example-4430000002"
        job_text = "Permanent contract. Hybrid. Salary: €40,000 EUR per year."
        extraction = JobExtraction(
            company="Example",
            job_title="Senior Data Analyst",
            location="Berlin",
            german_required="NO",
            detected_language="EN",
        )
        with patch.object(main.queue_service, "get_by_url", return_value=None), \
             patch.object(main.scraper, "scrape_job_posting", return_value={"text": job_text, "work_format": "Hybrid"}), \
             patch.object(main.gemini_service, "extract_job_info", return_value=extraction), \
             patch.object(main.gemini_service, "evaluate_role_upgrade_semantics") as upgrade_llm, \
             patch.object(main.gemini_service, "evaluate_candidate_fit") as fit, \
             patch.object(main, "_load_cv_maestro_text") as load_cv, \
             patch.object(main, "load_candidate_bible") as load_bible, \
             patch.object(main.queue_service, "upsert_triage_result") as upsert:
            result = main._triage_job_url(url)

        self.assertEqual(result["status"], "ready")
        upgrade_llm.assert_not_called()
        fit.assert_not_called()
        load_cv.assert_not_called()
        load_bible.assert_not_called()
        self.assertIsNone(upsert.call_args.kwargs["candidate_fit_decision"])
        self.assertEqual(upsert.call_args.kwargs["status"], "ready_for_review")


    def test_automatic_search_does_not_reanalyse_old_failed_rows(self):
        url = "https://de.linkedin.com/jobs/view/data-analyst-at-example-4430000003"
        with patch.object(
            main.scraper,
            "scrape_linkedin_search_results",
            return_value=[url],
        ), patch.object(
            main.queue_service,
            "enqueue_triage_urls",
            return_value={"inserted": 0, "skipped_duplicates": 1, "total": 1},
        ), patch.object(
            main.queue_service,
            "get_by_url",
            return_value={"status": "failed", "job_url": url},
        ), patch.object(
            main,
            "_triage_job_url",
        ) as triage:
            payload = main.LinkedInSearchInput(
                search_url="https://de.linkedin.com/jobs/search/"
            )
            result = main.linkedin_search_and_triage(payload)

        self.assertEqual(result["triage_count"], 0)
        self.assertEqual(result["new_count"], 0)
        triage.assert_not_called()

    def test_13_vp_title_remains_banned(self):
        self.assertEqual(main._excluded_job_title_reason("VP, Data & Analytics"), "vp")
        self.assertEqual(
            main._excluded_job_title_reason("Vice President Data"),
            "vice_president",
        )

    def test_14_working_student_remains_banned(self):
        self.assertEqual(
            main._excluded_job_title_reason("Working Student Data Analyst"),
            "working_student",
        )

    def test_hard_banned_titles_stop_before_upgrade_and_fit_llms(self):
        from models import JobExtraction

        cases = (
            ("Junior Data Analyst", "junior"),
            ("VP, Data & Analytics", "vp"),
            ("Working Student Data Analyst", "working_student"),
        )
        for idx, (title, excluded_reason) in enumerate(cases):
            with self.subTest(title=title):
                url = f"https://de.linkedin.com/jobs/view/test-{idx}-443100000{idx}"
                extraction = JobExtraction(
                    company="Example",
                    job_title=title,
                    location="Berlin",
                    german_required="NO",
                    detected_language="EN",
                )
                with patch.object(main.queue_service, "get_by_url", return_value=None), \
                     patch.object(
                         main.scraper,
                         "scrape_job_posting",
                         return_value={"text": "English job description.", "work_format": "Hybrid"},
                     ), \
                     patch.object(main.gemini_service, "extract_job_info", return_value=extraction), \
                     patch.object(main.gemini_service, "evaluate_role_upgrade_semantics") as upgrade_llm, \
                     patch.object(main.gemini_service, "evaluate_candidate_fit") as fit_llm, \
                     patch.object(main.queue_service, "upsert_triage_result") as upsert:
                    result = main._triage_job_url(url)

                self.assertEqual(result["status"], "discarded")
                self.assertEqual(result["rejected_reason"], "existing_banned_title")
                self.assertEqual(result["reason"], f"excluded_job_title:{excluded_reason}")
                upgrade_llm.assert_not_called()
                fit_llm.assert_not_called()
                self.assertEqual(upsert.call_args.kwargs["status"], "discarded")


    def test_title_language_requirement_rejects_unsupported_languages(self):
        cases = (
            ("Customer Success Manager - Dutch Speaking", "dutch"),
            ("Customer Success Manager - Croatian Speaker", "croatian"),
            ("Account Manager - German-speaking", "german"),
            ("Sales Manager - Fluent French", "french"),
            ("Growth Manager - Italian C2", "italian"),
        )
        for title, expected in cases:
            with self.subTest(title=title):
                self.assertEqual(
                    main._unsupported_required_language_reason(title),
                    expected,
                )

    def test_title_language_requirement_allows_english_and_spanish(self):
        allowed = (
            "Customer Success Manager - English Speaking",
            "Growth Manager - Spanish Speaking",
            "Account Manager - Native English",
            "CRM Manager - Fluent Spanish",
        )
        for title in allowed:
            with self.subTest(title=title):
                self.assertIsNone(
                    main._unsupported_required_language_reason(title)
                )

    def test_market_name_alone_is_not_treated_as_language_requirement(self):
        self.assertIsNone(
            main._unsupported_required_language_reason("French Market Manager")
        )
        self.assertIsNone(
            main._unsupported_required_language_reason("DACH Growth Manager")
        )

    def test_existing_banned_title_keeps_precedence_over_language_requirement(self):
        self.assertEqual(
            main._excluded_job_title_reason("Junior Data Analyst - Dutch Speaking"),
            "junior",
        )

    def test_unsupported_title_language_stops_before_upgrade_and_fit_llms(self):
        from models import JobExtraction

        url = "https://de.linkedin.com/jobs/view/customer-success-manager-4431000099"
        extraction = JobExtraction(
            company="Example",
            job_title="Customer Success Manager - Dutch Speaking",
            location="Berlin",
            german_required="NO",
            detected_language="EN",
        )

        with patch.object(main.queue_service, "get_by_url", return_value=None), \
             patch.object(
                 main.scraper,
                 "scrape_job_posting",
                 return_value={"text": "English job description.", "work_format": "Hybrid"},
             ), \
             patch.object(main.gemini_service, "extract_job_info", return_value=extraction), \
             patch.object(main.gemini_service, "evaluate_role_upgrade_semantics") as upgrade_llm, \
             patch.object(main.gemini_service, "evaluate_candidate_fit") as fit_llm, \
             patch.object(main.queue_service, "upsert_triage_result") as upsert:
            result = main._triage_job_url(url)

        self.assertEqual(result["status"], "discarded")
        self.assertEqual(
            result["rejected_reason"],
            "unsupported_required_language",
        )
        self.assertEqual(
            result["reason"],
            "unsupported_required_language:dutch",
        )
        upgrade_llm.assert_not_called()
        fit_llm.assert_not_called()
        self.assertEqual(upsert.call_args.kwargs["status"], "discarded")
        self.assertEqual(
            upsert.call_args.kwargs["rejected_reason"],
            "unsupported_required_language",
        )

    def test_explicit_low_salary_no_longer_rejects_senior_hybrid(self):
        from models import JobExtraction

        url = "https://de.linkedin.com/jobs/view/senior-data-analyst-4431000010"
        extraction = JobExtraction(
            company="Example",
            job_title="Senior Data Analyst",
            location="Berlin",
            german_required="NO",
            detected_language="EN",
        )
        job_text = "Permanent contract. Hybrid. Base salary: €50,000 EUR per year."
        with patch.object(main.queue_service, "get_by_url", return_value=None), \
             patch.object(main.scraper, "scrape_job_posting", return_value={"text": job_text, "work_format": "Hybrid"}), \
             patch.object(main.gemini_service, "extract_job_info", return_value=extraction), \
             patch.object(main.gemini_service, "evaluate_role_upgrade_semantics") as upgrade_llm, \
             patch.object(main.gemini_service, "evaluate_candidate_fit") as fit_llm, \
             patch.object(main.queue_service, "upsert_triage_result") as upsert:
            result = main._triage_job_url(url)

        self.assertEqual(result["status"], "ready")
        upgrade_llm.assert_not_called()
        fit_llm.assert_not_called()
        self.assertEqual(upsert.call_args.kwargs["status"], "ready_for_review")


    def test_junior_is_hard_banned_before_upgrade_filter(self):
        self.assertEqual(
            main._excluded_job_title_reason("Junior Data Analyst"),
            "junior",
        )


if __name__ == "__main__":
    unittest.main()
