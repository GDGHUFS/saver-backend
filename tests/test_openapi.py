import unittest

from src.app import app


class OpenApiTest(unittest.TestCase):
    def test_public_endpoints_have_operation_documentation(self):
        schema = app.openapi()

        for path, methods in schema["paths"].items():
            for method, operation in methods.items():
                if method == "parameters":
                    continue
                with self.subTest(path=path, method=method):
                    self.assertTrue(operation.get("summary"))
                    self.assertTrue(operation.get("description"))
                    self.assertTrue(operation.get("responses"))

    def test_signed_cookie_authentication_is_declared(self):
        schema = app.openapi()
        security_scheme = schema["components"]["securitySchemes"]["APIKeyCookie"]

        self.assertEqual(security_scheme["type"], "apiKey")
        self.assertEqual(security_scheme["in"], "cookie")
        self.assertEqual(security_scheme["name"], "saver_session")
        self.assertEqual(schema["paths"]["/auth/me"]["get"]["security"], [{"APIKeyCookie": []}])


if __name__ == "__main__":
    unittest.main()
