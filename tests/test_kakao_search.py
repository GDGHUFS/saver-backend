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

    def test_does_not_reuse_login_rest_api_key(self):
        with patch.dict("os.environ", {"KAKAO_KEY": "oauth-key"}, clear=True):
            settings = KakaoSearchSettings.from_env()

        self.assertIsNone(settings)


if __name__ == "__main__":
    unittest.main()
