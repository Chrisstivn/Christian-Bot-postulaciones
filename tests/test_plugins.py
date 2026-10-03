import unittest

from application_agent.ats_plugins import get_handler


class PluginTests(unittest.TestCase):
    def test_known_plugin_selector(self):
        handler = get_handler("greenhouse")
        self.assertIn("email", handler.get_special_fields())
        self.assertTrue(handler.selectors_for("resume"))


    def test_all_current_feed_plugins_are_registered(self):
        expected = {
            "personio",
            "successfactors",
            "teamtailor",
            "join",
            "hibob",
            "amazonjobs",
            "linkedin",
        }
        for ats in expected:
            with self.subTest(ats=ats):
                handler = get_handler(ats)
                self.assertEqual(handler.ats, ats)
                self.assertTrue(handler.selectors_for("email"))
                self.assertTrue(handler.selectors_for("resume"))

    def test_unknown_plugin_falls_back_to_base(self):
        handler = get_handler("unknown")
        self.assertEqual(handler.ats, "unknown")
        self.assertEqual(handler.selectors_for("email"), [])


if __name__ == "__main__":
    unittest.main()
