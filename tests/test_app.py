import unittest
from unittest.mock import patch

from src.app import cors_allowed_origins_from_env, frontend_url_from_env


class FrontendSettingsTest(unittest.TestCase):
    def test_reads_frontend_url_from_environment(self):
        with patch.dict("os.environ", {"FRONTEND_URL": " https://example.com/ "}):
            self.assertEqual(frontend_url_from_env(), "https://example.com")

    def test_uses_local_frontend_url_by_default(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(frontend_url_from_env(), "http://localhost:5173")

    def test_rejects_invalid_frontend_url(self):
        invalid_urls = (
            "example.com",
            "javascript:alert(1)",
            "https://user:password@example.com",
            "https://example.com?next=https://attacker.example",
            "https://example.com#fragment",
        )
        for frontend_url in invalid_urls:
            with (
                self.subTest(frontend_url=frontend_url),
                patch.dict("os.environ", {"FRONTEND_URL": frontend_url}),
                self.assertRaises(ValueError),
            ):
                frontend_url_from_env()


class CorsSettingsTest(unittest.TestCase):
    def test_reads_comma_separated_origins_from_environment(self):
        with patch.dict(
            "os.environ",
            {"CORS_ALLOWED_ORIGINS": " http://localhost:3000,https://saver.example.com/ "},
        ):
            self.assertEqual(
                cors_allowed_origins_from_env(),
                ["http://localhost:3000", "https://saver.example.com"],
            )

    def test_omits_cors_when_origins_are_not_configured(self):
        with patch.dict("os.environ", {}, clear=True):
            self.assertEqual(cors_allowed_origins_from_env(), [])

    def test_rejects_wildcard_origin(self):
        with patch.dict("os.environ", {"CORS_ALLOWED_ORIGINS": "*"}):
            with self.assertRaises(ValueError):
                cors_allowed_origins_from_env()

    def test_includes_frontend_origin_and_removes_duplicates(self):
        with patch.dict(
            "os.environ",
            {"CORS_ALLOWED_ORIGINS": "https://example.com,https://preview.example.com"},
        ):
            self.assertEqual(
                cors_allowed_origins_from_env("https://example.com/app"),
                ["https://example.com", "https://preview.example.com"],
            )


if __name__ == "__main__":
    unittest.main()
