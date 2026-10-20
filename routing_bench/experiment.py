from __future__ import annotations

import argparse
import json
import math
import platform
import random
import re
import subprocess
import sys
import time
from contextlib import ExitStack
from datetime import datetime
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from zoneinfo import ZoneInfo

from .data import METADATA_FIELDS, load_corpus, load_skills, sha256_bytes

ROOT = Path(__file__).resolve().parents[1]
QUESTION_ID = "skill"


def now():
    return datetime.now(ZoneInfo("Europe/Moscow"))


def dump(path: Path, value):
    temp = path.with_suffix(path.suffix + ".tmp")
    temp.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    temp.replace(path)


def append(handle, value):
    handle.write(json.dumps(value, ensure_ascii=False, allow_nan=False) + "\n")
    handle.flush()


def make_payload(model, prompt, sample, skills, full, fields):
    return {"model": model, "state": {"query": sample["question"]}, "questions": {
        QUESTION_ID: {"type": "choice", "instructions": prompt,
                      "criteria": {skill.name: skill.description(full, fields) for skill in skills}}}}


def rank_response(response: dict, names: list[str]):
    if response.get("truncated"):
        raise ValueError("Backend reported truncated input")
    answer = response["answers"][QUESTION_ID]
    if answer.get("type", "choice") != "choice":
        raise ValueError("Expected a choice answer")
    probabilities = answer["probabilities"]
    if not isinstance(probabilities, dict) or set(probabilities) != set(names):
        raise ValueError("Probabilities must cover exactly every supplied candidate")
    for name, p in probabilities.items():
        if isinstance(p, bool) or not isinstance(p, (float, int)) or not math.isfinite(p) or not 0 <= p <= 1:
            raise ValueError(f"Invalid probability for {name}: {p!r}")
    total = sum(probabilities.values())
    # Native APIs round probabilities. Preserve raw values and normalize only this drift.
    if total <= 0 or abs(total - 1) > 0.02:
        raise ValueError(f"Probability mass must be near 1, got {total}")
    reported = answer.get("choice")
    if reported is not None:
        if reported not in probabilities or probabilities[reported] < max(probabilities.values()):
            raise ValueError("Reported choice is not a maximum-probability candidate")
    # Honor the native choice when its probabilities tie after serialization;
    # break other ties by input order, which is recorded in every detail row.
    order = {name: i for i, name in enumerate(names)}
    ranked = sorted(names, key=lambda n: (-probabilities[n], n != reported, order[n]))
    return [{"skill": name, "probability": probabilities[name] / total} for name in ranked], total


def accuracy(rows):
    n = len(rows)
    hits = {f"accuracy@{k}": sum(bool(set(row.get("ranking", [])[:k]) & set(row["expected_skills"]))
                                 for row in rows) for k in (1, 3, 5)}
    return {"n_samples": n, "hits": hits, **{key: value / n if n else None for key, value in hits.items()}}


def common_parser(description):
    parser = argparse.ArgumentParser(description=description)
    parser.add_argument("--model", required=True, help="Model ID or local checkpoint directory")
    parser.add_argument("--strategy", required=True, choices=("1-step", "2-step", "1", "2"))
    parser.add_argument("--system-prompt", type=Path, default=ROOT / "prompts/routing.txt")
    parser.add_argument("--corpus", type=Path, default=ROOT / "data/corpus/corpus_find_skill.json")
    parser.add_argument("--skills-dir", type=Path, default=ROOT / "data/skills")
    parser.add_argument("--output-dir", type=Path, default=ROOT / "experiments")
    parser.add_argument("--metadata-fields", nargs="+", choices=METADATA_FIELDS, default=list(METADATA_FIELDS))
    parser.add_argument("--shortlist-k", type=int, choices=(3, 4, 5), default=5)
    parser.add_argument("--limit", type=int, help="First N samples, after optional shuffling")
    parser.add_argument("--offset", type=int, default=0)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--shuffle-samples", action="store_true")
    parser.add_argument("--shuffle-skills", action="store_true", help="Shuffle catalog once using --seed")
    parser.add_argument("--missing-labels", choices=("keep", "error"), default="keep")
    parser.add_argument("--timeout", type=float, default=120)
    parser.add_argument("--retries", type=int, default=3)
    parser.add_argument("--fail-fast", action="store_true")
    parser.add_argument("--dry-run", action="store_true", help="Validate data and write a request preview without inference")
    parser.add_argument("--env-file", type=Path, default=ROOT / ".env")
    return parser


def package_versions():
    result = {}
    for name in ("PyYAML", "requests", "torch", "transformers", "huggingface-hub", "frida-decisions", "decider-ai", "kev", "jeva-model"):
        try:
            result[name] = version(name)
        except PackageNotFoundError:
            pass
    return result


def run(args, factory) -> int:
    if args.limit is not None and args.limit <= 0 or args.offset < 0 or args.timeout <= 0 or args.retries < 0:
        raise ValueError("limit/timeout must be positive, offset/retries nonnegative")
    if not math.isfinite(args.timeout):
        raise ValueError("timeout must be finite")
    # Load credentials without overwriting explicitly exported environment variables.
    if args.env_file.is_file():
        from dotenv import load_dotenv
        load_dotenv(args.env_file, override=False)
    strategy = {"1": "1-step", "2": "2-step"}.get(args.strategy, args.strategy)
    prompt = args.system_prompt.read_text(encoding="utf-8").strip()
    if not prompt:
        raise ValueError("System prompt is empty")
    skills = load_skills(args.skills_dir)
    all_samples = load_corpus(args.corpus)
    rng = random.Random(args.seed)
    if args.shuffle_skills:
        rng.shuffle(skills)
    samples = list(all_samples)
    if args.shuffle_samples:
        rng.shuffle(samples)
    samples = samples[args.offset:args.offset + args.limit if args.limit is not None else None]
    if not samples:
        raise ValueError("Selected corpus slice is empty")
    skill_names = {skill.name for skill in skills}
    missing = sorted({label for row in samples for label in row["expected_skills"]} - skill_names)
    affected = [row["sample_id"] for row in samples if set(row["expected_skills"]) - skill_names]
    unreachable = [row["sample_id"] for row in samples if not set(row["expected_skills"]) & skill_names]
    if missing:
        print(f"Warning: unavailable labels {missing}; {len(unreachable)} samples have no reachable label", file=sys.stderr)
        if args.missing_labels == "error":
            raise ValueError("Corpus references missing skills; fix data or use --missing-labels keep")
    started = now()
    safe_name = re.sub(r"[^a-zA-Z0-9._-]+", "__", args.model).strip("._") or "model"
    folder = args.output_dir / strategy / f"{safe_name[:120]}_{started.strftime('%Y%m%dT%H%M%S.%f%z')}"
    folder.mkdir(parents=True, exist_ok=False)
    print(f"Experiment: {folder}", flush=True)
    try:
        git = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True, check=True).stdout.strip()
    except (OSError, subprocess.CalledProcessError):
        git = None
    config = {"schema_version": 1, "model": args.model, "strategy": strategy,
        "started_at": started.isoformat(), "timezone": "Europe/Moscow", "status": "prepared",
        "arguments": {key: str(value) if isinstance(value, Path) else value for key, value in vars(args).items()},
        "system_prompt": prompt, "system_prompt_placement": "questions.skill.instructions (native decision API has no chat system role)",
        "input_structure": {"corpus": {"examples": [{"question": "str", "expected_skills": ["skill_name"]}]},
            "stage_1": {"state": {"query": "question"}, "criteria": "name -> JSON(metadata_fields)"},
            "stage_2": {"state": {"query": "question"}, "criteria": "top-k name -> complete original file text"}},
        "metadata_fields": args.metadata_fields, "shortlist_k": args.shortlist_k,
        "candidate_order": [skill.name for skill in skills], "sample_ids": [row["sample_id"] for row in samples],
        "n_samples": len(samples), "n_corpus_samples": len(all_samples), "n_skills": len(skills),
        "corpus_sha256": sha256_bytes(args.corpus.read_bytes()),
        "missing_labels": missing, "samples_with_missing_labels": affected, "unreachable_sample_ids": unreachable,
        "metric_definition": "Per-sample hit@k: any expected skill in final top-k; failures count as misses; "
            "stage-2 ranking is conditional on shortlist; no stage-1 probabilities are mixed in; k is clipped to available candidates",
        "ties": "native choice first among equal probabilities, then input order",
        "python": platform.python_version(), "platform": platform.platform(), "packages": package_versions(), "git_commit": git,
        "source_sha256": {str(p.relative_to(ROOT)): sha256_bytes(p.read_bytes())
                          for p in sorted([*ROOT.glob("run_*.py"), *ROOT.glob("routing_bench/*.py")])}}
    dump(folder / "config.json", config)
    dump(folder / "skills.json", [dict(name=s.name, metadata=s.metadata, text=s.text, path=s.path, sha256=s.sha256) for s in skills])
    dump(folder / "samples.json", samples)
    if args.dry_run:
        dump(folder / "request_preview.json", make_payload(args.model, prompt, samples[0], skills, False, args.metadata_fields))
        config.update(status="dry_run", finished_at=now().isoformat())
        dump(folder / "config.json", config)
        dump(folder / "metrics.json", {"status": "dry_run", "n_samples_planned": len(samples),
            "accuracy@1": None, "accuracy@3": None, "accuracy@5": None})
        return 0
    final_rows, first_rows = [], []
    backend = None
    status, fatal = "running", None
    stages = 2 if strategy == "2-step" else 1
    try:
        config["status"] = status
        dump(folder / "config.json", config)
        backend = factory(args)
        config["backend"] = getattr(backend, "info", {})
        config["packages"] = package_versions()
        dump(folder / "config.json", config)
        with ExitStack() as stack:
            handles = {stage: {kind: stack.enter_context((folder / f"stage_{stage}_{kind}.jsonl").open("w", encoding="utf-8"))
                               for kind in ("winners", "details")} for stage in range(1, stages + 1)}
            errors = stack.enter_context((folder / "errors.jsonl").open("w", encoding="utf-8"))
            for sample in samples:
                choices, previous_error = skills, None
                for stage in range(1, stages + 1):
                    record = dict(sample, stage=stage, status="ok")
                    response = None
                    payload = None
                    clock_start = time.perf_counter()
                    try:
                        if previous_error:
                            raise ValueError("Skipped because stage 1 failed")
                        payload = make_payload(args.model, prompt, sample, choices, stage == 2, args.metadata_fields)
                        response = backend(payload)
                        ranking, mass = rank_response(response, [s.name for s in choices])
                        record.update(winner=ranking[0]["skill"], probability=ranking[0]["probability"],
                            ranking=[entry["skill"] for entry in ranking])
                        detail = dict(record, probabilities={r["skill"]: r["probability"] for r in ranking},
                            top_10=ranking[:10], raw_probability_sum=mass)
                    except Exception as error:
                        record.update(status="skipped" if previous_error else "error", winner=None, probability=None,
                                      ranking=[], error=f"{type(error).__name__}: {error}")
                        detail = dict(record, probabilities={}, top_10=[])
                        append(errors, record)
                        previous_error = record["error"]
                    detail.update(request=payload, response=response,
                                  candidate_order=[s.name for s in choices], elapsed_seconds=time.perf_counter() - clock_start)
                    append(handles[stage]["winners"], record)
                    append(handles[stage]["details"], detail)
                    if stage == 1:
                        first_rows.append(record)
                    if stage == stages:
                        final_rows.append(record)
                    if record["status"] == "ok" and stage == 1 and stages == 2:
                        by_name = {s.name: s for s in skills}
                        choices = [by_name[name] for name in record["ranking"][:args.shortlist_k]]
                print(f"[{len(final_rows)}/{len(samples)}] sample={sample['sample_id']} {record['status']} winner={record['winner']}", flush=True)
                if previous_error and args.fail_fast:
                    raise RuntimeError(previous_error)
        status = "completed_with_errors" if any(row["status"] != "ok" for row in final_rows) else "completed"
    except KeyboardInterrupt:
        status, fatal = "interrupted", "KeyboardInterrupt"
    except Exception as error:
        status, fatal = "failed", f"{type(error).__name__}: {error}"
    finally:
        if backend is not None and hasattr(backend, "close"):
            try:
                backend.close()
            except Exception as error:
                status, fatal = "failed", f"Backend cleanup failed: {error}"
        finished = now()
        config.update(status=status, finished_at=finished.isoformat(), error=fatal)
        dump(folder / "config.json", config)
        metrics = accuracy(final_rows)
        metrics.update(status=status, evaluated_stage=stages, n_samples_planned=len(samples),
            n_samples_unprocessed=len(samples) - len(final_rows), n_errors=sum(r["status"] != "ok" for r in final_rows),
            complete=len(final_rows) == len(samples), missing_labels=missing,
            n_unreachable_samples=len(unreachable), elapsed_seconds=(finished - started).total_seconds(), error=fatal)
        if stages == 2:
            metrics["shortlist_hit_rate"] = sum(bool(set(row["ranking"][:args.shortlist_k]) & set(row["expected_skills"]))
                                                for row in first_rows) / len(first_rows) if first_rows else None
        dump(folder / "metrics.json", metrics)
    print(json.dumps(metrics, ensure_ascii=False, indent=2))
    return 0 if status == "completed" else (130 if status == "interrupted" else 1)


def main(parser, factory):
    args = parser.parse_args()
    try:
        return run(args, factory)
    except (ValueError, OSError, KeyError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 2
