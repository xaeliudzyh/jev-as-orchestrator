"""Offline regression coverage for explicit model and retrospective prompt scopes."""
from contextlib import redirect_stdout
import importlib.util
import io
from pathlib import Path
import unittest


FIXTURE_PATH = Path(__file__).with_name("test_holdout_evaluator_exp2.py")
SPEC = importlib.util.spec_from_file_location("holdout_fixture_exp2", FIXTURE_PATH)
fixtures = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(fixtures)
evaluation = fixtures.evaluation


class ScenarioScopeTests(fixtures.HoldoutFixture):
    def make_retrospective_run(self, strategy="1-step", name="old-prompt"):
        prompt = self.root / "scenario100.txt"
        text = "Synthetic policy developed using the full 193-question benchmark."
        prompt.write_text(text + "\n", encoding="utf-8")
        folder = self.make_run(strategy, name=name)
        self.mutate_json(folder, "config.json", lambda config: config.update(
            system_prompt=text, system_prompt_sha256=evaluation.text_digest(text)))
        for stage in range(1, evaluation.STAGES[strategy] + 1):
            def update_requests(rows):
                for row in rows:
                    if row["request"] is not None:
                        row["request"]["questions"]["subagent"]["instructions"] = text
            self.mutate_jsonl(folder, f"stage_{stage}_details.jsonl", update_requests)
        return folder, prompt

    def test_default_rejects_old_prompt_without_modifying_train70_freeze(self):
        _, prompt = self.make_retrospective_run()
        freeze = self.split / "prompt_freeze.json"
        before = freeze.read_bytes()
        output = self.root / "unauthorized-report"
        with self.assertRaisesRegex(ValueError, "differs from prompt_freeze"):
            evaluation.evaluate(self.runs, self.split, prompt, output)
        self.assertFalse(output.exists())
        self.assertEqual(freeze.read_bytes(), before)
        selected, exclusions, _ = evaluation.collect(self.runs, self.frozen)
        self.assertEqual(selected, [])
        self.assertIn("prompt mismatch", exclusions[0]["reason"])

    def test_explicit_retrospective_accepts_old_prompt_and_labels_test_exposure(self):
        self.make_retrospective_run("1-step", "old-one")
        _, prompt = self.make_retrospective_run("2-step", "old-two")
        freeze = self.split / "prompt_freeze.json"
        before = freeze.read_bytes()
        output = self.root / "retrospective-report"
        report = evaluation.evaluate(self.runs, self.split, prompt, output,
            models=["typesafe/jev-1.14"], prompt_evaluation="retrospective")
        self.assertTrue(report["complete"])
        self.assertEqual(report["expected_results"], 2)
        self.assertEqual(report["prompt_evaluation"], "retrospective")
        self.assertEqual(report["evaluation_status"], "retrospective_test_exposed")
        self.assertIn("no clean holdout claim", report["evaluation_caveat"].lower())
        self.assertFalse(report["frozen"]["prompt_freeze_enforced"])
        self.assertEqual(report["frozen"]["prompt_freeze_sha256"], evaluation.digest(freeze))
        self.assertEqual(report["frozen"]["prompt_freeze_system_prompt_sha256"], self.frozen["prompt_hash"])
        chosen_hash = evaluation.text_digest(prompt.read_text().strip())
        self.assertNotEqual(chosen_hash, self.frozen["prompt_hash"])
        self.assertEqual(report["frozen"]["system_prompt_sha256"], chosen_hash)
        self.assertTrue(all(view["system_prompt_sha256"] == chosen_hash for view in report["selected"]))
        for view in report["selected"]:
            self.assertEqual({subset: values["n_samples"] for subset, values in view["metrics"].items()},
                             evaluation.EXPECTED_COUNTS)
        readme = (output / "README.md").read_text().lower()
        self.assertIn("retrospective", readme)
        self.assertIn("test-exposed", readme)
        self.assertIn("no clean holdout claim", readme)
        self.assertNotIn("this split measures a held-out portion", readme)
        self.assertEqual(freeze.read_bytes(), before)

    def test_retrospective_still_checks_frozen_files(self):
        _, prompt = self.make_retrospective_run()
        with (self.split / "catalog_enabled.jsonl").open("a") as handle:
            handle.write("\n")
        with self.assertRaisesRegex(ValueError, "Frozen file hash mismatch"):
            evaluation.load_frozen(self.split, prompt, prompt_evaluation="retrospective")

    def test_retrospective_still_requires_chosen_prompt_hash_and_request_text(self):
        wrong_hash, prompt = self.make_retrospective_run(name="wrong-hash")
        self.mutate_json(wrong_hash, "config.json", lambda config: config.update(system_prompt_sha256="0" * 64))
        wrong_request, _ = self.make_retrospective_run(name="wrong-request")
        self.mutate_jsonl(wrong_request, "stage_1_details.jsonl", lambda rows:
            rows[1]["request"]["questions"]["subagent"].update(instructions="Other policy"))
        frozen = evaluation.load_frozen(self.split, prompt, prompt_evaluation="retrospective")
        selected, exclusions, _ = evaluation.collect(self.runs, frozen, models=["typesafe/jev-1.14"])
        self.assertEqual(selected, [])
        self.assertEqual(len(exclusions), 2)
        self.assertTrue(any("prompt mismatch" in row["reason"] for row in exclusions))
        self.assertTrue(any("Request prompt mismatch" in row["reason"] for row in exclusions))

    def test_default_scope_stays_two_models_even_with_clef_artifacts(self):
        all_models = [*evaluation.MODELS, "Cloudflare/clef-flash", "Cloudflare/clef"]
        for index, model in enumerate(all_models):
            for strategy in evaluation.STAGES:
                self.make_run(strategy, name=f"model-{index}", model=model)
        report = evaluation.evaluate(self.runs, self.split, self.prompt, self.root / "default-report")
        self.assertEqual(report["scope"]["models"], evaluation.MODELS)
        self.assertEqual(len(evaluation.MODELS), 2)
        self.assertEqual(report["expected_results"], 4)
        self.assertEqual(len(report["selected"]), 4)
        self.assertTrue(report["complete"])
        self.assertTrue(report["frozen"]["prompt_freeze_enforced"])
        self.assertEqual(len(report["exclusions"]), 4)

    def test_explicit_clef_scope_selects_both_models_and_stages(self):
        models = ["Cloudflare/clef-flash", "Cloudflare/clef"]
        for index, model in enumerate(models):
            for strategy in evaluation.STAGES:
                self.make_run(strategy, name=f"clef-{index}", model=model)
        report = evaluation.evaluate(self.runs, self.split, self.prompt, self.root / "clef-report", models=models)
        self.assertEqual(set(report["scope"]["models"]), set(models))
        self.assertEqual(report["expected_results"], 4)
        self.assertEqual(len(report["selected"]), 4)
        self.assertTrue(report["complete"])
        self.assertEqual({view["model_label"] for view in report["selected"]}, {"Clef 9B", "Clef 27B"})

    def test_arbitrary_requested_model_uses_model_id_label_and_dynamic_missing(self):
        model = "local/future-router"
        self.make_run(model=model)
        report = evaluation.evaluate(self.runs, self.split, self.prompt, self.root / "custom-report", models=[model])
        self.assertEqual(report["scope"]["models"], {model: model})
        self.assertEqual(report["expected_results"], 2)
        self.assertEqual(report["selected"][0]["model_label"], model)
        self.assertEqual(report["missing"], [{"model": model, "stage": 2, "strategy": "2-step"}])

    def test_cli_accepts_models_and_retrospective_and_prints_dynamic_denominator(self):
        _, prompt = self.make_retrospective_run()
        output = self.root / "cli-report"
        args = ["--runs-root", str(self.runs), "--split-dir", str(self.split), "--prompt", str(prompt),
                "--output-dir", str(output), "--models", "typesafe/jev-1.14", "--prompt-evaluation", "retrospective"]
        stream = io.StringIO()
        with redirect_stdout(stream):
            self.assertEqual(evaluation.main(args), 0)
        self.assertIn("Selected 1/2 model/stage results", stream.getvalue())
        self.assertEqual(evaluation.read_json(output / "summary.json")["evaluation_status"], "retrospective_test_exposed")

    def test_invalid_explicit_scope_and_evaluation_mode_are_rejected(self):
        for models in ([], "typesafe/jev-1.14", [""], [" model "], ["m", "m"]):
            with self.subTest(models=models), self.assertRaises(ValueError):
                evaluation.collect(self.runs, self.frozen, models=models)
        with self.assertRaisesRegex(ValueError, "Unsupported prompt evaluation"):
            evaluation.load_frozen(self.split, self.prompt, prompt_evaluation="unmarked")


if __name__ == "__main__":
    unittest.main()
