"""Search regression tests without network, Gemini calls or app startup."""
import ast
import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
import job_quality


def load_pipeline_functions():
    tree = ast.parse((ROOT / "main.py").read_text())
    names = {"_is_linkedin_url", "_extract_job_info_or_discard",
             "_triage_job_url", "linkedin_search_and_triage"}
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef)
             and node.name in names]
    for node in nodes:
        node.decorator_list = []
    module = ast.Module(body=nodes, type_ignores=[])
    env = {
        "LinkedInSearchInput": SimpleNamespace, "job_quality": job_quality,
        "queue_service": Mock(), "scraper": Mock(), "metrics": Mock(),
        "gemini_service": Mock(), "log": Mock(),
        "HTTPException": lambda code, message: RuntimeError(message),
    }
    exec(compile(module, "main.py", "exec"), env)
    return env


class ChristianSearchTests(unittest.TestCase):
    def setUp(self):
        self.env_patch = patch.dict(os.environ, {
            "SEARCH_EXCLUDED_TITLE_REGEX": "",
            "SEARCH_ALLOWED_WORK_FORMATS": "",
            "SEARCH_EXCLUDED_CONTRACT_TYPES": "",
        })
        self.env_patch.start()
        self.addCleanup(self.env_patch.stop)
        self.app = load_pipeline_functions()
        self.queue = self.app["queue_service"]
        self.queue.get_by_url.return_value = None
        self.app["scraper"].scrape_job_posting.return_value = {
            "text": "Práctica profesional junior presencial, contrato temporal.",
            "work_format": "On-site",
        }
        self.app["scraper"].find_closed_application_marker.return_value = None
        self.app["gemini_service"].extract_job_info.return_value = SimpleNamespace(
            company="Empresa Chile", job_title="Junior Analyst",
            detected_language="OTHER", german_required="YES",
        )

    def test_spanish_chilean_job_is_ready_despite_legacy_exclusions(self):
        result = self.app["_triage_job_url"]("https://cl.linkedin.com/jobs/view/123")
        self.assertEqual(result["status"], "ready")
        self.assertEqual(result["baseline_comparison"]["filter_policy"], "unrestricted")

    def test_extraction_accepts_any_language_and_language_requirement(self):
        info, discarded = self.app["_extract_job_info_or_discard"]("url", "texto")
        self.assertIsNone(discarded)
        self.assertEqual(info.german_required, "YES")

    def test_titles_workplaces_and_contracts_are_unrestricted(self):
        for title in ("Intern", "Working Student", "VP", "Junior", "Data Analyst", "Director"):
            for workplace in ("Remote", "Hybrid", "On-site", "Unknown"):
                with self.subTest(title=title, workplace=workplace):
                    decision = job_quality.evaluate_upgrade(
                        company="Empresa", job_title=title,
                        job_text="Temporary fixed-term contract", work_format=workplace)
                    self.assertTrue(decision.keep)

    def test_custom_filter_only_acts_when_configured(self):
        with patch.dict(os.environ, {"SEARCH_EXCLUDED_TITLE_REGEX": r"\bjunior\b"}):
            rejected = job_quality.evaluate_upgrade(company="", job_title="Junior Analyst", job_text="")
            accepted = job_quality.evaluate_upgrade(company="", job_title="Analista", job_text="")
            self.assertFalse(rejected.keep)
            self.assertTrue(accepted.keep)

    def test_closed_jobs_and_duplicates_still_do_not_enter_review(self):
        self.app["scraper"].find_closed_application_marker.return_value = "closed"
        result = self.app["_triage_job_url"]("https://cl.linkedin.com/jobs/view/123")
        self.assertEqual(result["rejected_reason"], "closed_job")
        self.queue.get_by_url.return_value = {"status": "ready_for_review"}
        result = self.app["_triage_job_url"]("https://cl.linkedin.com/jobs/view/123")
        self.assertEqual(result["status"], "duplicate")

    def test_search_enqueues_chilean_and_global_urls(self):
        urls = ["https://cl.linkedin.com/jobs/view/123", "https://www.linkedin.com/jobs/view/456"]
        self.app["scraper"].scrape_linkedin_search_results.return_value = urls
        self.queue.enqueue_triage_urls.return_value = {"inserted": 2}
        self.queue.get_by_url.return_value = {"status": "triage_pending"}
        payload = SimpleNamespace(search_url="https://www.linkedin.com/jobs/search/?location=Chile", max_jobs=25)
        result = self.app["linkedin_search_and_triage"](payload)
        self.queue.enqueue_triage_urls.assert_called_once_with(urls)
        self.assertEqual(result["ready_count"], 2)
        self.assertEqual(result["country_discarded_count"], 0)

    def test_pdf_has_no_language_eligibility_gate(self):
        tree = ast.parse((ROOT / "main.py").read_text())
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                        and n.name == "_build_application_pdf_artifacts")
        comparisons = [ast.unparse(n) for n in ast.walk(function) if isinstance(n, ast.Compare)]
        self.assertFalse(any("detected_language" in n or "german_required" in n for n in comparisons))

    def test_spanish_cv_text_passes_format_validation(self):
        tree = ast.parse((ROOT / "gemini_service.py").read_text())
        function = next(n for n in tree.body if isinstance(n, ast.FunctionDef)
                        and n.name == "_validate_adaptation")
        env = {
            "_title_style_problems": lambda text: [],
            "_bullet_style_problems": lambda text: [],
            "_estimated_title_lines": lambda text: 2,
            "_estimated_bullet_lines": lambda text: 2,
        }
        exec(compile(ast.Module(body=[function], type_ignores=[]), "gemini_service.py", "exec"), env)
        def text_of_length(text, length):
            return text + " " + "a" * (length - len(text) - 2) + "."
        adaptation = SimpleNamespace(
            nuevo_titulo=text_of_length("Analista de gestión y administración", 42),
            nuevo_perfil=text_of_length("Tengo experiencia en análisis y gestión", 590),
            nuevo_cargo_actual="Analista de gestión",
            nuevas_tareas=[text_of_length("Gestioné proyectos y análisis de información", 180)] * 4,
        )
        self.assertEqual(env["_validate_adaptation"](adaptation), [])

    def test_cv_and_application_prompts_request_spanish(self):
        tree = ast.parse((ROOT / "gemini_service.py").read_text())
        names = {"CV_ADAPTATION_SYSTEM_PROMPT", "ANSWERS_SYSTEM_PROMPT", "OPEN_FIELD_SYSTEM_PROMPT"}
        prompts = {node.targets[0].id: node.value.value for node in tree.body
                   if isinstance(node, ast.Assign) and isinstance(node.targets[0], ast.Name)
                   and node.targets[0].id in names}
        self.assertIn("CV_ADAPTATION_SYSTEM_PROMPT", prompts)
        self.assertIn("ANSWERS_SYSTEM_PROMPT", prompts)
        self.assertIn("OPEN_FIELD_SYSTEM_PROMPT", prompts)
        for prompt in prompts.values():
            self.assertIn("SIEMPRE en español", prompt)
            self.assertNotIn("en inglés", prompt)


if __name__ == "__main__":
    unittest.main()
