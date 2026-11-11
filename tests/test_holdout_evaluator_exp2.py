"""Synthetic offline regression tests; no benchmark questions or model calls."""
import csv
import importlib.util
import json
from pathlib import Path
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "exp2/evaluate_holdout_exp2.py"
SPEC = importlib.util.spec_from_file_location("evaluate_holdout_exp2", SCRIPT)
evaluation = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(evaluation)


def write_json(path, value):
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def write_jsonl(path, rows):
    path.write_text("".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows), encoding="utf-8")


def saved_metrics(rows):
    # Independent fixture oracle: one gold label, no filtering of the denominator.
    hits = {f"accuracy@{k}": sum(r["status"] == "ok" and r["expected_subagents"][0] in r["ranking"][:k]
                               for r in rows) for k in (1, 3, 5)}
    errors = sum(r["status"] != "ok" for r in rows)
    return {"n_samples": len(rows), "hits": hits,
            **{key: value / len(rows) for key, value in hits.items()},
            "n_errors": errors, "status": "completed_with_errors" if errors else "completed",
            "complete": True, "n_samples_planned": 193, "n_samples_unprocessed": 0}


class HoldoutFixture(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.split = self.root / "split"
        self.split.mkdir()
        self.runs = self.root / "runs"
        self.runs.mkdir()
        self.prompt = self.root / "prompt.txt"
        self.prompt.write_text("\nSynthetic train-only policy.\n", encoding="utf-8")
        self.prompt_text = self.prompt.read_text().strip()
        self.names = [f"agent{i:02}" for i in range(17)]
        self.catalog = [{"target_subagent": name, "target_subagent_description": f"Description {name}",
                         "target_subagent_full_text": f"Full text {name}", "enabled": 1} for name in self.names]
        self.rows = [{evaluation.BUCKET_FIELDS[0]: f"Synthetic question {i}", "skill_name": f"source-{i}",
                      "SubAgent": self.names[i % 12]} for i in range(193)]
        self.ids = {"train": list(range(135)), "test": list(range(135, 193))}
        for name, ids in {"full": list(range(193)), **self.ids}.items():
            with (self.split / f"bucket_{name}.csv").open("w", encoding="utf-8", newline="") as handle:
                writer = csv.DictWriter(handle, fieldnames=evaluation.BUCKET_FIELDS, delimiter=";", lineterminator="\n")
                writer.writeheader()
                writer.writerows(self.rows[i] for i in ids)
        write_jsonl(self.split / "catalog_enabled.jsonl", self.catalog)
        self.manifest = {"schema_version": 1, "n_full": 193, "n_train": 135, "n_test": 58,
            "n_candidates": 17, "n_observed_labels": 12,
            "source_corpus_sha256": evaluation.text_digest("unchanged synthetic source corpus"),
            "source_bucket_sha256": evaluation.digest(self.split / "bucket_full.csv"),
            "train_original_ids": self.ids["train"], "test_original_ids": self.ids["test"],
            "assignment": [{"original_sample_id": i, "split": subset, "split_sample_id": j,
                "question_sha256": evaluation.text_digest(self.rows[i][evaluation.BUCKET_FIELDS[0]]),
                "target_subagent": self.rows[i]["SubAgent"]}
                for subset, ids in self.ids.items() for j, i in enumerate(ids)],
            "label_counts": {name: {subset: sum(self.rows[i]["SubAgent"] == name for i in ids)
                for subset, ids in {"full": list(range(193)), **self.ids}.items()} for name in self.names[:12]},
            "files_sha256": {p.name: evaluation.digest(p) for p in self.split.iterdir()}}
        write_json(self.split / "manifest.json", self.manifest)
        write_json(self.split / "prompt_freeze.json", {
            "system_prompt_sha256": evaluation.text_digest(self.prompt_text), "raw_file_sha256": evaluation.digest(self.prompt)})
        self.frozen = evaluation.load_frozen(self.split, self.prompt)

    def make_run(self, strategy="1-step", name="run", model="typesafe/jev-1.14",
                 started_at="2026-11-11T10:00:00+03:00", shortlist_k=5):
        folder = self.runs / strategy / name
        folder.mkdir(parents=True)
        stage = evaluation.STAGES[strategy]
        config = {"experiment": "exp2", "question_id": "subagent", "model": model,
            "strategy": strategy, "evaluated_stage": stage, "run_id": name, "started_at": started_at,
            "status": "completed_with_errors", "n_samples": 193, "n_corpus_samples": 193,
            "bucket_sha256": self.frozen["bucket_hash"], "corpus_sha256": self.frozen["corpus_hash"],
            "n_subagents": 17, "include_disabled": False, "candidate_order": self.names,
            "system_prompt_sha256": self.frozen["prompt_hash"], "system_prompt": self.prompt_text,
            "sample_ids": list(range(193)), "shortlist_k": shortlist_k, "missing_labels": [],
            "unreachable_sample_ids": [], "excluded_disabled_sample_ids": [],
            "artifact_origin": "shared_stage_1" if stage == 1 else "two_stage_pipeline"}
        samples = [{"sample_id": i, "question": r[evaluation.BUCKET_FIELDS[0]],
                    "expected_subagents": [r["SubAgent"]], "source_skill_name": r["skill_name"]}
                   for i, r in enumerate(self.rows)]
        write_json(folder / "samples.json", samples)
        write_json(folder / "subagents.json", [{"name": r["target_subagent"], "metadata": {"enabled": 1},
            "short_text": r["target_subagent_description"], "text": r["target_subagent_full_text"]} for r in self.catalog])
        stages = {}
        for current_stage in range(1, stage + 1):
            winners, details = [], []
            for i, sample in enumerate(samples):
                previous_failed = current_stage == 2 and i in {0, 135}
                names = (stages[1][i]["ranking"][:shortlist_k]
                         if current_stage == 2 and not previous_failed else self.names)
                if current_stage == 1:
                    gold = sample["expected_subagents"][0]
                    ranking = [n for n in names if n != gold]
                    ranking.insert(i % 6, gold)
                else:
                    ranking = list(reversed(names))
                status = ("skipped" if previous_failed else "error" if i in {0, 135} or
                          current_stage == 2 and i == 136 else "ok")
                raw = {name: (len(ranking) - ranking.index(name)) / (len(ranking) * (len(ranking) + 1) / 2)
                       for name in names} if status == "ok" else {}
                winner = ranking[0] if status == "ok" else None
                record = dict(sample, stage=current_stage, status=status,
                    ranking=ranking if status == "ok" else [], winner=winner,
                    probability=raw[winner] / sum(raw.values()) if winner is not None else None, confidence=None)
                field = "target_subagent_description" if current_stage == 1 else "target_subagent_full_text"
                texts = {r["target_subagent"]: r[field] for r in self.catalog}
                request = {"model": model, "state": {"query": sample["question"]}, "questions": {"subagent": {
                    "type": "choice", "instructions": self.prompt_text,
                    "criteria": {n: texts[n] for n in names}}}}
                response = {"answers": {"subagent": {"type": "choice", "choice": winner,
                    "probabilities": raw}}} if status == "ok" else None
                detail = dict(record, candidate_order=names, request=None if previous_failed else request, response=response)
                winners.append(record)
                details.append(detail)
            stages[current_stage] = winners
            write_jsonl(folder / f"stage_{current_stage}_winners.jsonl", winners)
            write_jsonl(folder / f"stage_{current_stage}_details.jsonl", details)
        metrics = saved_metrics(stages[stage])
        metrics["evaluated_stage"] = stage
        if stage == 2:
            metrics["stage_1_metrics"] = {k: v for k, v in saved_metrics(stages[1]).items()
                if k in {"n_samples", "hits", "accuracy@1", "accuracy@3", "accuracy@5"}}
            metrics["shortlist_hit_rate"] = sum(r["status"] == "ok" and r["expected_subagents"][0] in
                r["ranking"][:shortlist_k] for r in stages[1]) / 193
        write_json(folder / "config.json", config)
        write_json(folder / "metrics.json", metrics)
        return folder

    def load_run(self, folder):
        return evaluation.load_run(folder, evaluation.read_json(folder / "config.json"),
            evaluation.read_json(folder / "metrics.json"), self.frozen, folder.parent.name)[0]

    def mutate_json(self, folder, filename, change):
        path = folder / filename
        value = evaluation.read_json(path)
        change(value)
        write_json(path, value)

    def mutate_jsonl(self, folder, filename, change):
        path = folder / filename
        rows = evaluation.read_jsonl(path)
        change(rows)
        write_jsonl(path, rows)


class MetricTests(HoldoutFixture):
    def test_exact_full_train_test_counts_and_errors_from_same_responses(self):
        self.make_run("1-step")
        self.make_run("2-step")
        report = evaluation.evaluate(self.runs, self.split, self.prompt, self.root / "report")
        self.assertEqual(len(report["selected"]), 2)
        self.assertFalse(report["complete"])
        self.assertEqual(len(report["missing"]), 2)
        expected = {1: {"full": (32, 96, 159, 2), "train": (22, 68, 112, 1), "test": (10, 28, 47, 1)},
                    2: {"full": (31, 94, 158, 3), "train": (22, 67, 112, 1), "test": (9, 27, 46, 2)}}
        for view in report["selected"]:
            for subset, counts in expected[view["stage"]].items():
                m = view["metrics"][subset]
                self.assertEqual(tuple(m["hits"][f"accuracy@{k}"] for k in (1, 3, 5)) + (m["n_errors"],), counts)
                self.assertEqual(m["accuracy@1"], counts[0] / evaluation.EXPECTED_COUNTS[subset])
                self.assertEqual(sum(c["n_samples"] for c in m["per_class"].values()), m["n_samples"])
                self.assertEqual(m["macro_accuracy@1"], sum(c["accuracy@1"] for c in m["per_class"].values()) / 12)
            for k in (1, 3, 5):
                self.assertEqual(view["metrics"]["full"]["hits"][f"accuracy@{k}"],
                    sum(view["metrics"][s]["hits"][f"accuracy@{k}"] for s in ("train", "test")))
            for name, sha in view["source_files_sha256"].items():
                self.assertEqual(evaluation.digest(Path(view["source_folder"]) / name), sha)
        self.assertTrue((self.root / "report/summary.json").is_file())
        readme = (self.root / "report/README.md").read_text()
        self.assertIn("10/58", readme)
        self.assertIn("Errors", readme)

    def test_shortlist_three_clips_at_three_without_restoring_missing_gold(self):
        view = self.load_run(self.make_run("2-step", shortlist_k=3))
        self.assertEqual(view["metrics"]["full"]["hits"], {"accuracy@1": 32, "accuracy@3": 96, "accuracy@5": 96})
        self.assertEqual(view["metrics"]["test"]["hits"], {"accuracy@1": 9, "accuracy@3": 28, "accuracy@5": 28})

    def test_non_ok_records_are_misses_even_with_stale_rankings(self):
        rows = [{"status": "error", "ranking": ["a"], "expected_subagents": ["a"]},
                {"status": "skipped", "ranking": ["a"], "expected_subagents": ["a"]},
                {"status": "ok", "ranking": ["a"], "expected_subagents": ["a"]}]
        metrics = evaluation.aggregate(rows)
        self.assertEqual(metrics["n_samples"], 3)
        self.assertEqual(metrics["n_errors"], 2)
        self.assertEqual(metrics["accuracy@5"], 1 / 3)

    def test_current_prompt_matches_runner_strip_and_optional_freeze(self):
        self.assertEqual(self.frozen["prompt_text"], "Synthetic train-only policy.")
        (self.split / "prompt_freeze.json").unlink()
        self.assertIsNone(evaluation.load_frozen(self.split, self.prompt)["provenance"]["prompt_freeze_path"])

    def test_shuffled_exports_are_split_by_original_ids_not_row_positions(self):
        folder = self.make_run("2-step")
        self.mutate_json(folder, "config.json", lambda c: c["sample_ids"].reverse())
        self.mutate_json(folder, "samples.json", lambda rows: rows.reverse())
        for stage in (1, 2):
            for kind in ("winners", "details"):
                self.mutate_jsonl(folder, f"stage_{stage}_{kind}.jsonl", lambda rows: rows.reverse())
        view = self.load_run(folder)
        self.assertEqual(view["metrics"]["train"]["hits"]["accuracy@1"], 22)
        self.assertEqual(view["metrics"]["test"]["hits"]["accuracy@1"], 9)
        self.assertEqual(view["metrics"]["test"]["n_errors"], 2)

    def test_native_ties_honor_reported_choice_then_input_order(self):
        names = ["a", "b", "c"]
        answer = {"type": "choice", "choice": "c", "probabilities": dict.fromkeys(names, 1 / 3)}
        detail = {"response": {"answers": {"subagent": answer}}, "ranking": ["c", "a", "b"], "probability": 1 / 3}
        evaluation.validate_native_ranking(detail, names, "tie")
        detail["ranking"] = ["a", "b", "c"]
        with self.assertRaisesRegex(ValueError, "differs from native"):
            evaluation.validate_native_ranking(detail, names, "tie")
        del answer["choice"]
        evaluation.validate_native_ranking(detail, names, "tie without reported choice")


class FrozenIntegrityTests(HoldoutFixture):
    def test_tampered_frozen_file_is_rejected(self):
        with (self.split / "bucket_full.csv").open("a") as handle:
            handle.write("\n")
        with self.assertRaisesRegex(ValueError, "Frozen file hash mismatch"):
            evaluation.load_frozen(self.split, self.prompt)

    def test_assignment_question_or_gold_is_checked_against_full_bucket(self):
        self.manifest["assignment"][0]["question_sha256"] = "0" * 64
        write_json(self.split / "manifest.json", self.manifest)
        with self.assertRaisesRegex(ValueError, "Assignment question/gold mismatch"):
            evaluation.load_frozen(self.split, self.prompt)

    def test_subset_rows_must_match_assignment_even_if_file_hash_is_updated(self):
        path = self.split / "bucket_test.csv"
        rows = evaluation.read_rows(path, evaluation.BUCKET_FIELDS)
        rows[0], rows[1] = rows[1], rows[0]
        with path.open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=evaluation.BUCKET_FIELDS, delimiter=";")
            writer.writeheader()
            writer.writerows(rows)
        self.manifest["files_sha256"][path.name] = evaluation.digest(path)
        write_json(self.split / "manifest.json", self.manifest)
        with self.assertRaisesRegex(ValueError, "test rows differ"):
            evaluation.load_frozen(self.split, self.prompt)

    def test_prompt_freeze_hash_is_binding(self):
        self.prompt.write_text("Changed prompt")
        with self.assertRaisesRegex(ValueError, "differs from prompt_freeze"):
            evaluation.load_frozen(self.split, self.prompt)

    def test_optional_raw_hash_detects_surrounding_whitespace_changes(self):
        self.prompt.write_text(self.prompt_text + "\n\n")
        with self.assertRaisesRegex(ValueError, "Raw prompt file differs"):
            evaluation.load_frozen(self.split, self.prompt)


class RunIntegrityTests(HoldoutFixture):
    def test_wrong_frozen_inputs_and_partial_runs_are_rejected(self):
        mutations = [("bucket_sha256", "wrong", "bucket"), ("corpus_sha256", "wrong", "corpus"),
            ("system_prompt_sha256", "wrong", "prompt"), ("system_prompt", "old policy", "prompt"),
            ("n_subagents", 21, "17 enabled"), ("n_samples", 135, "full 193"),
            ("candidate_order", self.names[:-1], "candidate set")]
        for i, (field, value, reason) in enumerate(mutations):
            with self.subTest(field=field):
                folder = self.make_run(name=f"bad-{i}")
                self.mutate_json(folder, "config.json", lambda c: c.update({field: value}))
                with self.assertRaisesRegex(ValueError, reason):
                    self.load_run(folder)

    def test_saved_correct_counts_rates_and_errors_must_all_agree(self):
        changes = [(lambda m: m["hits"].update({"accuracy@1": 33}), "correct-count"),
                   (lambda m: m.update({"accuracy@3": .99}), "rate mismatch"),
                   (lambda m: m.update({"n_errors": 0}), "error count")]
        for i, (change, reason) in enumerate(changes):
            with self.subTest(reason=reason):
                folder = self.make_run(name=f"metric-{i}")
                self.mutate_json(folder, "metrics.json", change)
                with self.assertRaisesRegex(ValueError, reason):
                    self.load_run(folder)

    def test_original_question_gold_and_unique_ids_are_required(self):
        changes = [(lambda rows: rows[1].update(question="Other query"), "Original question"),
                   (lambda rows: rows[1].update(expected_subagents=["agent16"]), "Gold mismatch"),
                   (lambda rows: rows[1].update(sample_id=0), "duplicate sample IDs")]
        for i, (change, reason) in enumerate(changes):
            with self.subTest(reason=reason):
                folder = self.make_run(name=f"sample-{i}")
                self.mutate_jsonl(folder, "stage_1_winners.jsonl", change)
                with self.assertRaisesRegex(ValueError, reason):
                    self.load_run(folder)

    def test_candidate_must_be_in_stage_shortlist(self):
        folder = self.make_run("2-step")
        for kind in ("winners", "details"):
            self.mutate_jsonl(folder, f"stage_2_{kind}.jsonl", lambda rows: rows[1]["ranking"].__setitem__(-1, "agent16"))
        with self.assertRaisesRegex(ValueError, "Ranking candidate inclusion"):
            self.load_run(folder)

    def test_saved_request_prompt_and_catalog_text_are_checked(self):
        changes = [(lambda rows: rows[1]["request"]["questions"]["subagent"].update(instructions="Old prompt"), "Request prompt"),
            (lambda rows: rows[1]["request"]["questions"]["subagent"]["criteria"].update(agent00="Wrong text"), "candidate text")]
        for i, (change, reason) in enumerate(changes):
            with self.subTest(reason=reason):
                folder = self.make_run(name=f"request-{i}")
                self.mutate_jsonl(folder, "stage_1_details.jsonl", change)
                with self.assertRaisesRegex(ValueError, reason):
                    self.load_run(folder)

    def test_native_response_ranking_is_checked(self):
        folder = self.make_run()
        self.mutate_jsonl(folder, "stage_1_details.jsonl", lambda rows:
            rows[1]["response"]["answers"]["subagent"].update(choice=rows[1]["ranking"][-1]))
        with self.assertRaisesRegex(ValueError, "Invalid native choice"):
            self.load_run(folder)

    def test_error_and_skipped_records_cannot_look_like_successes(self):
        folder = self.make_run("2-step")
        for kind in ("winners", "details"):
            self.mutate_jsonl(folder, f"stage_2_{kind}.jsonl", lambda rows: rows[0].update(status="error"))
        with self.assertRaisesRegex(ValueError, "failure propagation"):
            self.load_run(folder)

    def test_embedded_first_stage_metrics_are_checked_for_stage_two(self):
        folder = self.make_run("2-step")
        self.mutate_json(folder, "metrics.json", lambda m: m["stage_1_metrics"]["hits"].update({"accuracy@5": 193}))
        with self.assertRaisesRegex(ValueError, "embedded stage_1_metrics"):
            self.load_run(folder)


class SelectionAndOutputTests(HoldoutFixture):
    def test_latest_complete_eligible_run_wins_with_exclusions(self):
        self.make_run(name="old", started_at="2026-11-09T00:00:00+03:00")
        newest = self.make_run(name="new", started_at="2026-11-10T00:00:00+03:00")
        partial = self.make_run(name="partial", started_at="2026-11-11T00:00:00+03:00")
        self.mutate_json(partial, "metrics.json", lambda m: m.update(complete=False))
        old_prompt = self.make_run(name="other-prompt", started_at="2026-11-12T00:00:00+03:00")
        self.mutate_json(old_prompt, "config.json", lambda c: c.update(system_prompt_sha256="previous"))
        views, exclusions, missing = evaluation.collect(self.runs, self.frozen)
        self.assertEqual([v["source_folder"] for v in views], [str(newest.resolve())])
        self.assertEqual(len(exclusions), 3)
        self.assertEqual(len(missing), 3)
        self.assertTrue(any("Superseded" in e["reason"] for e in exclusions))
        self.assertTrue(any("not complete" in e["reason"] for e in exclusions))
        self.assertTrue(any("prompt mismatch" in e["reason"] for e in exclusions))

    def test_latest_run_compares_timezone_aware_instants(self):
        self.make_run(name="later-local-hour", started_at="2026-11-11T10:00:00+03:00")
        latest = self.make_run(name="later-utc-instant", started_at="2026-11-11T08:00:00+00:00")
        views, _, _ = evaluation.collect(self.runs, self.frozen)
        self.assertEqual(views[0]["source_folder"], str(latest.resolve()))

    def test_stage_one_copy_inside_two_step_is_not_selected_twice(self):
        self.make_run("2-step")
        views, _, missing = evaluation.collect(self.runs, self.frozen)
        self.assertEqual([v["stage"] for v in views], [2])
        self.assertIn({"model": "typesafe/jev-1.14", "stage": 1, "strategy": "1-step"}, missing)

    def test_both_models_and_both_stages_produce_exactly_four_results(self):
        for i, model in enumerate(evaluation.MODELS):
            for strategy in evaluation.STAGES:
                self.make_run(strategy, name=f"model-{i}", model=model)
        summary = evaluation.evaluate(self.runs, self.split, self.prompt, self.root / "all")
        self.assertTrue(summary["complete"])
        self.assertEqual(summary["missing"], [])
        self.assertEqual(len(summary["selected"]), 4)

    def test_existing_output_is_refused_without_overwrite(self):
        output = self.root / "report"
        output.mkdir()
        sentinel = output / "summary.json"
        sentinel.write_text("original")
        with self.assertRaisesRegex(ValueError, "must be new"):
            evaluation.evaluate(self.runs, self.split, self.prompt, output)
        self.assertEqual(sentinel.read_text(), "original")

    def test_cli_succeeds_for_one_available_stage_and_reports_missing(self):
        self.make_run()
        args = ["--runs-root", str(self.runs), "--split-dir", str(self.split), "--prompt", str(self.prompt),
                "--output-dir", str(self.root / "partial-report")]
        self.assertEqual(evaluation.main(args), 0)
        summary = evaluation.read_json(self.root / "partial-report/summary.json")
        self.assertEqual(len(summary["missing"]), 3)
        self.assertFalse(summary["complete"])

    def test_no_eligible_runs_is_nonzero_and_records_malformed_exclusion(self):
        bad = self.runs / "1-step/bad"
        bad.mkdir(parents=True)
        (bad / "config.json").write_text("{")
        args = ["--runs-root", str(self.runs), "--split-dir", str(self.split), "--prompt", str(self.prompt),
                "--output-dir", str(self.root / "empty-report")]
        self.assertEqual(evaluation.main(args), 1)
        summary = evaluation.read_json(self.root / "empty-report/summary.json")
        self.assertEqual(summary["selected"], [])
        self.assertEqual(len(summary["missing"]), 4)
        self.assertIn("JSONDecodeError", summary["exclusions"][0]["reason"])

    def test_malformed_frozen_input_is_clean_cli_error_without_output(self):
        write_json(self.split / "manifest.json", [])
        output = self.root / "invalid-report"
        args = ["--runs-root", str(self.runs), "--split-dir", str(self.split), "--prompt", str(self.prompt),
                "--output-dir", str(output)]
        self.assertEqual(evaluation.main(args), 2)
        self.assertFalse(output.exists())


if __name__ == "__main__":
    unittest.main()
