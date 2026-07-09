import unittest
from unittest.mock import patch

from src.app import cors_allowed_origins_from_env


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


if __name__ == "__main__":
    unittest.main()
