from __future__ import annotations

import stat
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import bili_agent_cli.profile as profile_module
from bili_agent_cli.profile import (
    ProfileError,
    get_csrf_token,
    get_sessdata_cookie_header,
    get_user_id,
    get_write_cookie_header,
    load_profile,
    parse_cookie_header,
    save_profile,
    serialize_cookies,
)


class ProfileTest(unittest.TestCase):
    def test_profile_path_is_inside_secret_directory(self) -> None:
        self.assertEqual(
            profile_module.PROFILE_PATH,
            profile_module.PROJECT_ROOT / "secret" / "profile.txt",
        )

    def test_parse_and_serialize_login_cookies(self) -> None:
        cookies = parse_cookie_header(
            "SESSDATA=session-value; bili_jct=csrf-value; DedeUserID=123"
        )

        self.assertEqual(cookies["DedeUserID"], "123")
        self.assertIn("SESSDATA=session-value", serialize_cookies(cookies))

    def test_rejects_incomplete_login_cookies(self) -> None:
        with self.assertRaises(ProfileError):
            serialize_cookies({"SESSDATA": "session-value"})

    def test_get_request_only_uses_sessdata(self) -> None:
        cookie_header = get_sessdata_cookie_header(
            {
                "SESSDATA": "session-value",
                "bili_jct": "csrf-value",
                "DedeUserID": "123",
            }
        )

        self.assertEqual(cookie_header, "SESSDATA=session-value")

    def test_get_write_auth_uses_sessdata_and_csrf(self) -> None:
        cookies = {
            "SESSDATA": "session-value",
            "bili_jct": "csrf-value",
            "DedeUserID": "123",
        }

        self.assertEqual(get_csrf_token(cookies), "csrf-value")
        self.assertEqual(
            get_write_cookie_header(cookies),
            "SESSDATA=session-value; bili_jct=csrf-value",
        )

    def test_get_write_auth_rejects_missing_required_values(self) -> None:
        with self.assertRaises(ProfileError):
            get_csrf_token({"SESSDATA": "session-value"})

        with self.assertRaises(ProfileError):
            get_write_cookie_header({"bili_jct": "csrf-value"})

    def test_get_user_id(self) -> None:
        self.assertEqual(get_user_id({"DedeUserID": "123"}), "123")

        with self.assertRaises(ProfileError):
            get_user_id({"DedeUserID": "invalid"})

    def test_saves_profile_with_private_permissions(self) -> None:
        with tempfile.TemporaryDirectory() as temporary_directory:
            project_root = Path(temporary_directory)
            profile_path = project_root / "secret" / "profile.txt"
            cookies = {
                "SESSDATA": "session-value",
                "bili_jct": "csrf-value",
                "DedeUserID": "123",
            }

            with (
                patch.object(profile_module, "PROFILE_PATH", profile_path),
            ):
                self.assertEqual(save_profile(cookies), profile_path)
                self.assertEqual(load_profile(), cookies)

            mode = stat.S_IMODE(profile_path.stat().st_mode)
            self.assertEqual(mode, 0o600)


if __name__ == "__main__":
    unittest.main()
