import json
import unittest
from pathlib import Path


WORKFLOWS = (
    Path("N8N Job Application Automation - PRO PIPELINE V2 (con revisión manual) (4).json"),
    Path("N8N Job Application Automation - PRO PIPELINE V2 - PDF AUTOFILL SEPARADO.json"),
)


def load_workflow(path: Path) -> dict:
    text = path.read_text(encoding="utf-8-sig")
    return json.loads(text)


class SheetStatusPreservationTests(unittest.TestCase):
    def test_discovery_never_upserts_existing_sheet_rows(self):
        for path in WORKFLOWS:
            with self.subTest(path=str(path)):
                workflow = load_workflow(path)
                nodes = {node["name"]: node for node in workflow["nodes"]}
                connections = workflow["connections"]

                self.assertIn("Snapshot Existing Sheet Links1", nodes)
                self.assertIn("Build Existing Link Set1", nodes)
                self.assertIn("Only URLs Not Already in Sheet1", nodes)

                save = nodes["Save New URLs for Review1"]
                self.assertEqual(save["parameters"]["operation"], "append")
                self.assertNotIn(
                    "matchingColumns",
                    save["parameters"]["columns"],
                )
                self.assertEqual(
                    save["parameters"]["columns"]["value"]["Status"],
                    "NEW",
                )

                build_code = nodes["Build Existing Link Set1"]["parameters"]["jsCode"]
                guard_code = nodes["Only URLs Not Already in Sheet1"]["parameters"]["jsCode"]
                self.assertIn("item.json.Link", build_code)
                self.assertIn("item.json.real_apply_url", build_code)
                self.assertIn("u.search = ''", build_code)
                self.assertIn("u.hash = ''", build_code)
                self.assertIn("existing_links", guard_code)
                self.assertIn("u.search = ''", guard_code)
                self.assertIn("!existing.has(link)", guard_code)

                schedule_target = connections["Schedule (Buscar en LinkedIn)"]["main"][0][0]["node"]
                self.assertEqual(schedule_target, "Snapshot Existing Sheet Links1")

                split_target = connections["Split URLs1"]["main"][0][0]["node"]
                self.assertEqual(split_target, "Only URLs Not Already in Sheet1")

                guard_target = connections["Only URLs Not Already in Sheet1"]["main"][0][0]["node"]
                # The separated legacy JSON contains mojibake in this node
                # label ("alemÃ¡n") but the connection is otherwise the same
                # node. Normalize only for the assertion; do not rename live
                # workflow nodes as part of this benchmark change.
                normalized_guard_target = guard_target.replace("alemÃ¡n", "alemán")
                self.assertEqual(
                    normalized_guard_target,
                    "Call /triage-job (dedup + idioma + alemán)1",
                )


    def test_save_new_node_can_never_update_existing_rows(self):
        for path in WORKFLOWS:
            with self.subTest(path=str(path)):
                workflow = load_workflow(path)
                nodes = {node["name"]: node for node in workflow["nodes"]}
                save = nodes["Save New URLs for Review1"]

                self.assertEqual(save["parameters"]["operation"], "append")
                self.assertNotEqual(
                    save["parameters"]["operation"],
                    "appendOrUpdate",
                )
                self.assertNotIn(
                    "matchingColumns",
                    save["parameters"].get("columns", {}),
                )

    def test_ready_rows_include_baseline_decision_context(self):
        for path in WORKFLOWS:
            with self.subTest(path=str(path)):
                workflow = load_workflow(path)
                nodes = {node["name"]: node for node in workflow["nodes"]}
                save = nodes["Save New URLs for Review1"]
                values = save["parameters"]["columns"]["value"]

                self.assertIn("reason", values)
                self.assertIn("baseline_level", values["reason"])
                self.assertIn("upgrade_reason", values["reason"])

    def test_snapshot_is_collapsed_before_search(self):
        for path in WORKFLOWS:
            with self.subTest(path=str(path)):
                workflow = load_workflow(path)
                connections = workflow["connections"]

                self.assertEqual(
                    connections["Snapshot Existing Sheet Links1"]["main"][0][0]["node"],
                    "Build Existing Link Set1",
                )
                self.assertEqual(
                    connections["Build Existing Link Set1"]["main"][0][0]["node"],
                    "Search Params1",
                )


if __name__ == "__main__":
    unittest.main()
