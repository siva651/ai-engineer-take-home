import unittest

from evaluate import canonical, score_report, token_usage


class EvaluationTests(unittest.TestCase):
    def test_numeric_and_whitespace_formatting_are_normalized(self):
        self.assertEqual(canonical(" 13.50 ", "value"), canonical(13.5, "value"))
        self.assertEqual(canonical(" 13.0 - 17.0 ", "reference_range"), canonical("13.0-17.0", "reference_range"))

    def test_textual_value_and_null_unit_supported(self):
        self.assertEqual(canonical("Dark Yellow", "value"), "dark yellow")
        self.assertEqual(canonical(None, "unit"), "<null>")


    def test_cost_is_estimated_only_for_configured_model(self):
        class FakeModule:
            MODEL = "gemini-3.8-flash"
            LAST_USAGE = {"promptTokenCount": 1000, "candidatesTokenCount": 200, "thoughtsTokenCount": 100}
        result = token_usage(FakeModule)
        self.assertAlmostEqual(result["paid_tier_cost_estimate_usd"], (1000 * 0.75 + 300 * 3.75) / 1_000_000)
        FakeModule.MODEL = "gemini-3.5-flash-lite"
        result = token_usage(FakeModule)
        self.assertAlmostEqual(result["paid_tier_cost_estimate_usd"], (1000 * 0.30 + 300 * 2.50) / 1_000_000)
        FakeModule.MODEL = "unconfigured-model"
        result = token_usage(FakeModule)
        self.assertIsNone(result["paid_tier_cost_estimate_usd"])
        self.assertIn("No configured pricing", result["note"])

    def test_exact_row_metrics(self):
        gold = [
            {"test_name": "Colour", "value": "Dark Yellow", "unit": None, "reference_range": "Pale Yellow"},
            {"test_name": "CRP", "value": "<0.5", "unit": "mg/L", "reference_range": "<5.0"},
        ]
        predicted = [dict(gold[0]), {**gold[1], "unit": None}]
        result = score_report(predicted, gold)
        self.assertEqual(result["exact_row_matches"], 1)
        self.assertEqual(result["gold_rows"], 2)
        self.assertEqual(result["row_precision"], 0.5)
        self.assertEqual(result["row_recall"], 0.5)
        self.assertEqual(result["row_f1"], 0.5)

    def test_order_is_part_of_sequence_match(self):
        a = {"test_name": "A", "value": "1", "unit": None, "reference_range": None}
        b = {"test_name": "B", "value": "2", "unit": None, "reference_range": None}
        self.assertFalse(score_report([b, a], [a, b])["exact_sequence_match"])
        self.assertEqual(score_report([b, a], [a, b])["row_f1"], 1.0)


if __name__ == "__main__":
    unittest.main()
