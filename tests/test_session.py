import unittest

from src.auth.session import InvalidSession, create_session_cookie, read_session_cookie


class SessionCookieTest(unittest.TestCase):
    secret = "test-secret-with-enough-randomness"

    def test_round_trip(self):
        cookie = create_session_cookie(1234, self.secret, 3600, now=1000)

        self.assertEqual(read_session_cookie(cookie, self.secret, now=1001), 1234)

    def test_rejects_tampered_cookie(self):
        cookie = create_session_cookie(1234, self.secret, 3600, now=1000)
        payload, signature = cookie.split(".")
        tampered = f"{payload}.{signature[:-1]}A"

        with self.assertRaises(InvalidSession):
            read_session_cookie(tampered, self.secret, now=1001)

    def test_rejects_expired_cookie(self):
        cookie = create_session_cookie(1234, self.secret, 10, now=1000)

        with self.assertRaises(InvalidSession):
            read_session_cookie(cookie, self.secret, now=1010)

    def test_rejects_malformed_cookie(self):
        for cookie in ("", "not-base64", "payload.signature.extra"):
            with self.subTest(cookie=cookie), self.assertRaises(InvalidSession):
                read_session_cookie(cookie, self.secret, now=1000)


if __name__ == "__main__":
    unittest.main()
