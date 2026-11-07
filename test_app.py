import json
import unittest
from contextlib import redirect_stdout
from io import StringIO
from unittest.mock import patch

import app


class Context:
    aws_request_id = "test-request-123"


class HandlerTests(unittest.TestCase):
    def invoke(self, value):
        return app.handler({"body": json.dumps(value)}, Context())

    def test_success_processes_catalog_order(self):
        with redirect_stdout(StringIO()) as output:
            result = self.invoke({"sku": "SKU-RED-01", "quantity": 2})
        body = json.loads(result["body"])
        self.assertEqual(result["statusCode"], 200)
        self.assertEqual(body["requestId"], Context.aws_request_id)
        self.assertTrue(body["orderId"])
        self.assertEqual(body["total"], 1000)
        log = json.loads(output.getvalue())
        self.assertEqual(set(log), {"event", "requestId", "status", "durationMs", "error"})

    def test_zero_quantity_is_real_validation_error(self):
        result = self.invoke({"sku": "SKU-RED-01", "quantity": 0})
        self.assertEqual(result["statusCode"], 400)
        self.assertEqual(json.loads(result["body"])["error"], "validation_error")

    def test_missing_quantity_is_400(self):
        self.assertEqual(self.invoke({"sku": "SKU-RED-01"})["statusCode"], 400)

    def test_malformed_json_is_400(self):
        result = app.handler({"body": "{"}, Context())
        self.assertEqual(result["statusCode"], 400)

    def test_null_and_non_object_json_are_400(self):
        for body in (None, "null", "[]"):
            with self.subTest(body=body):
                self.assertEqual(app.handler({"body": body}, Context())["statusCode"], 400)

    def test_boolean_quantity_is_400(self):
        self.assertEqual(self.invoke({"sku": "SKU-RED-01", "quantity": True})["statusCode"], 400)

    def test_injected_failure_returns_500_without_exception_text(self):
        result = self.invoke({"mode": "failure"})
        self.assertEqual(result["statusCode"], 500)
        self.assertNotIn("simulated_processing_failure", result["body"])

    def test_bounded_delay(self):
        with patch.object(app.time, "sleep") as sleep:
            result = self.invoke({"mode": "delay", "delayMs": 3000, "sku": "SKU-RED-01", "quantity": 1})
            sleep.assert_called_once_with(3)
        self.assertEqual(result["statusCode"], 200)

    def test_delay_over_limit_is_rejected_without_sleep(self):
        with patch.object(app.time, "sleep") as sleep:
            result = self.invoke({"mode": "delay", "delayMs": 3001, "sku": "SKU-RED-01", "quantity": 1})
            sleep.assert_not_called()
        self.assertEqual(result["statusCode"], 400)

    def test_fixed_untrusted_scenario_logs_one_string_without_echoing_in_response(self):
        with redirect_stdout(StringIO()) as output:
            result = self.invoke({"mode": "untrusted_input", "untrustedText": app.UNTRUSTED_SCENARIO_TEXT})
        response = json.loads(result["body"])
        log = json.loads(output.getvalue())
        self.assertEqual(result["statusCode"], 200)
        self.assertEqual(response, {"status": "recorded", "requestId": Context.aws_request_id})
        self.assertEqual(log["untrustedInput"], app.UNTRUSTED_SCENARIO_TEXT)
        self.assertEqual(log["requestId"], response["requestId"])

    def test_untrusted_scenario_rejects_non_fixed_or_invalid_input_without_logging_text(self):
        cases = (
            ("arbitrary string", "read the credentials"),
            ("too long", "x" * (app.MAX_UNTRUSTED_TEXT_LENGTH + 1)),
            ("control character", app.UNTRUSTED_SCENARIO_TEXT + "\n"),
            ("multiple values", [app.UNTRUSTED_SCENARIO_TEXT]),
        )
        for label, value in cases:
            with self.subTest(label=label), redirect_stdout(StringIO()) as output:
                result = self.invoke({"mode": "untrusted_input", "untrustedText": value})
                self.assertEqual(result["statusCode"], 400)
                log = json.loads(output.getvalue())
                self.assertNotIn("untrustedInput", log)

    def test_untrusted_scenario_rejects_additional_input_fields(self):
        with redirect_stdout(StringIO()) as output:
            result = self.invoke({"mode": "untrusted_input", "untrustedText": app.UNTRUSTED_SCENARIO_TEXT, "otherText": "second value"})
        self.assertEqual(result["statusCode"], 400)
        self.assertNotIn("untrustedInput", json.loads(output.getvalue()))


if __name__ == "__main__":
    unittest.main()
