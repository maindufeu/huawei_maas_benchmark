"""Task loading and code extraction.

Tasks are YAML files (one or many per file) grouped by workflow class T1-T5
(Framework v1.0 §4). Each task declares how it should be graded via its
`evaluation` block, which selects an evaluator from the registry.
"""
from __future__ import annotations

import glob
import os
import re
from dataclasses import dataclass, field
from typing import Any

import yaml


CLASS_LABELS = {
    "T1": "Code generation",
    "T2": "Completion / refactoring",
    "T3": "Debugging",
    "T4": "System design",
    "T5": "Mixed workflow (agentic)",
}


@dataclass
class Task:
    id: str
    task_class: str                # T1..T5
    prompt: str
    evaluation: dict               # {"type": "unit_test", ...}
    title: str = ""
    language: str = "python"
    difficulty: str = "medium"
    system_prompt: str = ""
    timeout_seconds: int = 60
    max_tokens: int = 4096
    context_files: dict = field(default_factory=dict)  # filename -> contents
    meta: dict = field(default_factory=dict)

    @property
    def class_label(self) -> str:
        return CLASS_LABELS.get(self.task_class, self.task_class)


DEFAULT_SYSTEM_PROMPT = (
    "You are a senior software engineer. Produce correct, minimal, "
    "production-quality code. Follow the requested output format exactly. "
    "Do not include explanations unless explicitly requested."
)


def load_tasks(path: str) -> list[Task]:
    """Load tasks from a YAML file or a directory of YAML files."""
    files: list[str] = []
    if os.path.isdir(path):
        files = sorted(glob.glob(os.path.join(path, "**", "*.yaml"), recursive=True))
        files += sorted(glob.glob(os.path.join(path, "**", "*.yml"), recursive=True))
    else:
        files = [path]

    tasks: list[Task] = []
    for f in files:
        with open(f, "r", encoding="utf-8") as fh:
            docs = list(yaml.safe_load_all(fh))
        for doc in docs:
            if not doc:
                continue
            items = doc if isinstance(doc, list) else [doc]
            for raw in items:
                tasks.append(_parse_task(raw, source=f))
    return tasks


def _parse_task(raw: dict[str, Any], source: str = "") -> Task:
    try:
        return Task(
            id=raw["id"],
            task_class=raw.get("class", raw.get("task_class", "T1")),
            prompt=raw["prompt"],
            evaluation=raw.get("evaluation", {"type": "judge"}),
            title=raw.get("title", raw["id"]),
            language=raw.get("language", "python"),
            difficulty=raw.get("difficulty", "medium"),
            system_prompt=raw.get("system_prompt", "") or DEFAULT_SYSTEM_PROMPT,
            timeout_seconds=int(raw.get("timeout_seconds", 60)),
            max_tokens=int(raw.get("max_tokens", 4096)),
            context_files=raw.get("context_files", {}) or {},
            meta=raw.get("meta", {}) or {},
        )
    except KeyError as e:
        raise ValueError(f"Task in {source} missing required field {e}") from e


# ---- Code extraction -------------------------------------------------------

_FENCE_RE = re.compile(r"```[ \t]*([a-zA-Z0-9_+-]*)[ \t]*\r?\n(.*?)```", re.DOTALL)


def extract_code(text: str, language: str = "python") -> str:
    """Pull runnable code out of a model response.

    Preference order:
      1. fenced blocks tagged with the requested language,
      2. any fenced blocks (concatenated),
      3. the raw text (assume it is already code).
    """
    if not text:
        return ""
    blocks = _FENCE_RE.findall(text)
    if blocks:
        aliases = _lang_aliases(language)
        tagged = [body for tag, body in blocks if tag.lower() in aliases]
        chosen = tagged if tagged else [body for _, body in blocks]
        return "\n\n".join(b.strip() for b in chosen).strip()
    return text.strip()


def _lang_aliases(language: str) -> set[str]:
    l = language.lower()
    table = {
        "python": {"python", "py", "python3", ""},
        "typescript": {"typescript", "ts", "tsx", ""},
        "javascript": {"javascript", "js", "jsx", ""},
        "java": {"java", ""},
        "go": {"go", "golang", ""},
        "sql": {"sql", ""},
    }
    return table.get(l, {l, ""})
