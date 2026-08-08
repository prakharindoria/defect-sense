"""Versioned prompt loading.

Prompts live in `*.md` next to this module and are read at runtime, never
inlined in Python (CLAUDE.md rule 8). Each carries YAML front matter with a
`version`, and that version is recorded on every `TraceSpan` — so when an
explanation looks wrong you can tell which prompt produced it, and prompt
iteration is demonstrable rather than asserted.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import yaml

PROMPT_DIR = Path(__file__).parent
_FRONT_MATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)


class PromptNotFoundError(FileNotFoundError):
    """A prompt was requested that does not exist on disk."""


@dataclass(frozen=True, slots=True)
class Prompt:
    name: str
    version: str
    tier: str
    body: str

    def render(self, **values: object) -> str:
        """Fill `{placeholder}` slots.

        Uses explicit replacement rather than str.format because the prompt body
        contains JSON examples full of literal braces, which format() would
        choke on.
        """
        out = self.body
        for key, value in values.items():
            out = out.replace("{" + key + "}", str(value))
        return out


@lru_cache(maxsize=32)
def load(name: str) -> Prompt:
    path = PROMPT_DIR / f"{name}.md"
    if not path.exists():
        raise PromptNotFoundError(f"no prompt '{name}' at {path}")

    raw = path.read_text(encoding="utf-8")
    meta: dict[str, object] = {}
    if match := _FRONT_MATTER.match(raw):
        meta = yaml.safe_load(match.group(1)) or {}
        raw = raw[match.end():]

    return Prompt(
        name=name,
        version=str(meta.get("version", "0.0.0")),
        tier=str(meta.get("tier", "fast")),
        body=raw.strip(),
    )
