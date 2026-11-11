"""Regression tests for the offline experiment-2 confidence analysis."""
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "exp2" / "analyze_confidence_exp2.py"
SPEC = importlib.util.spec_from_file_location("analyze_confidence_exp2", SCRIPT)
analysis = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(analysis)


def row(confidence, correct=True, status="ok", probability=None):
    return {"confidence": confidence, "correct": correct, "status": status,
            "winner_probability": probability}


class OperatingPointTests(unittest.TestCase):
    def test_uses_native_confidence_instead_of_winner_probability(self):
        rows = [row(.2, True, probability=.99), row(.9, False, probability=.01)]
        point = analysis.operating_point(rows, .8)
        self.assertEqual(point["accepted"], 1)
        self.assertEqual(point["accepted_correct"], 0)
        self.assertEqual(point["accuracy_at_1_accepted"], 0)

    def test_threshold_is_inclusive_and_keeps_all_ties(self):
        rows = [row(.8), row(.8, False), row(.79), row(1)]
        point = analysis.operating_point(rows, .8)
        self.assertEqual(point["accepted"], 3)
        self.assertEqual(point["accepted_correct"], 2)
        self.assertEqual(point["rejected_low_confidence"], 1)
        self.assertEqual(point["coverage"], 3 / 4)

    def test_unavailable_is_not_counted_as_low_confidence(self):
        # A failed request cannot become available even if a stale score is present.
        rows = [row(.95), row(.2, False), row(None), row(.99, False, "api_error")]
        point = analysis.operating_point(rows, .8)
        self.assertEqual(point["n_total"], 4)
        self.assertEqual(point["accepted"], 1)
        self.assertEqual(point["coverage"], .25)
        self.assertEqual(point["unavailable"], 2)
        self.assertEqual(point["rejected_low_confidence"], 1)
        self.assertEqual(point["rejected_total"], 3)
        self.assertEqual(point["rejected_wrong_predictions"], 1)
        self.assertEqual(point["withheld_correct"], 1)

    def test_zero_accepted_has_null_accuracy_and_interval(self):
        point = analysis.operating_point([row(.5), row(None, False, "api_error")], .9)
        self.assertEqual(point["accepted"], 0)
        self.assertEqual(point["coverage"], 0)
        self.assertEqual(point["accepted_correct"], 0)
        self.assertIsNone(point["accuracy_at_1_accepted"])
        self.assertIsNone(point["accuracy_wilson_95_nominal"])
        self.assertEqual(point["rejected_total"], 2)
        self.assertEqual(point["correct_automated_share_of_bucket"], 0)

    def test_empty_bucket_has_no_coverage_denominator(self):
        point = analysis.operating_point([], 0)
        self.assertEqual(point["n_total"], 0)
        self.assertIsNone(point["coverage"])
        self.assertIsNone(point["correct_automated_share_of_bucket"])

    def test_threshold_validation_rejects_nonfinite_and_nonnumeric_values(self):
        for value in (-.01, 1.01, float("nan"), float("inf"), True, "0.8", None):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    analysis.operating_point([], value)


class SweepAndSelectionTests(unittest.TestCase):
    def test_sweep_uses_unique_native_confidences_and_both_endpoints(self):
        points = analysis.sweep([row(.8), row(.8), row(.3), row(1), row(None),
                                 row(.6, False, "api_error"), row(.2, probability=.77)])
        self.assertEqual([p["threshold"] for p in points], [0, .2, .3, .8, 1])
        self.assertEqual([p["accepted"] for p in points], [5, 5, 4, 3, 1])

    def test_coverage_floor_applies_to_all_requests(self):
        rows = [row(.9), row(.9), row(.2, False), row(None, False, "api_error")]
        points = analysis.sweep(rows)
        best = analysis.best_point(points, .75)
        self.assertEqual(best["accepted"], 3)
        self.assertEqual(best["coverage"], .75)
        self.assertAlmostEqual(best["accuracy_at_1_accepted"], 2 / 3)
        self.assertIsNone(analysis.best_point(points, .8))
        best_half = analysis.best_point(points, .5)
        self.assertEqual(best_half["accepted"], 2)
        self.assertEqual(best_half["accuracy_at_1_accepted"], 1)

    def test_equal_rational_accuracy_prefers_more_coverage(self):
        points = [
            {"accepted_correct": 1, "accepted": 3, "coverage": .3, "threshold": .9},
            {"accepted_correct": 2, "accepted": 6, "coverage": .6, "threshold": .8},
        ]
        self.assertIs(analysis.best_point(points, .3), points[1])

    def test_exact_accuracy_comparison_does_not_round_nearby_fractions(self):
        # Both accuracies round to 1.0 as floats, but the smaller sample is perfect.
        n = 10 ** 18
        points = [
            {"accepted_correct": n - 1, "accepted": n, "coverage": 1, "threshold": .1},
            {"accepted_correct": n - 1, "accepted": n - 1, "coverage": .99, "threshold": .9},
        ]
        self.assertIs(analysis.best_point(points, .5), points[1])

    def test_equal_counts_prefer_lower_threshold(self):
        points = [
            {"accepted_correct": 2, "accepted": 3, "coverage": .6, "threshold": .8},
            {"accepted_correct": 2, "accepted": 3, "coverage": .6, "threshold": .3},
        ]
        self.assertIs(analysis.best_point(points, .5), points[1])

    def test_no_accepted_points_never_produce_a_recommendation(self):
        self.assertIsNone(analysis.best_point(analysis.sweep([row(None)]), 0))

    def test_bins_have_unique_boundary_membership_and_include_one(self):
        scores = [0, .5, .7, .8, .9, .95, .98, 1]
        rows = [row(value, value != .98) for value in scores]
        rows.extend([row(None), row(.99, False, "api_error")])
        bins = analysis.bins(rows)
        self.assertEqual([b["n"] for b in bins], [1, 1, 1, 1, 1, 1, 2])
        self.assertEqual(sum(b["n"] for b in bins), len(scores))
        self.assertEqual([b["upper_inclusive"] for b in bins], [False] * 6 + [True])
        self.assertEqual(bins[-1]["correct"], 1)
        self.assertEqual(bins[-1]["accuracy_at_1"], .5)
        self.assertEqual(bins[-1]["mean_native_confidence"], .99)


class LoadViewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.config = {
            "question_id": "routing", "n_samples": 3,
            "arguments": {"system_prompt": "exp2/routing_exp2.txt"},
            "model": "typesafe/jev-1.14", "started_at": "2026-11-10T00:00:00Z",
            "system_prompt": "test prompt", "bucket_sha256": "bucket", "corpus_sha256": "corpus",
        }
        self.metrics = {"evaluated_stage": 1, "hits": {"accuracy@1": 2}, "accuracy@1": 2 / 3}
        self.details = [
            self.detail("low-native", .2, .99),
            self.detail("missing-native", None, .98),
            self.detail("api-failure", None, None, status="api_error"),
        ]

    @staticmethod
    def detail(sample_id, confidence, probability, status="ok"):
        answer = {} if confidence is None else {"confidence": confidence}
        return {"sample_id": sample_id, "question": sample_id, "status": status,
                "response": {"answers": {"routing": answer}}, "confidence": confidence,
                "probability": probability, "winner": "agent-a" if status == "ok" else None,
                "ranking": ["agent-a"] if status == "ok" else [], "expected_subagents": ["agent-a"]}

    def load(self):
        (self.folder / "config.json").write_text(json.dumps(self.config), encoding="utf-8")
        (self.folder / "metrics.json").write_text(json.dumps(self.metrics), encoding="utf-8")
        (self.folder / "stage_1_details.jsonl").write_text(
            "\n".join(json.dumps(detail) for detail in self.details) + "\n", encoding="utf-8")
        return analysis.load_view(self.folder, self.config, self.metrics)

    def test_native_field_and_separate_failure_missing_counts_survive_loading(self):
        view = self.load()
        self.assertEqual(view["rows"][0]["confidence"], .2)
        self.assertEqual(view["rows"][0]["winner_probability"], .99)
        self.assertIsNone(view["rows"][1]["confidence"])
        self.assertEqual(view["status_counts"], {"ok": 2, "api_error": 1})
        self.assertEqual(view["missing_confidence_on_success"], 1)
        self.assertEqual(view["baseline_hits"], 2)
        self.assertEqual(view["fixed_thresholds"][0]["accepted"], 1)
        self.assertEqual(view["fixed_thresholds"][0]["unavailable"], 2)
        self.assertEqual(analysis.operating_point(view["rows"], .8)["accepted"], 0)

    def test_disagreement_between_saved_and_native_confidence_is_rejected(self):
        self.details[0]["confidence"] = .99
        with self.assertRaisesRegex(ValueError, "differs from native"):
            self.load()

    def test_invalid_native_confidence_is_rejected(self):
        for value in (-.1, 1.1, float("nan"), float("inf"), True, "0.8"):
            with self.subTest(value=value):
                self.details[0]["confidence"] = value
                self.details[0]["response"]["answers"]["routing"]["confidence"] = value
                with self.assertRaisesRegex(ValueError, "Invalid native confidence"):
                    self.load()


if __name__ == "__main__":
    unittest.main()
