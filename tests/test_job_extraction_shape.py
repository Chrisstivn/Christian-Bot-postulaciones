import unittest
from unittest.mock import patch

import gemini_service


class JobExtractionShapeTests(unittest.TestCase):
    def test_triage_accepts_singleton_list_wrapping(self):
        payload = [{
            "company": "Example GmbH",
            "job_title": "Growth Manager",
            "location": "Berlin",
            "german_required": "NO",
            "detected_language": "EN",
        }]

        with patch.object(gemini_service, "_call_gemini_json", return_value=payload):
            result = gemini_service.extract_job_info(
                "Example job text",
                include_questions=False,
            )

        self.assertEqual(result.company, "Example GmbH")
        self.assertEqual(result.job_title, "Growth Manager")
        self.assertEqual(result.detected_language, "EN")
        self.assertEqual(result.questions, [])

    def test_triage_rejects_ambiguous_multi_item_list(self):
        payload = [
            {
                "company": "Example GmbH",
                "job_title": "Growth Manager",
                "location": "Berlin",
                "german_required": "NO",
                "detected_language": "EN",
            },
            {
                "company": "Other GmbH",
                "job_title": "Marketing Manager",
                "location": "Berlin",
                "german_required": "NO",
                "detected_language": "EN",
            },
        ]

        with patch.object(gemini_service, "_call_gemini_json", return_value=payload):
            with self.assertRaisesRegex(ValueError, "elementos=2"):
                gemini_service.extract_job_info(
                    "Example job text",
                    include_questions=False,
                )

    def test_normal_object_behavior_is_unchanged(self):
        payload = {
            "company": "Example GmbH",
            "job_title": "Growth Manager",
            "location": "Berlin",
            "german_required": "NO",
            "detected_language": "EN",
        }

        with patch.object(gemini_service, "_call_gemini_json", return_value=payload):
            result = gemini_service.extract_job_info(
                "Example job text",
                include_questions=False,
            )

        self.assertEqual(result.company, "Example GmbH")
        self.assertEqual(result.questions, [])


if __name__ == "__main__":
    unittest.main()
