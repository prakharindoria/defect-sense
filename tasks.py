#!/usr/bin/env python
"""FORGE task runner.

`make` is not available on every build machine (it is absent on the Windows
laptop this repo was bootstrapped on), so this module -- not the Makefile -- is
the real implementation. The Makefile is a thin delegating wrapper so that
`make demo` and `python tasks.py demo` are the same thing.

    python tasks.py <target> [args...]
    python tasks.py help

Targets that a build phase has not yet delivered report that fact with the
owning phase and workstream. They do not pretend to succeed (CLAUDE.md rule 3)
and they do not fail silently (rule 4).
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

ROOT = Path(__file__).resolve().parent
SRC = ROOT / "src"
WEB = ROOT / "apps" / "web"
ARTIFACTS = ROOT / ".forge"

# Targets not yet delivered, mapped to the phase and workstream that owns them.
# Kept here rather than scattered as stubs so the build status is greppable.
PENDING: dict[str, str] = {
    "seed": "v1.1 (pack loader + MES simulator seeding)",
    "eval": "v1.7 (holdout golden set + baseline rows)",
}

_TARGETS: dict[str, Callable[[list[str]], int]] = {}


def target(fn: Callable[[list[str]], int]) -> Callable[[list[str]], int]:
    _TARGETS[fn.__name__.rstrip("_").replace("_", "-")] = fn
    return fn


def run(cmd: list[str], *, cwd: Path | None = None, check: bool = True) -> int:
    """Run a subprocess, streaming output. Returns the exit code."""
    printable = " ".join(cmd)
    print(f"\n\x1b[36m$ {printable}\x1b[0m", flush=True)
    proc = subprocess.run(cmd, cwd=cwd or ROOT)  # noqa: S603
    if check and proc.returncode != 0:
        print(f"\x1b[31mfailed ({proc.returncode}): {printable}\x1b[0m", file=sys.stderr)
    return proc.returncode


def py() -> list[str]:
    return [sys.executable]


def _pending(name: str) -> int:
    print(
        f"\x1b[33m'{name}' is not built yet.\x1b[0m\n"
        f"  Owner: {PENDING[name]}\n"
        f"  This target exists so the interface is stable; it reports status "
        f"rather than pretending to run.",
        file=sys.stderr,
    )
    return 2


# ---------------------------------------------------------------------------
# Environment
# ---------------------------------------------------------------------------
@target
def install(argv: list[str]) -> int:
    """Install Python dependencies. `install ai` adds the heavy vision/RAG group."""
    extras = "dev"
    if argv and argv[0] in {"ai", "server", "all"}:
        extras = {"ai": "dev,ai", "server": "dev,server", "all": "dev,ai,server"}[argv[0]]
    return run([*py(), "-m", "pip", "install", "-e", f".[{extras}]"])


@target
def doctor(argv: list[str]) -> int:
    """Report what this machine can and cannot run. Run this first on a new box."""
    from forge.ops.doctor import report  # noqa: PLC0415

    return report()


@target
def probe(argv: list[str]) -> int:
    """Probe every configured LLM endpoint and print the capability matrix."""
    from forge.infrastructure.llm.probe import main  # noqa: PLC0415

    return main(argv)


# ---------------------------------------------------------------------------
# Quality gates
# ---------------------------------------------------------------------------
@target
def lint(argv: list[str]) -> int:
    rc = run([*py(), "-m", "ruff", "check", "."], check=False)
    rc |= run([*py(), "-m", "ruff", "format", "--check", "."], check=False)
    return rc


@target
def fmt(argv: list[str]) -> int:
    rc = run([*py(), "-m", "ruff", "format", "."], check=False)
    rc |= run([*py(), "-m", "ruff", "check", "--fix", "."], check=False)
    return rc


@target
def types(argv: list[str]) -> int:
    return run([*py(), "-m", "mypy", "src", "apps"], check=False)


@target
def test(argv: list[str]) -> int:
    """Run the test suite. Excludes network-hitting tests unless `test live`."""
    args = argv or ["-m", "not live"]
    return run([*py(), "-m", "pytest", *args], check=False)


@target
def arch(argv: list[str]) -> int:
    """Run only the executable architecture rules (import graph + pack isolation)."""
    return run([*py(), "-m", "pytest", "tests/architecture", "-v"], check=False)


@target
def verify(argv: list[str]) -> int:
    """Everything a merge to main must pass."""
    rc = lint([]) | types([]) | test([])
    print("\n\x1b[32mverify passed\x1b[0m" if rc == 0 else "\n\x1b[31mverify FAILED\x1b[0m")
    return rc


# ---------------------------------------------------------------------------
# Runtime
# ---------------------------------------------------------------------------
@target
def api(argv: list[str]) -> int:
    """Run the FastAPI app with reload."""
    port = os.environ.get("FORGE_API_PORT", "8000")
    # 0.0.0.0 is deliberate: the demo is driven from a second machine and from
    # phones on the same LAN. This is a development runner, never the production
    # entrypoint -- the container binds behind a reverse proxy.
    host = os.environ.get("FORGE_API_HOST", "0.0.0.0")  # noqa: S104
    return run(
        [*py(), "-m", "uvicorn", "apps.api.main:app", "--reload", "--host", host, "--port", port],
        check=False,
    )


@target
def web(argv: list[str]) -> int:
    """Run the React dev server on :5173 (proxies /api and /ws to :8000)."""
    npm = shutil.which("npm")
    if npm is None:
        print("npm not found on PATH", file=sys.stderr)
        return 1
    if not (WEB / "node_modules").exists():
        print("installing web dependencies (first run only)...")
        if run([npm, "install"], cwd=WEB, check=False) != 0:
            return 1
    return run([npm, "run", "dev"], cwd=WEB, check=False)


@target
def seed(argv: list[str]) -> int:
    return _pending("seed")


@target
def eval_(argv: list[str]) -> int:
    return _pending("eval")


@target
def demo(argv: list[str]) -> int:
    """Boot the full demo stack. Clean machine to demo-ready, zero manual steps."""
    print("Demo boot sequence:")
    print("  1. doctor      -- confirm the machine can run it")
    print("  2. seed        -- generators + MES simulator + memory bank")
    print("  3. probe       -- LLM capability matrix")
    print("  4. api + web   -- serve")
    rc = doctor([])
    if rc != 0:
        print("\n\x1b[31mdoctor reported a blocker; fix it before demoing.\x1b[0m")
        return rc
    return _pending("seed")


@target
def reset(argv: list[str]) -> int:
    """Delete all generated state. Everything removed here is reproducible from seed."""
    removed = []
    for path in (ARTIFACTS, ROOT / "data" / "generated", ROOT / ".pytest_cache"):
        if path.exists():
            shutil.rmtree(path)
            removed.append(str(path.relative_to(ROOT)))
    for db in ROOT.glob("*.sqlite3"):
        db.unlink()
        removed.append(db.name)
    print("removed: " + (", ".join(removed) if removed else "nothing (already clean)"))
    return 0


@target
def help_(argv: list[str]) -> int:
    print(__doc__)
    print("Targets:\n")
    width = max(len(n) for n in _TARGETS)
    for name, fn in sorted(_TARGETS.items()):
        doc = (fn.__doc__ or "").strip().splitlines()
        summary = doc[0] if doc else ""
        marker = "  \x1b[33m[not built]\x1b[0m" if name in PENDING else ""
        print(f"  {name:<{width}}  {summary}{marker}")
    return 0


def main(argv: list[str]) -> int:
    sys.path.insert(0, str(SRC))
    if not argv or argv[0] in {"-h", "--help", "help"}:
        return help_([])
    name = argv[0]
    fn = _TARGETS.get(name)
    if fn is None:
        print(f"unknown target: {name}\n", file=sys.stderr)
        return help_([]) or 1
    return fn(argv[1:])


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
