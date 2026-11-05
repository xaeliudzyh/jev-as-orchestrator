from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import yaml

METADATA_FIELDS = ("name", "title", "description", "tags")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


@dataclass(frozen=True)
class Skill:
    name: str
    metadata: dict
    text: str
    path: str
    sha256: str
    short_text: str | None = None

    def description(self, full: bool, fields: list[str]) -> str:
        if full:
            return self.text
        if self.short_text is not None:
            return self.short_text
        return json.dumps({field: self.metadata[field] for field in fields}, ensure_ascii=False)


def load_skills(directory: Path) -> list[Skill]:
    skills = []
    for path in sorted(directory.rglob("*")):
        if path.suffix.lower() not in {".md", ".yaml", ".yml"}:
            continue
        raw = path.read_bytes()
        text = raw.decode("utf-8-sig")
        if path.suffix.lower() == ".md":
            lines = text.splitlines()
            if not lines or lines[0].strip() != "---":
                raise ValueError(f"Missing YAML front matter: {path}")
            end = next((i for i in range(1, len(lines)) if lines[i].strip() == "---"), None)
            if end is None:
                raise ValueError(f"Unclosed YAML front matter: {path}")
            metadata = yaml.safe_load("\n".join(lines[1:end]))
        else:
            metadata = yaml.safe_load(text)
        if not isinstance(metadata, dict):
            raise ValueError(f"Skill metadata must be an object: {path}")
        for field in METADATA_FIELDS[:3]:
            if not isinstance(metadata.get(field), str) or not metadata[field].strip():
                raise ValueError(f"Missing/non-string {field}: {path}")
        tags = metadata.get("tags", [])
        if not isinstance(tags, list) or not all(isinstance(tag, str) for tag in tags):
            raise ValueError(f"tags must be a list of strings: {path}")
        short = {field: metadata[field] for field in METADATA_FIELDS[:3]}
        short["tags"] = tags
        skills.append(Skill(metadata["name"], short, text, str(path.resolve()), sha256_bytes(raw)))
    names = [skill.name for skill in skills]
    if not 2 <= len(skills) <= 255:
        raise ValueError(f"Expected 2..255 skills, got {len(skills)}")
    if len(set(names)) != len(names):
        raise ValueError("Duplicate skill names")
    return sorted(skills, key=lambda skill: skill.name)


def load_corpus(path: Path) -> list[dict]:
    if path.suffix.lower() == ".jsonl":
        data = [json.loads(line) for line in path.read_text(encoding="utf-8-sig").splitlines() if line.strip()]
    else:
        data = json.loads(path.read_text(encoding="utf-8-sig"))
        if isinstance(data, dict):
            data = data["examples"]
    if not isinstance(data, list) or not data:
        raise ValueError("Corpus must be a nonempty list or an object with examples")
    result = []
    for i, row in enumerate(data):
        if not isinstance(row, dict) or not isinstance(row.get("question"), str) or not row["question"].strip():
            raise ValueError(f"Invalid question at corpus index {i}")
        labels = row.get("expected_skills")
        if not isinstance(labels, list) or not labels or not all(isinstance(x, str) and x.strip() for x in labels):
            raise ValueError(f"Invalid expected_skills at corpus index {i}")
        result.append({"sample_id": i, "question": row["question"], "expected_skills": list(dict.fromkeys(labels))})
    return result
