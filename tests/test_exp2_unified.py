import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from exp2.run_exp2 import OPENROUTER_ENDPOINT, backend_exp2, parser_exp2, prepare_args_exp2
from routing_bench.experiment import run


class UnifiedExp2Tests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        catalog = [{"target_subagent": name, "target_subagent_description": f"SHORT {name}",
                    "target_subagent_full_text": f"FULL {name}", "enabled": 1} for name in "abcd"]
        bucket = [{"Вопрос в новой корзине": f"q{i}", "skill_name": "SECRET", "SubAgent": "d"} for i in range(2)]
        for name, rows in (("catalog", catalog), ("bucket", bucket)):
            (self.root / f"{name}.jsonl").write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
        (self.root / "prompt.txt").write_text("ROUTE", encoding="utf-8")

    def args(self, *extra):
        args = parser_exp2().parse_args(["--model", "test/model", "--shortlist-k", "3",
            "--bucket", str(self.root / "bucket.jsonl"), "--corpus", str(self.root / "catalog.jsonl"),
            "--system-prompt", str(self.root / "prompt.txt"), "--output-dir", str(self.root / "out"),
            "--env-file", str(self.root / "absent.env"), *extra])
        prepare_args_exp2(args)
        return args

    def execute(self, backend, *extra, factory=None):
        if isinstance(backend, Mock):
            backend.info = {}
        with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
            code = run(self.args(*extra), factory or (lambda _: backend))
        folders = {path.name: next(path.iterdir()) for path in (self.root / "out").iterdir()}
        return code, folders

    def answer(self, payload, winner="d"):
        names = list(payload["questions"]["subagent"]["criteria"])
        return {"answers": {"subagent": {"choice": winner,
            "probabilities": {name: float(name == winner) for name in names}, "confidence": 0.37}}}

    @staticmethod
    def read(folder, name="metrics.json"):
        return json.loads((folder / name).read_text(encoding="utf-8"))

    def test_default_two_step_shares_calls_and_exact_artifacts(self):
        calls = []
        def backend(payload):
            calls.append(payload)
            return self.answer(payload, "d" if len(calls) % 2 else "a")
        code, folders = self.execute(backend)
        self.assertEqual(code, 0)
        self.assertEqual(len(calls), 4)
        first, second = folders["1-step"], folders["2-step"]
        self.assertEqual(first.name, second.name)
        for name in ("stage_1_winners.jsonl", "stage_1_details.jsonl", "samples.json", "subagents.json"):
            self.assertEqual((first / name).read_bytes(), (second / name).read_bytes())
        self.assertFalse((first / "stage_2_details.jsonl").exists())
        self.assertEqual(self.read(first)["accuracy@1"], 1)
        self.assertEqual(self.read(second)["accuracy@1"], 0)
        self.assertEqual(self.read(first)["accuracy@3"], self.read(second)["accuracy@5"])
        first_config, second_config = self.read(first, "config.json"), self.read(second, "config.json")
        self.assertEqual(first_config["strategy"], "1-step")
        self.assertEqual(first_config["pipeline_strategy"], "2-step")
        self.assertEqual(first_config["paired_artifact_dir"], str(second.resolve()))
        self.assertEqual(second_config["paired_artifact_dir"], str(first.resolve()))
        self.assertEqual(first_config["system_prompt"], second_config["system_prompt"])
        for i, payload in enumerate(calls):
            self.assertEqual(payload["state"], {"query": f"q{i // 2}"})
            criteria = payload["questions"]["subagent"]["criteria"]
            self.assertEqual(len(criteria), 4 if i % 2 == 0 else 3)
            self.assertTrue(all(text.startswith("SHORT" if i % 2 == 0 else "FULL") for text in criteria.values()))
        self.assertNotIn("SECRET", json.dumps(calls))
        self.assertNotIn("expected_subagents", json.dumps(calls))
        winners = [json.loads(line) for line in (first / "stage_1_winners.jsonl").read_text().splitlines()]
        self.assertEqual(winners[0]["confidence"], 0.37)
        self.assertEqual(winners[0]["probability"], 1)
        self.assertEqual(self.read(first)["elapsed_seconds"], self.read(second)["stage_inference_seconds"]["1"])

    def test_one_step_only_has_one_directory_and_one_call_per_question(self):
        backend = Mock(side_effect=self.answer)
        code, folders = self.execute(backend, "--one-step-only")
        self.assertEqual(code, 0)
        self.assertEqual(set(folders), {"1-step"})
        self.assertEqual(backend.call_count, 2)
        self.assertTrue(self.read(folders["1-step"])["complete"])
        self.assertEqual(self.args("--strategy", "1-step").strategy, "1-step")

    def test_stage_two_error_does_not_damage_first_stage(self):
        calls = []
        def backend(payload):
            calls.append(payload)
            if len(calls) == 2:
                raise RuntimeError("stage two failed")
            return self.answer(payload)
        code, folders = self.execute(backend)
        self.assertEqual(code, 1)
        first, second = self.read(folders["1-step"]), self.read(folders["2-step"])
        self.assertEqual((first["accuracy@1"], first["n_errors"], first["status"]), (1, 0, "completed"))
        self.assertEqual((second["accuracy@1"], second["n_errors"]), (0.5, 1))
        self.assertEqual((folders["1-step"] / "errors.jsonl").read_text(), "")
        self.assertEqual(first["pipeline_status"], "completed_with_errors")

    def test_stage_one_error_skips_second_with_no_extra_call(self):
        calls = []
        def backend(payload):
            calls.append(payload)
            if len(calls) == 1:
                raise RuntimeError("stage one failed")
            return self.answer(payload)
        code, folders = self.execute(backend)
        self.assertEqual(code, 1)
        self.assertEqual(len(calls), 3)
        for folder in folders.values():
            self.assertEqual(self.read(folder)["n_errors"], 1)
            self.assertEqual(self.read(folder)["accuracy@1"], 0.5)
        skipped = json.loads((folders["2-step"] / "stage_2_winners.jsonl").read_text().splitlines()[0])
        self.assertEqual(skipped["status"], "skipped")

    def test_interrupt_during_stage_two_counts_each_view_separately(self):
        calls = []
        def backend(payload):
            calls.append(payload)
            if len(calls) == 2:
                raise KeyboardInterrupt
            return self.answer(payload)
        code, folders = self.execute(backend)
        self.assertEqual(code, 130)
        first, second = self.read(folders["1-step"]), self.read(folders["2-step"])
        self.assertEqual((first["n_samples"], first["n_samples_unprocessed"]), (1, 1))
        self.assertEqual((second["n_samples"], second["n_samples_unprocessed"]), (0, 2))
        self.assertFalse(first["complete"])
        self.assertIsNone(second["accuracy@1"])
        self.assertEqual(first["status"], "interrupted")

    def test_completed_first_view_survives_interrupt_in_last_second_call(self):
        def backend(payload):
            if len(payload["questions"]["subagent"]["criteria"]) == 3:
                raise KeyboardInterrupt
            return self.answer(payload)
        code, folders = self.execute(backend, "--limit", "1")
        self.assertEqual(code, 130)
        first = self.read(folders["1-step"])
        self.assertTrue(first["complete"])
        self.assertEqual(first["status"], "completed")
        self.assertEqual(first["pipeline_status"], "interrupted")
        self.assertIsNone(first["error"])

    def test_backend_initialization_failure_saves_both_empty_metrics(self):
        code, folders = self.execute(None, factory=Mock(side_effect=RuntimeError("initialization failed")))
        self.assertEqual(code, 1)
        for folder in folders.values():
            metrics = self.read(folder)
            self.assertEqual((metrics["status"], metrics["n_samples"]), ("failed", 0))
            self.assertIsNone(metrics["accuracy@1"])
            self.assertFalse(metrics["complete"])

    def test_dry_run_prepares_both_views_without_inference(self):
        factory = Mock(side_effect=AssertionError("inference forbidden"))
        code, folders = self.execute(None, "--dry-run", factory=factory)
        self.assertEqual(code, 0)
        factory.assert_not_called()
        self.assertEqual(set(folders), {"1-step", "2-step"})
        for strategy, folder in folders.items():
            metrics = self.read(folder)
            self.assertEqual(metrics["status"], "dry_run")
            self.assertEqual(metrics["evaluated_stage"], 1 if strategy == "1-step" else 2)
            self.assertIsNone(metrics["accuracy@1"])
        self.assertEqual((folders["1-step"] / "request_preview.json").read_bytes(),
                         (folders["2-step"] / "request_preview.json").read_bytes())

    def test_transport_defaults_and_dispatch(self):
        for name, endpoint, key in (("openrouter", OPENROUTER_ENDPOINT, "OPENROUTER_API_KEY"),
                                    ("hf", None, "HF_TOKEN")):
            args = self.args("--backend", name)
            self.assertEqual((args.endpoint, args.api_key_env), (endpoint, key))
            with patch("exp2.run_exp2.openrouter_backend") as remote, patch("exp2.run_exp2.hf_backend") as hf:
                backend_exp2(args)
                (remote if name == "openrouter" else hf).assert_called_once_with(args)
                (hf if name == "openrouter" else remote).assert_not_called()
        args = self.args("--backend", "hf", "--mode", "api", "--endpoint", "http://native.test", "--api-key-env", "CUSTOM")
        self.assertEqual((args.endpoint, args.api_key_env), ("http://native.test", "CUSTOM"))
        with self.assertRaisesRegex(ValueError, "explicit --endpoint"):
            self.args("--backend", "hf", "--mode", "api")

    def test_conflicting_strategy_flags_rejected(self):
        with contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit) as error:
            self.args("--strategy", "2-step", "--one-step-only")
        self.assertEqual(error.exception.code, 2)


if __name__ == "__main__":
    unittest.main()
