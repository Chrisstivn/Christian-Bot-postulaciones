import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi import HTTPException
from openpyxl import load_workbook
import excel_output as output


class ExcelOutputTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.path = Path(self.temp.name) / "postulaciones.xlsx"
        self.env = patch.dict(os.environ, {"OUTPUT_EXCEL_PATH": str(self.path)})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.temp.cleanup()

    def test_empty_workbook_created_and_can_be_read(self):
        self.assertEqual(output.read_rows(), [])
        self.assertTrue(self.path.exists())
        book = load_workbook(self.path)
        self.assertEqual(list(book.active.values)[0], tuple(output.HEADERS))
        book.close()

    def test_discovery_never_resets_reviewed_offer(self):
        output.write_row(output.RowInput(row={"Link": "https://example.com/1", "Status": "READY", "company": "Example"}))
        output.write_row(output.RowInput(row={"Link": "https://example.com/1", "Status": "NEW"}, preserve_existing=True))
        self.assertEqual(len(output.read_rows()), 1)
        self.assertEqual(output.read_rows()[0]["Status"], "READY")
        output.write_row(output.RowInput(row={"Link": "https://example.com/1", "Status": "DONE"}))
        self.assertEqual(output.read_rows()[0]["company"], "Example")
        self.assertEqual(output.read_rows()[0]["Status"], "DONE")

    def test_second_matching_key_and_literal_text(self):
        output.write_row(output.RowInput(row={"real_apply_url": "https://example.com/apply", "Status": "READY", "reason": "=1+1", "count": 0}, matching_columns=["real_apply_url"]))
        output.write_row(output.RowInput(row={"real_apply_url": "https://example.com/apply", "Status": "DONE"}, matching_columns=["real_apply_url"]))
        self.assertEqual(len(output.read_rows()), 1)
        self.assertEqual(output.read_rows()[0]["count"], 0)
        book = load_workbook(self.path)
        self.assertEqual(book.active["J2"].data_type, "s")
        book.close()

    def test_missing_match_key_does_not_add_row(self):
        with self.assertRaises(HTTPException):
            output.write_row(output.RowInput(row={"Status": "DONE"}))
        self.assertEqual(output.read_rows(), [])

    def test_workflows_are_disconnected_from_original_sheet(self):
        total = 0
        for path in Path(__file__).resolve().parents[1].glob("*.json"):
            if path.name.startswith("package"):
                continue
            data = json.loads(path.read_text(encoding="utf-8-sig"))
            for node in data.get("nodes", []):
                self.assertNotEqual(node.get("type"), "n8n-nodes-base.googleSheets")
                if node.get("parameters", {}).get("url", "").endswith("/excel-output/rows"):
                    total += 1
                    self.assertNotIn("credentials", node)
                    if "Snapshot" in node["name"]:
                        self.assertTrue(node["alwaysOutputData"])
        self.assertEqual(total, 12)
