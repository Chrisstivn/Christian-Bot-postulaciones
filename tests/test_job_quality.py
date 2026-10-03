import unittest

import job_quality


class TitleAndWorkplacePolicyTests(unittest.TestCase):
    """The user preselects LinkedIn URLs; no pay, seniority-AI or CV-fit filter."""

    def decision(self, title, workplace, description="Permanent contract."):
        return job_quality.evaluate_upgrade(
            company="Example",
            job_title=title,
            job_text=description,
            work_format=workplace,
        )

    def test_analyst_only_remote(self):
        for workplace, expected in (
            ("Remote", job_quality.KEEP),
            ("Hybrid", job_quality.REJECT),
            ("On-site", job_quality.REJECT),
        ):
            with self.subTest(workplace=workplace):
                actual = self.decision("Data Analyst", workplace)
                self.assertEqual(actual.decision, expected)
        self.assertEqual(
            self.decision("Data Analyst", "Hybrid").rejected_reason,
            "analyst_not_remote",
        )

    def test_all_analyst_specialties_follow_analyst_policy(self):
        for title in ("Business Analyst", "Marketing Analyst", "Data Operations Analyst"):
            with self.subTest(title=title):
                self.assertEqual(self.decision(title, "Hybrid").decision, job_quality.REJECT)
                self.assertEqual(self.decision(title, "Remote").decision, job_quality.KEEP)

    def test_senior_analyst_remote_or_hybrid_but_not_onsite(self):
        for workplace, expected in (
            ("Remote", job_quality.KEEP),
            ("Hybrid", job_quality.KEEP),
            ("On-site", job_quality.REJECT),
        ):
            with self.subTest(workplace=workplace):
                self.assertEqual(
                    self.decision("Senior Data Analyst", workplace).decision,
                    expected,
                )

    def test_sr_and_principal_analyst_count_as_senior(self):
        for title in ("Sr. Data Analyst", "Principal Product Analyst", "Staff BI Analyst"):
            with self.subTest(title=title):
                self.assertEqual(self.decision(title, "Hybrid").decision, job_quality.KEEP)
                self.assertEqual(self.decision(title, "On-site").decision, job_quality.REJECT)

    def test_every_manager_title_allows_every_workplace(self):
        titles = (
            "Marketing Manager",
            "Revenue Manager",
            "Operations Manager",
            "Category Manager",
            "Product Manager",
            "CRM Manager",
            "Strategy & Operations Manager",
        )
        for title in titles:
            for workplace in ("Remote", "Hybrid", "On-site"):
                with self.subTest(title=title, workplace=workplace):
                    result = self.decision(title, workplace)
                    self.assertEqual(result.decision, job_quality.KEEP)
                    self.assertEqual(result.comparison["level"], job_quality.ABOVE_BASELINE)

    def test_manager_does_not_require_gemini_scope_confirmation(self):
        decision = self.decision(
            "Growth Manager", "On-site",
            "Permanent. Routine dashboard reporting; salary €40,000 per year.",
        )
        self.assertFalse(decision.needs_semantic)
        self.assertEqual(decision.decision, job_quality.KEEP)

    def test_salary_is_ignored_even_when_explicit_or_passed(self):
        remote_analyst = self.decision(
            "Data Analyst", "Remote", "Permanent. Salary: €35,000 EUR per year."
        )
        senior_hybrid = job_quality.evaluate_upgrade(
            company="Example",
            job_title="Senior Analyst",
            job_text="Permanent. Salary: €48,000 EUR per year.",
            work_format="Hybrid",
            salary_eur=48_000,
            semantic={"level": "BELOW_BASELINE"},
        )
        onsite_manager = self.decision(
            "Product Manager", "On-site", "Permanent. €40,000 base salary."
        )
        for result in (remote_analyst, senior_hybrid, onsite_manager):
            self.assertEqual(result.decision, job_quality.KEEP)
            self.assertIsNone(result.comparison["expected_base_salary_eur"])
            self.assertEqual(result.comparison["salary_source"], "not_evaluated")

    def test_temporary_contract_always_rejected(self):
        for title, workplace, text in (
            ("Product Manager", "On-site", "Maternity cover"),
            ("Senior Analyst", "Hybrid", "Fixed-term 12-month contract"),
            ("Data Analyst", "Remote", "Temporary"),
        ):
            with self.subTest(title=title):
                actual = self.decision(title, workplace, text)
                self.assertEqual(actual.decision, job_quality.REJECT)
                self.assertEqual(actual.rejected_reason, "temporary_contract")

    def test_junior_never_passes(self):
        result = self.decision("Junior Data Analyst", "Remote")
        self.assertEqual(result.decision, job_quality.REJECT)
        self.assertEqual(result.rejected_reason, "below_current_role_baseline")

    def test_unknown_workplace_goes_to_review_not_discard(self):
        for title in ("Data Analyst", "Senior Product Analyst", "Marketing Manager"):
            with self.subTest(title=title):
                result = self.decision(title, "Unknown", "Permanent position")
                self.assertEqual(result.decision, job_quality.KEEP)
                self.assertTrue(result.comparison["requires_work_format_check"])

    def test_other_preselected_roles_remote_or_hybrid_not_onsite(self):
        for title in ("Product Owner", "Strategy Consultant", "Marketing Specialist"):
            with self.subTest(title=title):
                self.assertEqual(self.decision(title, "Hybrid").decision, job_quality.KEEP)
                self.assertEqual(self.decision(title, "On-site").decision, job_quality.REJECT)

    def test_linkedin_remote_metadata_is_authoritative(self):
        self.assertEqual(
            job_quality.classify_remote("Permanent. Flexible hours.", "Remote"),
            job_quality.FULLY_REMOTE,
        )
        self.assertEqual(
            job_quality.classify_remote("Remote work perk.", "On-site"),
            job_quality.ONSITE,
        )
        self.assertEqual(
            job_quality.classify_remote("Three days remote each week.", "Hybrid"),
            job_quality.HYBRID,
        )

    def test_explicit_mandatory_office_attendance_overrides_remote(self):
        self.assertEqual(
            job_quality.classify_remote(
                "100% remote, but 2 days per month in the office are required.", "Remote"
            ),
            job_quality.HYBRID,
        )
        self.assertEqual(
            job_quality.classify_remote("3 remote days and 2 days per week in the office"),
            job_quality.HYBRID,
        )

    def test_no_salary_threshold_remains_in_decision(self):
        self.assertEqual(job_quality.DATA_ANALYST_BASELINE["salary_eur"], 56_000)
        self.assertEqual(
            self.decision("Data Analyst", "Hybrid", "Permanent. Salary €150,000 EUR per year.").decision,
            job_quality.REJECT,
        )


if __name__ == "__main__":
    unittest.main()
