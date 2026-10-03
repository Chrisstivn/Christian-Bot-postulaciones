import unittest

from application_agent.confidence_engine import ConfidenceDecision
from application_agent.form_engine import answer_for_decision


class Candidate:
    field_type = "text"
    label = "Email"
    aria_label = ""
    placeholder = ""
    name = "email"
    element_id = ""
    nearby_text = ""

    @property
    def match_text(self):
        return self.label


class MappingTests(unittest.TestCase):
    def test_personal_email_mapping(self):
        decision = ConfidenceDecision(
            field_type="email",
            confidence=0.98,
            action="auto_fill",
            reason="test",
            original_field="Email",
            answer_key="email",
        )
        value = answer_for_decision(
            Candidate(),
            decision,
            {"email": "candidate@example.com"},
            [],
            "motivation",
            "experience",
            "",
            "",
        )
        self.assertEqual(value, "candidate@example.com")

    def test_cover_letter_mapping(self):
        decision = ConfidenceDecision(
            field_type="cover_letter",
            confidence=0.90,
            action="auto_fill",
            reason="test",
            original_field="Why us?",
            answer_key="cover_letter",
        )
        value = answer_for_decision(Candidate(), decision, {}, [], "I am motivated.", "Experience.", "", "")
        self.assertEqual(value, "I am motivated.")


if __name__ == "__main__":
    unittest.main()
