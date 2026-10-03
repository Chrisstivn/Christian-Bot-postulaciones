import tempfile
import unittest
from pathlib import Path

from application_agent.ats_memory import ATSMemory


class ATSMemoryTests(unittest.TestCase):
    def test_record_and_read_success(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ats_memory.json"
            memory = ATSMemory(path=path, enabled=True)
            memory.record_success("greenhouse", "email", "input[name=email]")

            reloaded = ATSMemory(path=path, enabled=True)
            entry = reloaded.get("greenhouse", "email")
            self.assertIsNotNone(entry)
            self.assertEqual(entry.selector, "input[name=email]")
            self.assertEqual(entry.success, 1)

    def test_failure_does_not_remove_selector(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ats_memory.json"
            memory = ATSMemory(path=path, enabled=True)
            memory.record_success("lever", "resume", "input[type=file]")
            memory.record_failure("lever", "resume", "input[type=file]")
            entry = memory.get("lever", "resume")
            self.assertEqual(entry.selector, "input[type=file]")
            self.assertEqual(entry.failure, 1)


if __name__ == "__main__":
    unittest.main()
