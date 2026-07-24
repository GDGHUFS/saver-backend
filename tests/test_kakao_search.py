import unittest
from unittest.mock import patch

from src.search.engine.kakao import KakaoSearchSettings


class KakaoSearchSettingsTest(unittest.TestCase):
    def test_reads_search_specific_rest_api_key(self):
        with patch.dict(
            "os.environ",
            {"KAKAO_SEARCH_REST_API_KEY": "search-key", "KAKAO_KEY": "oauth-key"},
            clear=True,
        ):
            settings = KakaoSearchSettings.from_env()

        self.assertIsNotNone(settings)
        self.assertEqual(settings.rest_api_key, "search-key")

    def test_falls_back_to_existing_kakao_rest_api_key(self):
        with patch.dict("os.environ", {"KAKAO_KEY": "oauth-key"}, clear=True):
            settings = KakaoSearchSettings.from_env()

        self.assertIsNotNone(settings)
        self.assertEqual(settings.rest_api_key, "oauth-key")


if __name__ == "__main__":
    unittest.main()
