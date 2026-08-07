"""Machine capability report.

Answers one question honestly: *what can this machine actually run?*

Every check reports a measured fact, never an assumption. A check that cannot
be performed reports UNKNOWN rather than guessing -- an unverified green tick
is worse than a red one, because it is believed.

Checks are classified:
  BLOCKER  the demo cannot run without this
  DEGRADED the demo runs with a named, visible reduction in capability
  INFO     recorded for the record; nothing depends on it
"""

from __future__ import annotations

import importlib.util
import os
import platform
import shutil
import socket
import subprocess
import sys
import time
from dataclasses import dataclass, field
from enum import Enum
from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]

MIN_PYTHON = (3, 11)
MIN_FREE_DISK_GB = 6.0


class Level(str, Enum):
    BLOCKER = "BLOCKER"
    DEGRADED = "DEGRADED"
    INFO = "INFO"


class Status(str, Enum):
    OK = "ok"
    FAIL = "fail"
    UNKNOWN = "unknown"


@dataclass(slots=True)
class Check:
    name: str
    status: Status
    detail: str
    level: Level = Level.INFO
    # What the operator should do about it. Empty when status is OK.
    remedy: str = ""
    # What degrades if this stays failed. Required for DEGRADED so nothing
    # fails silently (CLAUDE.md rule 4).
    consequence: str = ""


@dataclass(slots=True)
class Report:
    checks: list[Check] = field(default_factory=list)

    def add(self, check: Check) -> None:
        self.checks.append(check)

    @property
    def blockers(self) -> list[Check]:
        return [c for c in self.checks if c.level is Level.BLOCKER and c.status is Status.FAIL]

    @property
    def degradations(self) -> list[Check]:
        return [c for c in self.checks if c.level is Level.DEGRADED and c.status is Status.FAIL]


# ---------------------------------------------------------------------------
# Individual checks
# ---------------------------------------------------------------------------
def check_python() -> Check:
    v = sys.version_info
    got = f"{v.major}.{v.minor}.{v.micro}"
    if (v.major, v.minor) >= MIN_PYTHON:
        return Check("python", Status.OK, f"{got} ({platform.python_implementation()})")
    return Check(
        "python",
        Status.FAIL,
        f"{got}, need >= {MIN_PYTHON[0]}.{MIN_PYTHON[1]}",
        Level.BLOCKER,
        remedy=f"Install Python {MIN_PYTHON[0]}.{MIN_PYTHON[1]} or newer.",
    )


def check_cpu() -> Check:
    return Check("cpu", Status.OK, f"{os.cpu_count() or '?'} logical cores, {platform.machine()}")


def check_gpu() -> Check:
    """Presence of a CUDA GPU decides the vision backend and the honest fps target."""
    smi = shutil.which("nvidia-smi")
    if smi is None:
        return Check(
            "gpu",
            Status.FAIL,
            "no nvidia-smi on PATH; CUDA unavailable",
            Level.DEGRADED,
            remedy="None needed. ONNX Runtime CPU is selected automatically.",
            consequence=(
                "Vision runs on CPU. Report the measured fps, do not claim 5 -- "
                "see docs/DECISIONS.md ADR-0004."
            ),
        )
    try:
        out = subprocess.run(  # noqa: S603
            [smi, "--query-gpu=name,memory.total", "--format=csv,noheader"],
            capture_output=True, text=True, timeout=10, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return Check("gpu", Status.UNKNOWN, f"nvidia-smi failed: {exc}", Level.DEGRADED)
    if out.returncode != 0 or not out.stdout.strip():
        return Check("gpu", Status.UNKNOWN, "nvidia-smi returned nothing", Level.DEGRADED)
    return Check("gpu", Status.OK, out.stdout.strip().splitlines()[0])


def check_disk() -> Check:
    usage = shutil.disk_usage(ROOT)
    free_gb = usage.free / 1024**3
    if free_gb >= MIN_FREE_DISK_GB:
        return Check("disk", Status.OK, f"{free_gb:.1f} GB free")
    return Check(
        "disk",
        Status.FAIL,
        f"{free_gb:.1f} GB free, need >= {MIN_FREE_DISK_GB}",
        Level.BLOCKER,
        remedy="Free space. Model weights and the memory bank need room.",
    )


def check_docker() -> Check:
    exe = shutil.which("docker")
    if exe is None:
        return Check(
            "docker",
            Status.FAIL,
            "not installed",
            Level.DEGRADED,
            remedy="Install Docker Desktop, or run with PROFILE=local.",
            consequence=(
                "PROFILE=docker (Postgres/Redis/Chroma/MinIO) is unavailable. "
                "PROFILE=local runs the whole demo on SQLite + local files."
            ),
        )
    try:
        out = subprocess.run(  # noqa: S603
            [exe, "version", "--format", "{{.Server.Version}}"],
            capture_output=True, text=True, timeout=15, check=False,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return Check("docker", Status.UNKNOWN, str(exc), Level.DEGRADED)
    if out.returncode != 0:
        return Check(
            "docker", Status.FAIL, "installed but daemon not responding",
            Level.DEGRADED, remedy="Start Docker Desktop.",
            consequence="Same as not installed: PROFILE=local still works.",
        )
    return Check("docker", Status.OK, f"engine {out.stdout.strip()}")


def check_node() -> Check:
    exe = shutil.which("npm")
    if exe is None:
        return Check(
            "node", Status.FAIL, "npm not on PATH", Level.BLOCKER,
            remedy="Install Node 20+. The web app cannot build without it.",
        )
    try:
        out = subprocess.run(  # noqa: S603
            [exe, "--version"], capture_output=True, text=True, timeout=30, check=False
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return Check("node", Status.UNKNOWN, str(exc), Level.BLOCKER)
    return Check("node", Status.OK, f"npm {out.stdout.strip()}")


def check_python_deps() -> list[Check]:
    """Import-check rather than pip-list, because what matters is whether it loads."""
    groups = {
        "core": (Level.BLOCKER, ["pydantic", "fastapi", "httpx", "yaml", "structlog"]),
        "ai": (Level.DEGRADED, ["langgraph", "chromadb", "numpy", "cv2", "onnxruntime"]),
    }
    checks: list[Check] = []
    for group, (level, mods) in groups.items():
        missing = [m for m in mods if importlib.util.find_spec(m) is None]
        if not missing:
            checks.append(Check(f"deps:{group}", Status.OK, f"{len(mods)} modules importable"))
        else:
            checks.append(
                Check(
                    f"deps:{group}", Status.FAIL, f"missing: {', '.join(missing)}", level,
                    remedy=f"python tasks.py install {'ai' if group == 'ai' else ''}".strip(),
                    consequence=(
                        "Vision and retrieval are unavailable; the API and the "
                        "deterministic rule engine still run."
                        if group == "ai" else ""
                    ),
                )
            )
    return checks


def _tcp_open(host: str, port: int, timeout: float = 2.0) -> bool:
    try:
        with socket.create_connection((host, port), timeout=timeout):
            return True
    except OSError:
        return False


def check_ollama() -> Check:
    if _tcp_open("127.0.0.1", 11434):
        return Check("llm:ollama", Status.OK, "reachable on 127.0.0.1:11434")
    return Check(
        "llm:ollama", Status.FAIL, "not reachable on 127.0.0.1:11434", Level.INFO,
        remedy="Start Ollama, or configure TCS_BASE_URL / ANTHROPIC_API_KEY.",
        consequence="One provider fewer in the tier chain.",
    )


def check_llm_config() -> Check:
    """At least one LLM provider must be configured, or every tier lands on the fake adapter."""
    configured = [
        name for name, var in (
            ("tcs", "TCS_BASE_URL"),
            ("anthropic", "ANTHROPIC_API_KEY"),
            ("ollama", "OLLAMA_BASE_URL"),
        ) if os.environ.get(var)
    ]
    if configured:
        return Check("llm:config", Status.OK, f"configured: {', '.join(configured)}")
    return Check(
        "llm:config", Status.FAIL, "no provider configured", Level.DEGRADED,
        remedy="Copy .env.example to .env and set one provider block.",
        consequence=(
            "Every tier resolves to the deterministic FakeAdapter. Tests pass "
            "and the demo runs, but no genuine model reasoning is shown."
        ),
    )


def check_tls() -> Check:
    """Whether TLS verification is routed through the OS trust store.

    On an intercepting-proxy network this is the difference between all four
    live APIs working and three of them failing with CERTIFICATE_VERIFY_FAILED.
    """
    from forge.bootstrap import init  # noqa: PLC0415

    state = init()["tls"]
    if state.startswith("active"):
        return Check("tls", Status.OK, state)
    return Check(
        "tls", Status.FAIL, state, Level.DEGRADED,
        remedy="pip install truststore",
        consequence=(
            "On a network with an intercepting TLS proxy, live external API "
            "calls fail certificate verification. See src/forge/bootstrap.py."
        ),
    )


def check_live_apis() -> list[Check]:
    """The four keyless public APIs the product depends on, measured not assumed."""
    from forge.bootstrap import init  # noqa: PLC0415

    init()  # TLS must be configured before the first outbound call.
    try:
        import httpx  # noqa: PLC0415
    except ImportError:
        return [Check("api:*", Status.UNKNOWN, "httpx not installed", Level.DEGRADED)]

    endpoints = {
        "api:open-meteo": (
            "https://api.open-meteo.com/v1/forecast"
            "?latitude=18.52&longitude=73.86&current=temperature_2m"
        ),
        "api:nhtsa": (
            "https://api.nhtsa.gov/recalls/recallsByVehicle"
            "?make=chevrolet&model=colorado&modelYear=2023"
        ),
        "api:fx": "https://open.er-api.com/v6/latest/USD",
        "api:nager": "https://date.nager.at/api/v3/PublicHolidays/2026/DE",
    }
    checks: list[Check] = []
    for name, url in endpoints.items():
        start = time.perf_counter()
        try:
            resp = httpx.get(url, timeout=8.0)
            ms = int((time.perf_counter() - start) * 1000)
            if resp.status_code == 200:
                checks.append(Check(name, Status.OK, f"HTTP 200 in {ms}ms"))
            else:
                checks.append(
                    Check(
                        name, Status.FAIL, f"HTTP {resp.status_code} in {ms}ms", Level.DEGRADED,
                        remedy="Check network egress and proxy settings.",
                        consequence="Falls back to cached last-good; the UI shows a `stale` chip.",
                    )
                )
        except Exception as exc:  # noqa: BLE001 - a doctor must never itself crash
            ms = int((time.perf_counter() - start) * 1000)
            checks.append(
                Check(
                    name, Status.FAIL, f"{type(exc).__name__} after {ms}ms", Level.DEGRADED,
                    remedy="Check network egress and proxy settings.",
                    consequence="Falls back to the recorded fixture; the UI shows a `stale` chip.",
                )
            )
    return checks


# ---------------------------------------------------------------------------
# Report
# ---------------------------------------------------------------------------
def collect() -> Report:
    report = Report()
    report.add(check_python())
    report.add(check_cpu())
    report.add(check_gpu())
    report.add(check_disk())
    report.add(check_node())
    report.add(check_docker())
    for c in check_python_deps():
        report.add(c)
    report.add(check_tls())
    report.add(check_llm_config())
    report.add(check_ollama())
    for c in check_live_apis():
        report.add(c)
    return report


_GLYPH = {Status.OK: "\x1b[32m ok \x1b[0m", Status.FAIL: "\x1b[31mFAIL\x1b[0m",
          Status.UNKNOWN: "\x1b[33m ?? \x1b[0m"}


def report() -> int:
    """Print the capability report. Returns non-zero only when a BLOCKER fails."""
    rep = collect()
    width = max(len(c.name) for c in rep.checks)

    print("\n\x1b[1mFORGE machine report\x1b[0m")
    print(f"{platform.system()} {platform.release()} - {ROOT}\n")
    for c in rep.checks:
        print(f"  [{_GLYPH[c.status]}] {c.name:<{width}}  {c.detail}")

    if rep.degradations:
        print("\n\x1b[33mDegraded capabilities (the demo runs, with these reductions):\x1b[0m")
        for c in rep.degradations:
            print(f"  - {c.name}: {c.consequence or c.detail}")
            if c.remedy:
                print(f"      remedy: {c.remedy}")

    if rep.blockers:
        print("\n\x1b[31mBlockers (the demo cannot run):\x1b[0m")
        for c in rep.blockers:
            print(f"  - {c.name}: {c.detail}")
            if c.remedy:
                print(f"      remedy: {c.remedy}")
        return 1

    print("\n\x1b[32mNo blockers.\x1b[0m")
    return 0


if __name__ == "__main__":
    raise SystemExit(report())
