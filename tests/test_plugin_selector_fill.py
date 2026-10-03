import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from application_agent import form_engine


class _Handler:
    ats = "teamtailor"

    def __init__(self, fields):
        self._fields = fields

    def get_special_fields(self):
        return self._fields


class PluginSelectorFillTests(unittest.TestCase):
    def test_unambiguous_plugin_selector_is_used_directly(self):
        element = Mock()
        frame = Mock()
        frame.query_selector.return_value = element
        page = SimpleNamespace(frames=[frame])
        candidate = SimpleNamespace(
            field_type="email",
            match_text="Email",
        )
        stats = form_engine.EngineStats(ats="teamtailor")

        with patch.object(
            form_engine,
            "_candidate_from_element",
            return_value=candidate,
        ), patch.object(
            form_engine,
            "_current_value",
            return_value="",
        ), patch.object(
            form_engine,
            "answer_for_decision",
            return_value="candidate@example.com",
        ) as answer, patch.object(
            form_engine,
            "fill_field",
            return_value=True,
        ) as fill:
            count = form_engine.fill_from_plugin_selectors(
                page=page,
                personal_info={"email": "candidate@example.com"},
                responses=[],
                motivation_answer="",
                experience_answer="",
                cv_maestro_text="",
                job_context="",
                stats=stats,
                handler=_Handler({"email": ("input[type='email']",)}),
            )

        self.assertEqual(count, 1)
        frame.query_selector.assert_called_once_with("input[type='email']")
        answer.assert_called_once()
        fill.assert_called_once()
        self.assertEqual(stats.decisions[0]["answer_key"], "email")

    def test_shared_selector_is_not_used_ambiguously(self):
        frame = Mock()
        page = SimpleNamespace(frames=[frame])
        stats = form_engine.EngineStats(ats="test")

        count = form_engine.fill_from_plugin_selectors(
            page=page,
            personal_info={},
            responses=[],
            motivation_answer="",
            experience_answer="",
            cv_maestro_text="",
            job_context="",
            stats=stats,
            handler=_Handler(
                {
                    "first_name": ("input[name='name']",),
                    "last_name": ("input[name='name']",),
                }
            ),
        )

        self.assertEqual(count, 0)
        frame.query_selector.assert_not_called()


if __name__ == "__main__":
    unittest.main()
