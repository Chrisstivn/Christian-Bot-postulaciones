import unittest
from unittest.mock import patch
import recheck_search_filters as recheck


class RecheckTests(unittest.TestCase):
    def test_saved_fifteen_year_requirement_rejected_without_network(self):
        row = {'Link': 'https://cl.linkedin.com/jobs/view/example-123', 'Status': 'NEW',
               'company': 'Example', 'job_title': 'Engineer'}
        existing = {'status': 'ready_for_review', 'job_text': 'Más de 15 años de experiencia, preferentemente minería.'}
        with patch.object(recheck.scraper, '_fetch_linkedin_guest_job_html') as fetch, patch.object(recheck.gemini_service, 'extract_job_info') as llm:
            decision = recheck.recheck_row(row, existing)
        self.assertEqual(decision.decision, 'REJECT')
        fetch.assert_not_called()
        llm.assert_not_called()

    def test_applied_or_processing_jobs_are_never_rechecked(self):
        with patch.object(recheck.scraper, '_fetch_linkedin_guest_job_html') as fetch:
            self.assertIsNone(recheck.recheck_row({'Status': 'APPLIED'}, {'status': 'ready_for_review'}))
            self.assertIsNone(recheck.recheck_row({'Status': 'NEW'}, {'status': 'processing'}))
            self.assertIsNone(recheck.recheck_row({'Status': 'NEW'}, None))
        fetch.assert_not_called()

    def test_dry_run_never_writes_queue_or_workbook(self):
        with patch.object(recheck.excel_output, 'read_rows', return_value=[{'Link': 'x', 'Status': 'NEW'}]), \
             patch.object(recheck.queue_service, 'get_by_url', return_value={'status': 'ready_for_review'}), \
             patch.object(recheck, 'recheck_row', return_value=recheck.search_filters.SearchDecision('REJECT', ['requires_more_than_four_years'], {})), \
             patch.object(recheck.queue_service, 'upsert_triage_result') as db, \
             patch.object(recheck.excel_output, 'write_row') as excel:
            self.assertEqual(recheck.run()['REJECT'], 1)
        db.assert_not_called()
        excel.assert_not_called()
