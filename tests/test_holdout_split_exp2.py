import importlib.util
from pathlib import Path
import unittest

SPEC = importlib.util.spec_from_file_location("prepare_holdout_exp2", Path(__file__).resolve().parents[1] / "exp2/prepare_holdout_exp2.py")
splitter = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(splitter)


def rows(counts):
    return [{"Вопрос в новой корзине": f"Question {label} {i}", "skill_name": "unused", "SubAgent": label}
            for label, count in counts.items() for i in range(count)]


class StratifiedSplitTests(unittest.TestCase):
    def test_disjoint_exhaustive_deterministic_and_order_preserving(self):
        data = rows({"a": 76, "b": 25, "c": 18, "d": 14, "e": 13, "f": 12,
                     "g": 11, "h": 8, "i": 7, "j": 5, "k": 2, "l": 2})
        result = splitter.split_indices(data)
        self.assertEqual(result, splitter.split_indices(data))
        self.assertEqual(len(result["train"]), 135)
        self.assertEqual(len(result["test"]), 58)
        self.assertFalse(set(result["train"]) & set(result["test"]))
        self.assertEqual(sorted(result["train"] + result["test"]), list(range(193)))
        for subset in result.values():
            self.assertEqual(subset, sorted(subset))
            self.assertEqual({data[i]["SubAgent"] for i in subset}, set("abcdefghijkl"))

    def test_seed_changes_membership_without_changing_quotas(self):
        data = rows({"a": 30, "b": 12})
        first = splitter.split_indices(data, seed=42)
        second = splitter.split_indices(data, seed=43)
        self.assertNotEqual(first, second)
        for subset in first:
            self.assertEqual([sum(data[i]["SubAgent"] == label for i in first[subset]) for label in ("a", "b")],
                             [sum(data[i]["SubAgent"] == label for i in second[subset]) for label in ("a", "b")])

    def test_rare_classes_appear_in_both_parts(self):
        data = rows({"a": 2, "b": 2, "c": 20})
        result = splitter.split_indices(data)
        self.assertEqual(len(result["test"]), 8)
        for subset in result.values():
            for label in ("a", "b"):
                self.assertEqual(sum(data[i]["SubAgent"] == label for i in subset), 1)

    def test_normalized_duplicates_even_across_labels_rejected(self):
        data = rows({"a": 2, "b": 2})
        data[0]["Вопрос в новой корзине"] = "  ＱＵＥＳＴＩＯＮ \n SAME "
        data[2]["Вопрос в новой корзине"] = "question same"
        with self.assertRaisesRegex(ValueError, "duplicate"):
            splitter.split_indices(data)

    def test_singleton_and_unrepresentable_ratio_rejected(self):
        with self.assertRaisesRegex(ValueError, "at least two"):
            splitter.split_indices(rows({"a": 1, "b": 4}))
        with self.assertRaisesRegex(ValueError, "represent every label"):
            splitter.split_indices(rows({"a": 2, "b": 2, "c": 2}))


if __name__ == "__main__":
    unittest.main()
