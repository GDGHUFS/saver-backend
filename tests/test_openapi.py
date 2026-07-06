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
        self.assertEqual(
            schema["paths"]["/auth/withdraw/authorize"]["get"]["security"],
            [{"APIKeyCookie": []}],
        )
        self.assertEqual(
            schema["paths"]["/auth/withdraw/redirect"]["get"]["security"],
            [{"APIKeyCookie": []}],
        )

    def test_blog_endpoints_document_success_and_expected_errors(self):
        schema = app.openapi()
        blog_path = schema["paths"]["/blog/{blog_id}"]

        self.assertIn("get", schema["paths"]["/blog/latest"])
        author_operation = schema["paths"]["/blog/author/{user_id}"]["get"]
        self.assertNotIn("security", author_operation)
        self.assertEqual(set(author_operation["responses"]), {"200", "404", "422", "503"})
        self.assertEqual(
            schema["paths"]["/blog/"]["post"]["security"],
            [{"APIKeyCookie": []}],
        )
        self.assertNotIn("security", blog_path["get"])
        self.assertEqual(blog_path["put"]["security"], [{"APIKeyCookie": []}])
        self.assertEqual(blog_path["delete"]["security"], [{"APIKeyCookie": []}])
        self.assertEqual(set(blog_path["get"]["responses"]), {"200", "404", "422", "503"})
        self.assertEqual(set(blog_path["put"]["responses"]), {"204", "401", "404", "422", "503"})
        self.assertEqual(
            set(blog_path["delete"]["responses"]),
            {"204", "401", "404", "422", "503"},
        )

    def test_auth_database_endpoints_document_storage_failures(self):
        schema = app.openapi()

        self.assertIn("503", schema["paths"]["/redirect"]["get"]["responses"])
        self.assertIn("503", schema["paths"]["/auth/me"]["get"]["responses"])
        self.assertIn(
            "503",
            schema["paths"]["/auth/withdraw/authorize"]["get"]["responses"],
        )
        self.assertIn(
            "503",
            schema["paths"]["/auth/withdraw/redirect"]["get"]["responses"],
        )


if __name__ == "__main__":
    unittest.main()
