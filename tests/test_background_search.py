import sqlite3
import tempfile
import threading
import unittest
from pathlib import Path
from background_search import SearchRunner


class BackgroundSearchTests(unittest.TestCase):
    def test_start_returns_before_work_completes_and_retry_reuses_task(self):
        release=threading.Event();entered=threading.Event();calls=[]
        def work(payload):
            calls.append(payload);entered.set();release.wait(5)
            return {'ready_jobs':[{'job_url':'https://example/job'}],'review_jobs':[]}
        with tempfile.TemporaryDirectory() as folder:
            runner=SearchRunner(work,Path(folder)/'runs.sqlite3')
            try:
                ticket=runner.start({'search_url':'https://example/search','max_jobs':2})
                self.assertTrue(entered.wait(2))
                self.assertEqual(runner.status(ticket['task_id'])['state'],'running')
                self.assertEqual(runner.start({'max_jobs':2,'search_url':'https://example/search'}),ticket)
                release.set();runner.executor.shutdown(wait=True)
                result=runner.status(ticket['task_id'])
                self.assertEqual(result['state'],'completed')
                self.assertEqual(len(result['ready_jobs']),1)
                self.assertEqual(len(calls),1)
                self.assertEqual(runner.start({'max_jobs':2,'search_url':'https://example/search'}),ticket)
            finally:
                release.set();runner.executor.shutdown(wait=True)

    def test_worker_failure_is_reported_and_explicit_retry_can_run(self):
        def work(payload):raise ValueError('source unavailable')
        with tempfile.TemporaryDirectory() as folder:
            runner=SearchRunner(work,Path(folder)/'runs.sqlite3')
            ticket=runner.start({'search_url':'x'})
            runner.executor.shutdown(wait=True)
            result=runner.status(ticket['task_id'])
            self.assertEqual(result['state'],'failed')
            self.assertEqual(result['error'],'source unavailable')
            # A fresh process preserves failures, but allows a new task.
            retry=SearchRunner(lambda p:{'ready_jobs':[]},Path(folder)/'runs.sqlite3')
            other=retry.start({'search_url':'x'});retry.executor.shutdown(wait=True)
            self.assertNotEqual(other,ticket)
            self.assertEqual(retry.status(other['task_id'])['state'],'completed')

    def test_restart_preserves_results_and_reports_interrupted_tasks(self):
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'runs.sqlite3'
            runner=SearchRunner(lambda p:{'ready_jobs':[],'review_jobs':[]},path)
            ticket=runner.start({'search_url':'x'});runner.executor.shutdown(wait=True)
            with sqlite3.connect(path) as db:
                db.execute("INSERT INTO search_runs VALUES ('interrupted','hash','running',0,0,NULL,NULL)")
            restarted=SearchRunner(lambda p:{},path)
            try:
                self.assertEqual(restarted.status(ticket['task_id'])['state'],'completed')
                self.assertEqual(restarted.status('interrupted')['state'],'failed')
                self.assertIn('restarted',restarted.status('interrupted')['error'])
                with self.assertRaises(KeyError):restarted.status('missing')
            finally:restarted.executor.shutdown(wait=True)

    def test_worker_runs_searches_one_at_a_time(self):
        release=threading.Event();entered=threading.Event()
        def work(p):entered.set();release.wait(5);return {}
        with tempfile.TemporaryDirectory() as folder:
            runner=SearchRunner(work,Path(folder)/'runs.sqlite3')
            try:
                first=runner.start({'search_url':'a'});self.assertTrue(entered.wait(2))
                second=runner.start({'search_url':'b'})
                self.assertEqual(runner.status(second['task_id'])['state'],'queued')
            finally:release.set();runner.executor.shutdown(wait=True)
