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

    def test_search_endpoints_document_async_status_and_failures(self):
        schema = app.openapi()

        submit = schema["paths"]["/search"]["post"]
        result = schema["paths"]["/search/{magic_code}"]["get"]
        self.assertEqual(set(submit["responses"]), {"202", "401", "422", "503"})
        self.assertEqual(
            set(result["responses"]),
            {"200", "202", "401", "404", "422", "502", "503"},
        )
        self.assertEqual(submit["security"], [{"APIKeyCookie": []}])
        self.assertEqual(result["security"], [{"APIKeyCookie": []}])
        result_schema = schema["components"]["schemas"]["SearchResultResponse"]
        self.assertEqual(
            result_schema["properties"]["result"]["$ref"],
            "#/components/schemas/KagiSearchResponse",
        )

    def test_news_endpoint_documents_public_filtered_read(self):
        schema = app.openapi()
        publishers_operation = schema["paths"]["/news/publishers"]["get"]
        publisher_operation = schema["paths"]["/news/publishers/{publisher}"]["get"]
        operation = schema["paths"]["/news/latest"]["get"]
        page_operation = schema["paths"]["/news/latest/page"]["get"]

        self.assertNotIn("security", publishers_operation)
        self.assertEqual(set(publishers_operation["responses"]), {"200", "503"})
        self.assertEqual(
            publishers_operation["responses"]["200"]["content"]["application/json"]["schema"][
                "items"
            ]["$ref"],
            "#/components/schemas/NewsPublisherResponse",
        )

        self.assertNotIn("security", publisher_operation)
        self.assertEqual(set(publisher_operation["responses"]), {"200", "404", "422", "503"})
        publisher_parameters = {
            parameter["name"]: parameter for parameter in publisher_operation["parameters"]
        }
        self.assertEqual(set(publisher_parameters), {"publisher"})
        self.assertTrue(publisher_parameters["publisher"]["required"])
        self.assertEqual(
            publisher_operation["responses"]["200"]["content"]["application/json"]["schema"][
                "$ref"
            ],
            "#/components/schemas/NewsPublisherResponse",
        )

        self.assertNotIn("security", operation)
        self.assertEqual(set(operation["responses"]), {"200", "422", "503"})
        parameters = {parameter["name"]: parameter for parameter in operation["parameters"]}
        self.assertEqual(set(parameters), {"count", "publisher"})
        self.assertFalse(parameters["publisher"]["required"])

        self.assertNotIn("security", page_operation)
        self.assertEqual(set(page_operation["responses"]), {"200", "422", "503"})
        page_parameters = {
            parameter["name"]: parameter for parameter in page_operation["parameters"]
        }
        self.assertEqual(set(page_parameters), {"page_size", "publisher", "cursor"})
        self.assertFalse(page_parameters["cursor"]["required"])
        self.assertIn("커서 기반 페이지네이션", page_operation["description"])
        self.assertEqual(
            page_operation["responses"]["200"]["content"]["application/json"]["schema"]["$ref"],
            "#/components/schemas/NewsPageResponse",
        )

    def test_special_days_endpoint_documents_public_monthly_read(self):
        schema = app.openapi()
        operation = schema["paths"]["/special-days/{year_month}"]["get"]

        self.assertNotIn("security", operation)
        self.assertEqual(set(operation["responses"]), {"200", "422", "503"})
        parameters = {parameter["name"]: parameter for parameter in operation["parameters"]}
        self.assertEqual(set(parameters), {"year_month"})
        self.assertTrue(parameters["year_month"]["required"])
        self.assertEqual(
            parameters["year_month"]["schema"]["pattern"],
            "^[1-9][0-9]{3}-(0[1-9]|1[0-2])$",
        )
        self.assertEqual(
            operation["responses"]["200"]["content"]["application/json"]["schema"]["items"][
                "$ref"
            ],
            "#/components/schemas/SpecialDayResponse",
        )
        self.assertEqual(
            set(schema["components"]["schemas"]["SpecialDayKind"]["enum"]),
            {"국경일", "기념일", "24절기", "잡절"},
        )

    def test_weather_endpoints_document_public_current_and_forecast_reads(self):
        schema = app.openapi()
        current = schema["paths"]["/weather/current"]["get"]
        forecast = schema["paths"]["/weather/forecast"]["get"]

        self.assertNotIn("security", current)
        self.assertEqual(set(current["responses"]), {"200", "503"})
        self.assertEqual(
            current["responses"]["200"]["content"]["application/json"]["schema"]["$ref"],
            "#/components/schemas/NationwideCurrentWeatherResponse",
        )

        self.assertNotIn("security", forecast)
        self.assertEqual(set(forecast["responses"]), {"200", "404", "422", "503"})
        parameters = {parameter["name"]: parameter for parameter in forecast["parameters"]}
        self.assertEqual(set(parameters), {"region", "latitude", "longitude", "hours"})
        self.assertFalse(parameters["region"]["required"])
        self.assertFalse(parameters["latitude"]["required"])
        self.assertFalse(parameters["longitude"]["required"])
        self.assertEqual(parameters["hours"]["schema"]["minimum"], 1)
        self.assertEqual(parameters["hours"]["schema"]["maximum"], 72)
        self.assertEqual(
            forecast["responses"]["200"]["content"]["application/json"]["schema"]["$ref"],
            "#/components/schemas/WeatherForecastResponse",
        )


if __name__ == "__main__":
    unittest.main()
