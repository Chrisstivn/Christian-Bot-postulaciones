import unittest

from candidate_bible import load_candidate_bible
from application_agent import deterministic_answers


PERSONAL = {
    "first_name": "Christian",
    "last_name": "Example",
    "email": "candidate@example.com",
    "phone": "+49 157 000000",
    "linkedin": "https://www.linkedin.com/in/example/",
    "location": "Berlin, Germany",
    "available_from": "Immediately",
    "expected_salary": "60000",
}


class DeterministicAnswerTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.bible = load_candidate_bible()

    def resolve(self, question, cv_text=""):
        return deterministic_answers.resolve_known_answer(
            question,
            PERSONAL,
            self.bible,
            cv_text,
        )

    def test_sponsorship_is_no_not_work_authorization_yes(self):
        answer = self.resolve(
            "Will you now or in the future require visa sponsorship?"
        )
        self.assertIsNotNone(answer)
        self.assertEqual(answer.value, "No")
        self.assertEqual(answer.key, "sponsorship_required")

    def test_work_authorization_is_yes(self):
        answer = self.resolve(
            "Are you legally authorized to work in Germany?"
        )
        self.assertIsNotNone(answer)
        self.assertEqual(answer.value, "Yes")

    def test_country_of_residence_is_germany(self):
        answer = self.resolve("What is your country of residence?")
        self.assertIsNotNone(answer)
        self.assertEqual(answer.value, "Germany")

    def test_english_fluency_from_c1_is_yes(self):
        answer = self.resolve("Are you fluent in English?")
        self.assertIsNotNone(answer)
        self.assertEqual(answer.value, "Yes")

    def test_german_fluency_from_a2_is_no(self):
        answer = self.resolve("Are you fluent in German?")
        self.assertIsNotNone(answer)
        self.assertEqual(answer.value, "No")

    def test_years_threshold_uses_candidate_bible(self):
        answer = self.resolve(
            "Do you have at least 5 years of professional experience?"
        )
        self.assertIsNotNone(answer)
        self.assertEqual(answer.value, "Yes")

    def test_specific_tool_years_are_not_replaced_with_total_career_years(self):
        answer = self.resolve(
            "How many years of experience with SQL do you have?",
            cv_text="Skills: SQL, Power BI.",
        )
        self.assertIsNone(answer)

    def test_cv_fact_presence_can_answer_skill_yes(self):
        answer = self.resolve(
            "Do you have experience with SQL?",
            cv_text="Skills: Power BI, SQL, RStudio, QlikView.",
        )
        self.assertIsNotNone(answer)
        self.assertEqual(answer.value, "Yes")
        self.assertEqual(answer.source, "cv_fact_presence")

    def test_missing_skill_does_not_invent_no(self):
        answer = self.resolve(
            "Do you have experience with Kubernetes?",
            cv_text="Skills: Power BI, SQL, RStudio, QlikView.",
        )
        self.assertIsNone(answer)


class ClosedChoiceMatchingTests(unittest.TestCase):
    def test_yes_no_are_exact_semantic_matches(self):
        self.assertEqual(
            deterministic_answers.option_match_score(
                "Do you require sponsorship?", "No", "No"
            ),
            1.0,
        )
        self.assertEqual(
            deterministic_answers.option_match_score(
                "Are you authorized to work?", "Yes", "No"
            ),
            0.0,
        )

    def test_salary_selects_containing_range(self):
        idx, score = deterministic_answers.best_option_index(
            "Expected salary",
            "60000",
            ["Select...", "€40k-50k", "€50k-65k", "€65k-80k"],
        )
        self.assertEqual(idx, 2)
        self.assertGreaterEqual(score, 0.9)

    def test_salary_range_with_thousands_separator(self):
        idx, score = deterministic_answers.best_option_index(
            "Expected salary",
            "60000",
            ["€40,000 - €50,000", "€50,000 - €65,000", "€65,000 - €80,000"],
        )
        self.assertEqual(idx, 1)
        self.assertGreaterEqual(score, 0.9)

    def test_years_selects_containing_range(self):
        idx, score = deterministic_answers.best_option_index(
            "Years of experience",
            "6",
            ["0-2 years", "3-5 years", "6-10 years", "10+ years"],
        )
        self.assertEqual(idx, 2)
        self.assertGreaterEqual(score, 0.9)

    def test_c1_maps_to_advanced_or_fluent_option(self):
        idx, score = deterministic_answers.best_option_index(
            "English proficiency",
            "C1",
            ["Basic", "Intermediate", "Advanced / Fluent", "Native"],
        )
        self.assertEqual(idx, 2)
        self.assertGreaterEqual(score, 0.9)

    def test_sensitive_field_prefers_decline(self):
        idx, score = deterministic_answers.best_option_index(
            "Gender",
            "prefer not to say",
            ["Female", "Male", "Prefer not to say"],
            sensitive=True,
        )
        self.assertEqual(idx, 2)
        self.assertEqual(score, 1.0)

    def test_required_unknown_does_not_fall_back_to_first_option(self):
        idx, score = deterministic_answers.best_option_index(
            "Unrecognized mandatory question",
            "unrelated answer",
            ["Option A", "Option B", "Option C"],
        )
        self.assertIsNone(idx)
        self.assertLess(score, 0.62)


if __name__ == "__main__":
    unittest.main()
