import unittest

from candidate_bible import CandidateBible


class CandidateBibleTests(unittest.TestCase):
    def test_semantic_lookup_english(self):
        bible = CandidateBible(
            {
                "languages": {"english": {"level": "C1"}},
                "personal": {"availability": "Immediately"},
            }
        )
        key, value, score = bible.semantic_lookup("How fluent are you in English?")
        self.assertEqual(key, "languages.english.level")
        self.assertEqual(value, "C1")
        self.assertGreaterEqual(score, 0.58)

    def test_gemini_context_contains_truths(self):
        bible = CandidateBible({"personal": {"full_name": "Christian"}})
        self.assertIn("personal.full_name", bible.to_gemini_context())

    def test_autofill_lookup_boolean(self):
        bible = CandidateBible({"autofill": {"default_answers": {"willing_to_relocate": "No"}}})
        key, value, score = bible.autofill_lookup("Are you willing to relocate?")
        self.assertEqual(key, "autofill.default_answers.willing_to_relocate")
        self.assertEqual(value, "No")
        self.assertGreaterEqual(score, 0.56)


if __name__ == "__main__":
    unittest.main()
