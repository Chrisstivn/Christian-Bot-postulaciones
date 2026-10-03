import json
import unittest
from pathlib import Path


WORKFLOW = Path("N8N Job Application Automation - PRO PIPELINE V2 - PDF AUTOFILL SEPARADO.json")


class SheetPdfNameTests(unittest.TestCase):
    def test_sheet_uses_actual_output_pdf_basename(self):
        workflow = json.loads(WORKFLOW.read_text(encoding="utf-8-sig"))
        nodes = {node["name"]: node for node in workflow["nodes"]}
        save = nodes["Save Success1"]
        values = save["parameters"]["columns"]["value"]

        pdf_expr = values["pdf_name"]
        download_expr = values["download_url"]

        self.assertIn("pdf_path", pdf_expr)
        self.assertIn('.split("/").pop()', pdf_expr)
        self.assertIn("pdf_name", pdf_expr)

        self.assertIn("pdf_path", download_expr)
        self.assertIn('.split("/").pop()', download_expr)

    def test_backend_filename_format_keeps_parenthesized_initials(self):
        import pdf_generator

        self.assertEqual(
            pdf_generator.build_pdf_name("Adsquare", "Data Operations Analyst"),
            "CV_Christian_Adsquare_(DOA).pdf",
        )


if __name__ == "__main__":
    unittest.main()
