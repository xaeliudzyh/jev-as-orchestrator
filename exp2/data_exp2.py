"""Strict loaders for the subagent routing bucket and candidate catalog."""
from __future__ import annotations

import csv
import json
from pathlib import Path

from routing_bench.data import Skill, sha256_bytes

BUCKET_FIELDS = ("Вопрос в новой корзине", "skill_name", "SubAgent")
CORPUS_FIELDS = ("target_subagent", "target_subagent_description", "target_subagent_full_text", "enabled")


def read_rows(path: Path, fields: tuple[str, ...]) -> list[dict]:
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".csv":
        with path.open(encoding="utf-8-sig", newline="") as handle:
            reader = csv.DictReader(handle, delimiter=";")
            if reader.fieldnames != list(fields):
                raise ValueError(f"Expected CSV columns {fields}: {path}")
            rows = list(reader)
    elif path.suffix.lower() == ".jsonl":
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    else:
        raise ValueError("exp2 accepts .csv (semicolon) or .jsonl")
    if not rows:
        raise ValueError(f"Empty dataset: {path}")
    for index, row in enumerate(rows):
        if not isinstance(row, dict) or set(row) != set(fields):
            raise ValueError(f"Unexpected columns at {path}:{index + 1}")
        for field in fields:
            value = row[field]
            if field == "enabled":
                if path.suffix.lower() == ".csv" and value in {"0", "1"}:
                    row[field] = int(value)
                elif type(value) is not int or value not in (0, 1):
                    raise ValueError(f"enabled must be 0 or 1 at {path}:{index + 1}")
            elif not isinstance(value, str):
                raise ValueError(f"Non-string/malformed cell at {path}:{index + 1}")
    return rows


def load_subagents(path: Path, include_disabled: bool = False) -> list[Skill]:
    result = []
    for row in read_rows(path, CORPUS_FIELDS):
        name, description, full = (row[key] for key in CORPUS_FIELDS[:3])
        if not name.strip() or name != name.strip() or not description.strip() or not full.strip():
            raise ValueError("Subagent name, description and full text must be nonempty; names must be trimmed")
        raw = json.dumps(row, ensure_ascii=False, sort_keys=True).encode("utf-8")
        result.append(Skill(name, {"name": name, "description": description, "enabled": row["enabled"]}, full,
                            str(path.resolve()), sha256_bytes(raw), short_text=description))
    if not 2 <= len(result) <= 255:
        raise ValueError(f"Expected 2..255 subagents, got {len(result)}")
    if len({item.name for item in result}) != len(result):
        raise ValueError("Duplicate subagent names")
    if not include_disabled:
        result = [item for item in result if item.metadata["enabled"] == 1]
    if len(result) < 2:
        raise ValueError(f"Expected at least 2 eligible subagents, got {len(result)}")
    return sorted(result, key=lambda item: item.name)


def load_bucket(path: Path) -> list[dict]:
    result = []
    for index, row in enumerate(read_rows(path, BUCKET_FIELDS)):
        question, _, label = (row[key] for key in BUCKET_FIELDS)
        if not question.strip() or not label.strip() or label != label.strip():
            raise ValueError(f"Empty question or invalid SubAgent at bucket index {index}")
        # skill_name is provenance only. It never enters state or decision criteria.
        result.append({"sample_id": index, "question": question, "expected_subagents": [label],
                       "source_skill_name": row["skill_name"]})
    return result
