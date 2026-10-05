"""Integration checks for successful PDFs and fail-closed layout repair.
External LLM/database calls are simulated; rendering is opt-in and real.
"""
import os
import tempfile
import unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

os.environ.setdefault('NEO4J_PASSWORD', 'qa-not-a-real-password')
import main
from fastapi import HTTPException
from models import ScrapeInput
from test_christian_cv import ChristianCvTests, SOURCE


@unittest.skipUnless(SOURCE.exists(), 'Local master CV required')
class PdfPipelineTests(unittest.TestCase):
    def run_pipeline(self, guard_results, repair_error=None, real_render=False):
        with tempfile.TemporaryDirectory() as folder, ExitStack() as stack:
            directory=Path(folder)
            role=main.cv_date_guard.read_current_role(SOURCE)
            adaptation=ChristianCvTests().adaptation()
            adaptation.empresa_actual_sin_cambios=role.company
            adaptation.fechas_actual_sin_cambios=role.dates
            stack.enter_context(patch.object(main,'CV_MAESTRO_DOCX',str(SOURCE)))
            stack.enter_context(patch.object(main,'REAL_CURRENT_COMPANY',''))
            stack.enter_context(patch.object(main,'REAL_CURRENT_DATES',''))
            stack.enter_context(patch.object(main,'WORK_DIR',directory))
            stack.enter_context(patch.object(main.pdf_generator,'OUTPUT_DIR',directory))
            stack.enter_context(patch.object(main.pdf_generator,'PDF_ENGINE','libreoffice'))
            stack.enter_context(patch.object(main,'_resolve_job_text',return_value='Source job description'))
            stack.enter_context(patch.object(main.gemini_service,'extract_job_info',return_value=SimpleNamespace(
                company='Example',job_title='Project Manager',german_required='NO')))
            stack.enter_context(patch.object(main.gemini_service,'adapt_cv',return_value=adaptation))
            ingest=stack.enter_context(patch.object(main.neo4j_service,'ingest_application'))
            repair=stack.enter_context(patch.object(main.gemini_service,'repair_rendered_cv_layout',
                return_value=adaptation,side_effect=repair_error))
            if not real_render:
                def render(*args,**kwargs):
                    name=kwargs.get('pdf_name') or args[1]
                    path=directory/name;path.write_bytes(b'%PDF-simulated-conversion');return str(path)
                stack.enter_context(patch.object(main.pdf_generator,'build_final_pdf',side_effect=render))
                stack.enter_context(patch.object(main.pdf_layout_guard,'validate_pdf_layout',side_effect=guard_results))
            payload=ScrapeInput(url='https://example.com/jobs/1')
            if repair_error or (guard_results and guard_results[-1]):
                with self.assertRaises(HTTPException) as error:
                    main._build_application_pdf_artifacts(payload,'qa')
                self.assertEqual(error.exception.status_code,422)
                self.assertEqual(list(directory.glob('*.pdf')),[])
                ingest.assert_not_called()
            else:
                result=main._build_application_pdf_artifacts(payload,'qa')
                self.assertEqual(result['status'],'pdf_ready')
                self.assertEqual(result['pdf_name'],Path(result['pdf_path']).name)
                self.assertTrue(Path(result['pdf_path']).is_file())
                self.assertEqual(result['source_url'],payload.url)
                ingest.assert_called_once()
            return repair.call_count

    def test_valid_conversion_returns_actual_filename(self):
        self.assertEqual(self.run_pipeline([[]]),0)

    def test_successful_layout_repair_is_rendered_and_validated_again(self):
        self.assertEqual(self.run_pipeline([[{'field':'nuevo_perfil'}],[]]),1)

    def test_failed_repair_removes_pdf_and_never_marks_it_ready(self):
        self.run_pipeline([[{'field':'nuevo_perfil'}]],ValueError('Invalid rewrite'))

    def test_bad_final_layout_removes_pdf_and_never_marks_it_ready(self):
        self.run_pipeline([[{'field':'nuevo_perfil'}],[{'field':'nuevo_perfil'}]])

    def test_three_page_template_aborts_without_llm_layout_repair(self):
        self.assertEqual(self.run_pipeline([[{'field':'pagination','pages':3}]]),0)

    @unittest.skipUnless(os.getenv('RUN_PDF_RENDER_TESTS')=='1','Opt in to real conversion')
    def test_real_conversion_through_backend_pipeline(self):
        self.run_pipeline([],real_render=True)
