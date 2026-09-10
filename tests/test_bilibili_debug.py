from __future__ import annotations

import unittest

from bili_agent_cli.bilibili.debug import BilibiliDebugError, validate_api_path


class BilibiliDebugTest(unittest.TestCase):
    def test_accepts_api_path(self) -> None:
        self.assertEqual(
            validate_api_path("/x/web-interface/nav"),
            "/x/web-interface/nav",
        )

    def test_rejects_external_url(self) -> None:
        with self.assertRaises(BilibiliDebugError):
            validate_api_path("https://example.com/collect")

    def test_rejects_scheme_relative_url(self) -> None:
        with self.assertRaises(BilibiliDebugError):
            validate_api_path("//example.com/collect")


if __name__ == "__main__":
    unittest.main()
