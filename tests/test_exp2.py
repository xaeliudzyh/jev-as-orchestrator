import contextlib
import csv
import io
import json
import tempfile
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from exp2.data_exp2 import BUCKET_FIELDS, CORPUS_FIELDS, load_bucket, load_subagents
from routing_bench.backends import HTTPBackend, LocalBackend
from routing_bench.clef import CLEF_REVISIONS, ClefRuntime
from routing_bench.experiment import accuracy, common_parser, run


class SubagentTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.catalog = [{"target_subagent": name, "target_subagent_description": f"Exact description {name}\nsecond line",
                         "target_subagent_full_text": f"---\nname: {name}\ndescription: Exact description\n---\nFULL {name}"}
                        for name in "abcdef"]
        self.bucket = [{"Вопрос в новой корзине": "user question", "skill_name": "SECRET_GOLD_SKILL", "SubAgent": "f"}]
        for stem, rows, fields in (("catalog", self.catalog, CORPUS_FIELDS), ("bucket", self.bucket, BUCKET_FIELDS)):
            (self.root / f"{stem}.jsonl").write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in rows))
            with (self.root / f"{stem}.csv").open("w", encoding="utf-8-sig", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=fields, delimiter=";")
                writer.writeheader()
                writer.writerows(rows)
        (self.root / "prompt.txt").write_text("ROUTE")

    def args(self, *extra):
        return common_parser("test", "exp2").parse_args([
            "--model", "test/model", "--strategy", "2-step", "--shortlist-k", "3",
            "--bucket", str(self.root / "bucket.csv"), "--corpus", str(self.root / "catalog.jsonl"),
            "--system-prompt", str(self.root / "prompt.txt"), "--output-dir", str(self.root / "out"),
            "--env-file", str(self.root / "absent.env"), *extra])

    def execute(self, backend, *extra):
        if isinstance(backend, Mock):
            backend.info = {}
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = run(self.args(*extra), lambda _: backend)
        folder = next((self.root / "out").glob("*/*"))
        return code, folder

    def test_csv_and_jsonl_are_equivalent_and_exact(self):
        self.assertEqual(load_bucket(self.root / "bucket.csv"), load_bucket(self.root / "bucket.jsonl"))
        for suffix in ("csv", "jsonl"):
            candidates = load_subagents(self.root / f"catalog.{suffix}")
            self.assertEqual(candidates[0].description(False, []), self.catalog[0]["target_subagent_description"])
            self.assertEqual(candidates[0].description(True, []), self.catalog[0]["target_subagent_full_text"])
        self.assertEqual(len(candidates), 6)  # Includes candidates without any gold example.

    def test_two_calls_exact_inputs_shortlist_and_distinct_confidence(self):
        calls = []
        def backend(payload):
            calls.append(payload)
            names = list(payload["questions"]["subagent"]["criteria"])
            # First call selects f, e, d; second ranks e, f, d.
            order = ["f", "e", "d", "c", "b", "a"] if len(calls) == 1 else ["e", "f", "d"]
            weights = [0.3, 0.25, 0.2, 0.15, 0.07, 0.03] if len(calls) == 1 else [0.6, 0.3, 0.1]
            probabilities = dict(zip(order, weights))
            self.assertEqual(set(names), set(probabilities))
            return {"answers": {"subagent": {"type": "choice", "choice": order[0],
                    "probabilities": probabilities, "confidence": 0.123}}}
        code, folder = self.execute(backend)
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 2)
        self.assertEqual(calls[0]["state"], {"query": "user question"})
        self.assertEqual(calls[0]["state"], calls[1]["state"])
        first = calls[0]["questions"]["subagent"]
        second = calls[1]["questions"]["subagent"]
        self.assertEqual(first["instructions"], second["instructions"])
        self.assertEqual(first["criteria"], {r["target_subagent"]: r["target_subagent_description"] for r in self.catalog})
        self.assertEqual(list(second["criteria"]), ["f", "e", "d"])
        self.assertEqual(second["criteria"]["f"], self.catalog[-1]["target_subagent_full_text"])
        self.assertNotIn("SECRET_GOLD_SKILL", json.dumps(calls))
        self.assertNotIn("expected_subagents", json.dumps(calls))
        metrics = json.loads((folder / "metrics.json").read_text())
        self.assertEqual(metrics["accuracy@1"], 0)
        self.assertEqual(metrics["accuracy@3"], 1)
        self.assertEqual(metrics["accuracy@5"], 1)
        self.assertEqual(metrics["stage_1_metrics"]["accuracy@1"], 1)
        self.assertEqual(metrics["shortlist_hit_rate"], 1)
        winner = json.loads((folder / "stage_2_winners.jsonl").read_text())
        self.assertEqual(winner["confidence"], 0.123)
        self.assertEqual(winner["probability"], 0.6)
        self.assertTrue((folder / "subagents.json").is_file())
        self.assertFalse((folder / "skills.json").exists())

    def test_top_five_gets_five_full_texts(self):
        calls = []
        def backend(payload):
            calls.append(payload)
            criteria = payload["questions"]["subagent"]["criteria"]
            names = list(reversed(criteria))
            if len(calls) == 2:
                self.assertEqual(len(criteria), 5)
                self.assertTrue(all("FULL " in text for text in criteria.values()))
            denominator = sum(range(1, len(names) + 1))
            probabilities = {name: (len(names) - i) / denominator for i, name in enumerate(names)}
            return {"answers": {"subagent": {"choice": names[0], "probabilities": probabilities}}}
        code, folder = self.execute(backend, "--shortlist-k", "5")
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 2)
        self.assertEqual(json.loads((folder / "metrics.json").read_text())["accuracy@5"], 1)

    def test_shortlist_miss_stays_a_miss(self):
        def backend(payload):
            names = list(payload["questions"]["subagent"]["criteria"])
            return {"answers": {"subagent": {"choice": names[0], "probabilities": {n: 1/len(names) for n in names}}}}
        code, folder = self.execute(backend)
        metrics = json.loads((folder / "metrics.json").read_text())
        self.assertEqual(code, 0)
        self.assertEqual(metrics["shortlist_hit_rate"], 0)
        self.assertEqual(metrics["accuracy@5"], 0)

    def test_first_stage_error_skips_second_and_counts_miss(self):
        backend = Mock(side_effect=RuntimeError("inference failed"))
        code, folder = self.execute(backend)
        self.assertEqual(code, 1)
        self.assertEqual(backend.call_count, 1)
        metrics = json.loads((folder / "metrics.json").read_text())
        self.assertEqual(metrics["n_errors"], 1)
        self.assertEqual(metrics["n_samples"], 1)
        self.assertEqual(metrics["accuracy@1"], 0)
        self.assertEqual(json.loads((folder / "stage_2_winners.jsonl").read_text())["status"], "skipped")

    def test_one_step_has_one_call(self):
        backend = Mock(return_value={"answers": {"subagent": {"choice": "f", "probabilities": {n: float(n == "f") for n in "abcdef"}}}})
        code, folder = self.execute(backend, "--strategy", "1-step")
        self.assertEqual(code, 0)
        backend.assert_called_once()
        self.assertFalse((folder / "stage_2_winners.jsonl").exists())

    def test_missing_label_rejected_before_model(self):
        (self.root / "bucket.jsonl").write_text(json.dumps({**self.bucket[0], "SubAgent": "missing"}))
        with self.assertRaisesRegex(ValueError, "missing candidates"):
            self.execute(Mock(), "--bucket", str(self.root / "bucket.jsonl"))

    def test_duplicate_and_empty_candidates_rejected(self):
        path = self.root / "bad.jsonl"
        for rows in ([self.catalog[0], self.catalog[0]], [{**self.catalog[0], "target_subagent_description": ""}, self.catalog[1]]):
            path.write_text("\n".join(json.dumps(r) for r in rows))
            with self.assertRaises(ValueError):
                load_subagents(path)

    def test_malformed_columns_rejected(self):
        path = self.root / "bad.jsonl"
        path.write_text(json.dumps({**self.bucket[0], "unexpected": "bad"}))
        with self.assertRaisesRegex(ValueError, "columns"):
            load_bucket(path)

    def test_dry_run_never_initializes_model(self):
        backend = Mock(side_effect=AssertionError("must not call"))
        code, folder = self.execute(backend, "--dry-run")
        self.assertEqual(code, 0)
        backend.assert_not_called()
        self.assertEqual(len(json.loads((folder / "request_preview.json").read_text())["questions"]["subagent"]["criteria"]), 6)

    def test_hit_at_k_multi_label_semantics(self):
        metrics = accuracy([{"ranking": ["x", "b", "a"], "expected_subagents": ["a", "b"]},
                            {"ranking": [], "expected_subagents": ["a"]}], "expected_subagents")
        self.assertEqual(metrics["accuracy@1"], 0)
        self.assertEqual(metrics["accuracy@3"], 0.5)


class ClefTests(unittest.TestCase):
    def runtime(self, tokens, limit=8):
        runtime = ClefRuntime.__new__(ClefRuntime)
        runtime.limit = limit
        runtime.processor = types.SimpleNamespace(tokenizer="TOKENIZER")
        runtime.model = object()
        runtime.native = Mock()
        runtime.native.encode_record.return_value = types.SimpleNamespace(input_ids=tuple(range(tokens)))
        runtime.native.systemone.return_value = {"usage": {"input_tokens": tokens}, "answers": {}}
        return runtime

    def test_local_adapter_resolves_pinned_release_and_quantization(self):
        hub = types.ModuleType("huggingface_hub")
        hub.snapshot_download = Mock(return_value="/reviewed-checkpoint")
        with patch.dict(sys.modules, {"torch": types.ModuleType("torch"), "huggingface_hub": hub}), \
             patch("routing_bench.clef.ClefRuntime") as runtime_class:
            runtime_class.return_value.info = {"quantization": "4bit"}
            backend = LocalBackend("Cloudflare/clef-flash", "auto", device="cuda", clef_quantization="4bit")
            self.assertEqual(backend.adapter, "clef")
            hub.snapshot_download.assert_called_once_with("Cloudflare/clef-flash",
                revision=CLEF_REVISIONS["Cloudflare/clef-flash"], cache_dir=None)
            runtime_class.assert_called_once_with("/reviewed-checkpoint", "cuda", None, 32768, "4bit")
            payload = {"questions": {"subagent": {}}, "state": "full"}
            backend(payload)
            runtime_class.return_value.assert_called_once_with(payload)
            self.assertEqual(backend.info["revision"], CLEF_REVISIONS["Cloudflare/clef-flash"])

    def test_full_input_overflow_fails_before_forward(self):
        runtime = self.runtime(9)
        with self.assertRaisesRegex(ValueError, "input was not truncated"):
            runtime({"state": "full"})
        runtime.native.systemone.assert_not_called()
        self.assertEqual(runtime.native.encode_record.call_args.kwargs["max_length"], 2**31 - 1)

    def test_native_call_keeps_payload_and_checks_token_count(self):
        runtime = self.runtime(8)
        payload = {"state": "full", "questions": {"subagent": {"type": "choice"}}}
        result = runtime(payload)
        runtime.native.systemone.assert_called_once_with(runtime.model, runtime.processor, payload, max_length=8)
        self.assertEqual(result["benchmark_input_limits"]["complete_input_tokens"], 8)
        runtime.native.systemone.return_value = {"usage": {"input_tokens": 7}}
        with self.assertRaisesRegex(ValueError, "possible truncation"):
            runtime(payload)


class OpenRouterModelsTests(unittest.TestCase):
    def test_jev_and_luna_use_native_decisions_wire_format(self):
        for model in ("typesafe/jev-1.14", "openai/gpt-6-luna-decisions"):
            with self.subTest(model=model):
                backend = HTTPBackend("https://openrouter.ai/api/alpha/decisions", "not-a-real-key", 10, 0)
                self.addCleanup(backend.close)
                response = Mock(ok=True)
                response.json.return_value = {"answers": {"subagent": {"choice": "a", "confidence": .7,
                                                "probabilities": {"a": .8, "b": .2}}}}
                backend.session.post = Mock(return_value=response)
                payload = {"model": model, "state": {"query": "question"}, "questions": {"subagent": {
                    "type": "choice", "instructions": "route", "criteria": {"a": "A", "b": "B"}}}}
                self.assertEqual(backend(payload)["answers"]["subagent"]["confidence"], .7)
                self.assertEqual(backend.session.post.call_args.kwargs["json"], payload)
