import unittest
from unittest.mock import Mock

import form_filler


def _link(label: str, visible: bool = True):
    link = Mock()
    link.is_visible.return_value = visible
    link.inner_text.return_value = label
    return link


class PortalNavigationTests(unittest.TestCase):
    def test_exact_job_title_is_selected(self):
        target = _link("Senior Product Manager, Data Platform (m/f/d)")
        other = _link("Senior Product Marketing Manager")
        page = Mock()
        page.query_selector_all.return_value = [other, target]

        found = form_filler._find_matching_job_link(
            page,
            "Senior Product Manager, Data Platform",
        )

        self.assertIs(found, target)

    def test_close_but_wrong_job_is_not_selected(self):
        page = Mock()
        page.query_selector_all.return_value = [
            _link("Senior Product Marketing Manager"),
            _link("Product Manager, Marketing Platform"),
        ]

        found = form_filler._find_matching_job_link(
            page,
            "Senior Product Manager, Data Platform",
        )

        self.assertIsNone(found)

    def test_gender_suffix_does_not_break_exact_match(self):
        target = _link("Growth Strategy Manager (m/f/d)")
        page = Mock()
        page.query_selector_all.return_value = [target]

        found = form_filler._find_matching_job_link(
            page,
            "Growth Strategy Manager",
        )

        self.assertIs(found, target)


if __name__ == "__main__":
    unittest.main()
