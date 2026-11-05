import contextlib
import dataclasses
import io
import json
import math
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from routing_bench.backends import HTTPBackend, LocalBackend
from routing_bench.data import load_corpus, load_skills
from routing_bench.experiment import accuracy, common_parser, rank_response, run


class Fixtures(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.skills = self.root / "skills"
        self.skills.mkdir()
        for name in "abcdef":
            meta = f"name: {name}\ntitle: title {name}\ndescription: description {name}\ntags: [hr]\n"
            (self.skills / f"{name}.md").write_text(f"---\n{meta}---\nFULL BODY {name}\n", encoding="utf-8")
        self.corpus = self.root / "corpus.json"
        self.corpus.write_text(json.dumps({"examples": [
            {"question": "q0", "expected_skills": ["b"]},
            {"question": "q1", "expected_skills": ["f"]},
            {"question": "q2", "expected_skills": ["missing", "b"]},
            {"question": "q3", "expected_skills": ["missing"]},
        ]}), encoding="utf-8")
        self.prompt = self.root / "prompt.txt"
        self.prompt.write_text("PROMPT", encoding="utf-8")

    def args(self, strategy="2-step", *extra):
        return common_parser("test").parse_args(["--model", "test/model", "--strategy", strategy,
            "--skills-dir", str(self.skills), "--corpus", str(self.corpus),
            "--system-prompt", str(self.prompt), "--output-dir", str(self.root / "experiments"),
            "--env-file", str(self.root / "absent.env"), *extra])

    def experiment(self):
        return next((self.root / "experiments").glob("*/*"))

    def quiet_run(self, args, backend):
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            return run(args, lambda _: backend)


class DataTests(Fixtures):
    def test_metadata_and_original_text(self):
        skills = load_skills(self.skills)
        self.assertEqual(len(skills), 6)
        self.assertNotIn("FULL BODY", skills[0].description(False, ["name", "description"]))
        self.assertEqual(skills[0].description(True, ["name"]), (self.skills / "a.md").read_text())

    def test_yaml_recipe_and_duplicate_names(self):
        (self.skills / "recipe.yaml").write_text("name: recipe\ntitle: Recipe\ndescription: Test\ntags: []\nsteps: [one]\n")
        self.assertEqual(len(load_skills(self.skills)), 7)
        (self.skills / "copy.yaml").write_text("name: a\ntitle: Copy\ndescription: Test\n")
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            load_skills(self.skills)

    def test_corpus_ids_and_multiple_labels(self):
        rows = load_corpus(self.corpus)
        self.assertEqual(rows[2]["expected_skills"], ["missing", "b"])
        self.assertEqual(rows[3]["sample_id"], 3)

    def test_bad_inputs_fail_before_network(self):
        (self.skills / "a.md").write_text("# No front matter")
        with self.assertRaisesRegex(ValueError, "front matter"):
            self.quiet_run(self.args(), Mock())


class ProbabilityTests(unittest.TestCase):
    def response(self, p, **extra):
        return {"answers": {"skill": dict(probabilities=p, **extra)}}

    def test_full_distribution_order_and_rounding(self):
        rank, mass = rank_response(self.response({"a": .3333, "b": .3333, "c": .3333}, choice="b"), ["a", "b", "c"])
        self.assertEqual([r["skill"] for r in rank], ["b", "a", "c"])
        self.assertAlmostEqual(sum(r["probability"] for r in rank), 1)
        self.assertAlmostEqual(mass, .9999)

    def test_reject_invalid_distributions(self):
        for p in ({"a": 1}, {"a": 1, "b": 0, "c": 0}, {"a": math.nan, "b": 1},
                  {"a": -1, "b": 2}, {"a": True, "b": False}, {"a": 0, "b": 0}, {"a": .1, "b": .1}):
            with self.subTest(p=p), self.assertRaises(ValueError):
                rank_response(self.response(p), ["a", "b"])

    def test_reject_inconsistent_choice_and_truncation(self):
        with self.assertRaises(ValueError):
            rank_response(self.response({"a": .9, "b": .1}, choice="b"), ["a", "b"])
        response = self.response({"a": .9, "b": .1})
        response["truncated"] = True
        with self.assertRaises(ValueError):
            rank_response(response, ["a", "b"])

    def test_accuracy_multi_label_and_failures(self):
        result = accuracy([{"expected_skills": ["b", "c"], "ranking": ["a", "b"]},
                           {"expected_skills": ["b"], "ranking": []}])
        self.assertEqual(result["accuracy@1"], 0)
        self.assertEqual(result["accuracy@3"], .5)


class FakeBackend:
    info = {"adapter": "test"}

    def __init__(self, fail=None):
        self.requests = []
        self.fail = fail
        self.closed = False

    def __call__(self, payload):
        self.requests.append(payload)
        if payload["state"]["query"] == self.fail:
            raise RuntimeError("network failed")
        criteria = payload["questions"]["skill"]["criteria"]
        names = list(criteria)
        if len(names) < 6:
            names.sort(key=lambda n: n != "b")
        weights = {name: len(names) - i for i, name in enumerate(names)}
        total = sum(weights.values())
        return {"model": "resolved/test", "answers": {"skill": {"type": "choice", "choice": names[0],
            "probabilities": {n: p / total for n, p in weights.items()}}}, "usage": {"input_tokens": 10}}

    def close(self):
        self.closed = True


class ExperimentTests(Fixtures):
    def test_two_step_rerank_no_label_leak_and_shortlist_miss(self):
        backend = FakeBackend()
        self.assertEqual(self.quiet_run(self.args("2-step", "--shortlist-k", "3"), backend), 0)
        self.assertEqual(len(backend.requests), 8)
        for payload in backend.requests:
            self.assertEqual(set(payload["state"]), {"query"})
            self.assertNotIn("expected_skills", json.dumps(payload))
        self.assertNotIn("FULL BODY", json.dumps(backend.requests[0]))
        self.assertIn("FULL BODY", json.dumps(backend.requests[1]))
        self.assertEqual(list(backend.requests[1]["questions"]["skill"]["criteria"]), ["a", "b", "c"])
        folder = self.experiment()
        metrics = json.loads((folder / "metrics.json").read_text())
        self.assertEqual(metrics["evaluated_stage"], 2)
        self.assertEqual(metrics["accuracy@1"], .5)
        self.assertEqual(metrics["accuracy@5"], .5)
        self.assertEqual(metrics["n_unreachable_samples"], 1)
        self.assertTrue(backend.closed)
        details = [json.loads(line) for line in (folder / "stage_1_details.jsonl").read_text().splitlines()]
        self.assertEqual(len(details), 4)
        self.assertEqual(len(details[0]["probabilities"]), 6)
        self.assertIn("usage", details[0]["response"])

    def test_one_step_top_k_metrics(self):
        self.assertEqual(self.quiet_run(self.args("1-step"), FakeBackend()), 0)
        metrics = json.loads((self.experiment() / "metrics.json").read_text())
        self.assertEqual(metrics["accuracy@1"], 0)
        self.assertEqual(metrics["accuracy@3"], .5)
        self.assertFalse((self.experiment() / "stage_2_winners.jsonl").exists())

    def test_error_is_miss_and_failed_first_stage_skips_second(self):
        backend = FakeBackend(fail="q0")
        self.assertEqual(self.quiet_run(self.args(), backend), 1)
        folder = self.experiment()
        metrics = json.loads((folder / "metrics.json").read_text())
        self.assertEqual(metrics["n_samples"], 4)
        self.assertEqual(metrics["accuracy@1"], .25)
        self.assertEqual(metrics["n_errors"], 1)
        self.assertEqual(len(backend.requests), 7)
        second = json.loads((folder / "stage_2_winners.jsonl").read_text().splitlines()[0])
        self.assertEqual(second["status"], "skipped")

    def test_dry_run_never_initializes_backend(self):
        args = self.args("2", "--dry-run")
        factory = Mock(side_effect=AssertionError("backend initialized"))
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(run(args, factory), 0)
        factory.assert_not_called()
        self.assertIsNone(json.loads((self.experiment() / "metrics.json").read_text())["accuracy@1"])

    def test_fail_fast_preserves_partial_artifacts(self):
        self.assertEqual(self.quiet_run(self.args("2-step", "--fail-fast"), FakeBackend("q1")), 1)
        metrics = json.loads((self.experiment() / "metrics.json").read_text())
        self.assertEqual(metrics["n_samples"], 2)
        self.assertEqual(metrics["n_samples_unprocessed"], 2)
        self.assertFalse(metrics["complete"])

    def test_initialization_failure_preserves_config_and_metrics(self):
        def factory(args):
            raise RuntimeError("No GPU")
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            self.assertEqual(run(self.args(), factory), 1)
        metrics = json.loads((self.experiment() / "metrics.json").read_text())
        self.assertEqual(metrics["status"], "failed")
        self.assertIsNone(metrics["accuracy@1"])

    def test_missing_labels_can_be_strict(self):
        with self.assertRaisesRegex(ValueError, "missing candidates"):
            self.quiet_run(self.args("1-step", "--missing-labels", "error"), FakeBackend())

    def test_slice_keeps_original_ids(self):
        self.quiet_run(self.args("1-step", "--offset", "1", "--limit", "2"), FakeBackend())
        rows = json.loads((self.experiment() / "samples.json").read_text())
        self.assertEqual([row["sample_id"] for row in rows], [1, 2])


class HTTPTests(unittest.TestCase):
    def test_handler_envelope_and_token(self):
        backend = HTTPBackend("https://example.test", "secret", 12, 0, "hf-handler")
        response = Mock(ok=True)
        response.json.return_value = [{"answers": {}}]
        with patch.object(backend.session, "post", return_value=response) as post:
            self.assertEqual(backend({"state": "q"}), {"answers": {}})
        self.assertEqual(post.call_args.kwargs["json"], {"inputs": {"state": "q"}})
        self.assertEqual(backend.session.headers["Authorization"], "Bearer secret")
        self.assertNotIn("secret", json.dumps(backend.info))
        backend.close()

    def test_retry_rate_limit_but_not_auth_error(self):
        backend = HTTPBackend("https://example.test", None, 12, 1)
        busy = Mock(status_code=429, headers={"Retry-After": "0"}, ok=False)
        ok = Mock(ok=True)
        ok.json.return_value = {"answers": {}}
        with patch.object(backend.session, "post", side_effect=[busy, ok]) as post, patch("time.sleep"):
            backend({"state": "q"})
            self.assertEqual(post.call_count, 2)
        unauthorized = Mock(status_code=401, ok=False, text="unauthorized")
        with patch.object(backend.session, "post", return_value=unauthorized) as post:
            with self.assertRaisesRegex(RuntimeError, "HTTP 401"):
                backend({})
            self.assertEqual(post.call_count, 1)
        backend.close()


class FridaTests(unittest.TestCase):
    def test_full_text_expands_native_truncation_caps(self):
        @dataclasses.dataclass
        class Config:
            instruction_max_tokens: int = 96
            option_max_tokens: int = 256
            max_row_tokens: int = 1024
            max_options_per_row: int = 16
        candidate = types.SimpleNamespace(state="q" * 30, instruction="i" * 200, text="t" * 1800)
        judge = Mock()
        judge.config = Config()
        judge.state_max = 384
        judge.compile.return_value = (None, [candidate], None)
        judge.text.count = len
        judge.return_value = {"answers": {}}
        observed = []
        judge.side_effect = lambda _: observed.append((judge.config, judge.state_max)) or {"answers": {}}
        backend = object.__new__(LocalBackend)
        backend.adapter, backend.limit, backend.model = "frida", 4096, judge
        result = backend({"questions": {}})
        self.assertEqual(observed[0][0].option_max_tokens, 1801)
        self.assertEqual(observed[0][0].instruction_max_tokens, 200)
        self.assertEqual(observed[0][1], 30)
        self.assertEqual(judge.config, Config())
        self.assertEqual(judge.state_max, 384)
        self.assertEqual(result["benchmark_input_limits"]["max_row_tokens"], 2031)
        self.assertTrue(result["benchmark_input_limits"]["expanded_native_caps"])
        # The next metadata-only call must use the native row budget again.
        candidate.text = "short"
        second = backend({"questions": {}})
        self.assertEqual(second["benchmark_input_limits"]["max_row_tokens"], 1024)
        self.assertEqual(observed[1][0].option_max_tokens, 6)
        # Restore configuration even after an OOM or other forward error.
        candidate.text = "t" * 1800
        judge.side_effect = RuntimeError("CUDA out of memory")
        with self.assertRaisesRegex(RuntimeError, "CUDA out of memory"):
            backend({"questions": {}})
        self.assertEqual(judge.config, Config())
        self.assertEqual(judge.state_max, 384)
        backend.limit = 100
        with self.assertRaisesRegex(ValueError, "not truncated"):
            backend({})


class NativeContractTests(unittest.TestCase):
    def test_jeva_closed_choice_preserves_diagnostics_and_limits(self):
        module = types.ModuleType("jeva")
        @dataclasses.dataclass
        class Choice:
            instructions: str
            criteria: dict
            allow_null: bool = False
        module.Choice = Choice
        module.Jeva = Mock()
        model = module.Jeva.from_pretrained.return_value
        @dataclasses.dataclass
        class ModelConfig:
            torch_dtype: str = "bfloat16"
        @dataclasses.dataclass
        class Config:
            model: ModelConfig = dataclasses.field(default_factory=ModelConfig)
        model.config = Config()
        model.checkpoint_dir = Path("/checkpoint")
        model.max_state_length, model.max_option_length = 9000, 8192
        native = {"answers": {"skill": {"kind": "choice", "choice": "a",
            "probabilities": {"a": .7, "b": .3}, "conditional_probabilities": {"a": .7, "b": .3},
            "null_probability": 0.0, "confidence": .7}},
            "usage": {"input_tokens": 20, "cached_option_tokens": 0, "truncated_tokens": 0},
            "latency_ms": 1.5}
        model.system_one.return_value.to_dict.side_effect = lambda: json.loads(json.dumps(native))
        payload = {"model": "LIQIIIII/Jeva-4B", "state": {"query": "hello"},
            "questions": {"skill": {"type": "choice", "instructions": "route", "criteria": {"a": "A", "b": "B"}}}}
        with patch.dict(sys.modules, {"jeva": module, "torch": types.ModuleType("torch")}):
            backend = LocalBackend(payload["model"], "auto", device="cuda", max_input_tokens=2000)
            self.assertEqual(backend.adapter, "jeva")
            self.assertEqual((model.max_state_length, model.max_option_length), (2000, 2000))
            module.Jeva.from_pretrained.assert_called_once_with(payload["model"], device="cuda", dtype=None,
                cache="none", truncation="error", option_chunk_size=1, option_batch_tokens=4096)
            result = backend(payload)
            model.system_one.assert_called_once_with(state=payload["state"], questions={"skill": Choice("route", {"a": "A", "b": "B"}, False)})
            ranking, _ = rank_response(result, ["a", "b"])
            self.assertEqual(ranking[0]["skill"], "a")
            self.assertEqual(result["answers"]["skill"]["confidence"], .7)
            self.assertEqual(result["usage"], native["usage"])
            self.assertEqual(result["latency_ms"], 1.5)
            native["usage"]["truncated_tokens"] = 1
            with self.assertRaisesRegex(ValueError, "truncated"):
                rank_response(backend(payload), ["a", "b"])
            backend.close()
            model.clear_cache.assert_called_once()

    def test_decider_passes_both_request_parts_without_cutting(self):
        module = types.ModuleType("decider.systemone")
        module.render_state = lambda state: state["query"]
        module.render_question = lambda spec: {"question": spec["instructions"], "options": list(spec["criteria"].values())}
        model = Mock()
        model.m.tok.encode.side_effect = lambda text, **kw: list(text)
        model.system_one.return_value = {"answers": {}}
        backend = object.__new__(LocalBackend)
        backend.adapter, backend.model, backend.limit = "decider", model, 2000
        payload = {"state": {"query": "hello"}, "questions": {"skill": {"instructions": "route", "criteria": {"a": "A", "b": "B"}}}}
        with patch.dict(sys.modules, {"decider.systemone": module}):
            self.assertEqual(backend(payload), {"answers": {}})
            model.system_one.assert_called_once_with(payload["state"], payload["questions"], max_state_tokens=2000, layout="state_first")
            backend.limit = 2
            with self.assertRaises(ValueError):
                backend(payload)

    def test_kev_admission_limit_and_request_contract(self):
        api = types.ModuleType("kev.api")
        api.SystemOneRequest = lambda **kw: kw
        api.to_record = lambda request: (request, {})
        model_api = types.ModuleType("kev.model")
        model_api.admit = Mock(return_value={"ids": list(range(20))})
        server = Mock()
        server.answer.return_value = {"answers": {}}
        backend = object.__new__(LocalBackend)
        backend.adapter, backend.model, backend.limit = "kev", server, 25
        with patch.dict(sys.modules, {"kev.api": api, "kev.model": model_api}):
            self.assertEqual(backend({"state": "q"}), {"answers": {}})
            model_api.admit.assert_called_once_with(server.model, server.tok, {"state": "q"}, truncate=False)
            backend.limit = 19
            with self.assertRaisesRegex(ValueError, "not truncated"):
                backend({"state": "q"})


if __name__ == "__main__":
    unittest.main()
