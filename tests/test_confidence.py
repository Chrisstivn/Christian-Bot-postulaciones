import unittest

from application_agent.confidence_engine import classify_open_question, score_field


class Candidate:
    field_type = "text"
    aria_label = ""
    label = "Corporate Email"
    placeholder = ""
    name = "email"
    element_id = ""
    nearby_text = ""

    @property
    def match_text(self):
        return " ".join([self.aria_label, self.label, self.placeholder, self.name]).strip()


class ConfidenceTests(unittest.TestCase):
    def test_email_scores_high_confidence(self):
        decision = score_field(Candidate(), ats="greenhouse")
        self.assertEqual(decision.field_type, "email")
        self.assertGreaterEqual(decision.confidence, 0.85)
        self.assertEqual(decision.action, "auto_fill")

    def test_question_classification(self):
        self.assertEqual(classify_open_question("Why do you want this role?"), "motivation")
        self.assertEqual(classify_open_question("Tell us about your biggest achievement"), "achievement")
        self.assertEqual(classify_open_question("Describe a challenge you solved"), "star_behavioral")


if __name__ == "__main__":
    unittest.main()
