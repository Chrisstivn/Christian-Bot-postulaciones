"""
Opt-in real ATS smoke test.

This test does NOT submit an application. It only opens the public Greenhouse
posting and verifies detector/form discovery basics. Run manually:

    RUN_REAL_ATS_TESTS=1 HEADLESS=false venv/bin/python -m unittest tests.e2e.test_greenhouse_planet -v
"""

from __future__ import annotations

import os
import tempfile
import unittest

from playwright.sync_api import sync_playwright

from application_agent.ats_plugins import get_handler
from application_agent.confidence_engine import score_field
from application_agent.ats_detector import detect_page
from application_agent.form_engine import EngineStats, inspect_fields, upload_cv


PLANET_URL = "https://job-boards.greenhouse.io/planetlabs/jobs/7993116?gh_src=e56b92851us"


@unittest.skipUnless(os.environ.get("RUN_REAL_ATS_TESTS") == "1", "real ATS test is opt-in")
class GreenhousePlanetSmokeTest(unittest.TestCase):
    def test_greenhouse_detection_and_fields(self):
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=os.environ.get("HEADLESS", "true").lower() == "true")
            page = browser.new_page()
            page.goto(PLANET_URL, wait_until="networkidle", timeout=45000)
            detection = detect_page(page)
            self.assertEqual(detection.ats, "greenhouse")
            handler = get_handler(detection.ats)
            self.assertTrue(handler.selectors_for("email"))
            stats = EngineStats(ats=detection.ats, ats_confidence=detection.confidence)
            fields = inspect_fields(page, stats)
            self.assertTrue(stats.detected or fields)
            decisions = [score_field(field, ats=detection.ats, plugin_hint=handler.hint_for_candidate(field)) for field in fields]
            self.assertTrue(any(decision.field_type in ("email", "first_name", "last_name", "resume") for decision in decisions))
            with tempfile.NamedTemporaryFile(suffix=".pdf") as fake_pdf:
                fake_pdf.write(b"%PDF-1.4\n% dry run test pdf\n")
                fake_pdf.flush()
                if page.query_selector("input[type='file']"):
                    self.assertTrue(upload_cv(page, fake_pdf.name, stats))
            browser.close()


if __name__ == "__main__":
    unittest.main()
