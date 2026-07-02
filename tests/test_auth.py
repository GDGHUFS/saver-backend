import unittest

from fastapi import HTTPException

from src.auth import _user_values


class UserValuesTest(unittest.TestCase):
    def test_reads_kakao_account_profile(self):
        profile = {
            "id": 1234,
            "kakao_account": {
                "profile": {
                    "nickname": "Saver 사용자",
                    "profile_image_url": "https://example.com/profile.png",
                }
            },
        }

        self.assertEqual(
            _user_values(profile, "https://example.com/default.svg"),
            (1234, "Saver 사용자", "https://example.com/profile.png"),
        )

    def test_uses_defaults_when_optional_profile_is_not_provided(self):
        self.assertEqual(
            _user_values({"id": 1234}, "https://example.com/default.svg"),
            (1234, "사용자-1234", "https://example.com/default.svg"),
        )

    def test_rejects_profile_without_user_id(self):
        with self.assertRaises(HTTPException) as raised:
            _user_values({}, "https://example.com/default.svg")

        self.assertEqual(raised.exception.status_code, 502)


if __name__ == "__main__":
    unittest.main()
