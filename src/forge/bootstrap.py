"""Process-level setup that must happen before any outbound network call.

This is not a layer. It is the handful of things that have to be true about the
interpreter itself, applied once at the entry point of every process (API,
task runner, tests, eval harness).

Why this file exists
--------------------
The build network runs a TLS-interception proxy. It re-signs certificates with
a private CA that is installed in the *operating system* trust store but is not
in certifi's bundle, which Python's ssl module uses by default. The symptom is
brutal to debug under time pressure: ``curl`` succeeds, the browser succeeds,
and ``httpx`` raises CERTIFICATE_VERIFY_FAILED on the same URL -- and only for
*some* hosts, because interception is per-domain.

Measured on the build machine, 2026-08-07:

    without truststore   open-meteo FAIL, fx FAIL, nager FAIL, nhtsa OK
    with truststore      all four HTTP 200

``truststore`` routes Python's TLS verification through the OS trust store, so
the corporate CA is honoured exactly as it is for curl and the browser.

The alternative some teams reach for at 3am is ``verify=False``. We do not do
that. It disables certificate verification globally, it would ship to whatever
environment runs the container, and "we turned off TLS verification to make the
demo work" is a question we do not want asked on stage.
"""

from __future__ import annotations

import logging
import os
import ssl
from pathlib import Path

_log = logging.getLogger(__name__)

_initialised = False


def _inject_os_trust_store() -> str:
    """Route TLS verification through the OS trust store. Returns a status string."""
    if os.environ.get("FORGE_DISABLE_TRUSTSTORE"):
        return "skipped (FORGE_DISABLE_TRUSTSTORE set)"
    try:
        import truststore  # noqa: PLC0415
    except ImportError:
        return "unavailable (truststore not installed; certifi bundle in use)"
    try:
        truststore.inject_into_ssl()
    except Exception as exc:  # noqa: BLE001 - never let TLS setup kill the process
        return f"failed ({type(exc).__name__}: {exc}); certifi bundle in use"
    return "active (OS trust store)"


def load_dotenv(path: str = ".env") -> int:
    """Load KEY=VALUE pairs from a .env file into os.environ.

    Hand-rolled rather than pulling in python-dotenv: it is fifteen lines, and a
    dependency whose only job is parsing `KEY=VALUE` is not worth the supply
    chain.

    Existing environment variables always win. A value exported in the shell or
    injected by the container must never be silently overridden by a stale file
    on disk -- that is the kind of thing that makes a demo behave differently on
    the machine it was rehearsed on.
    """
    p = Path(path)
    if not p.exists():
        return 0
    loaded = 0
    for line in p.read_text(encoding="utf-8").splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if key and key not in os.environ:
            os.environ[key] = value
            loaded += 1
    return loaded


def insecure_hosts() -> frozenset[str]:
    """Hosts for which certificate verification is deliberately skipped.

    The TCS GenAI Lab sample code calls ``httpx.Client(verify=False)``. That
    works, and on a corporate endpoint behind an intercepting proxy it may be
    the only thing that works -- but applied the way the sample does it, it
    disables verification for **every** outbound call in the process, including
    the four public APIs we pull evidence from. An attacker on the network could
    then feed us a forged NHTSA recall record and we would cite it on stage.

    So this is scoped. ``FORGE_TLS_INSECURE_HOSTS`` names specific hosts, the
    exemption applies to those hosts only, and every other connection keeps full
    verification. Try ``truststore`` first; reach for this only if the corporate
    CA genuinely is not in the OS store.

    Usage is surfaced on /admin and recorded as a degradation, because "we
    turned off TLS verification" should never be invisible.
    """
    raw = os.environ.get("FORGE_TLS_INSECURE_HOSTS", "")
    return frozenset(h.strip().lower() for h in raw.split(",") if h.strip())


def verify_for(url: str) -> bool:
    """Whether to verify certificates for this URL."""
    hosts = insecure_hosts()
    if not hosts:
        return True
    try:
        from urllib.parse import urlparse  # noqa: PLC0415

        host = (urlparse(url).hostname or "").lower()
    except ValueError:
        return True
    if host in hosts:
        _log.warning(
            "TLS verification disabled for %s by FORGE_TLS_INSECURE_HOSTS", host
        )
        return False
    return True


def init(*, strict_tls_probe: bool = False) -> dict[str, str]:
    """Idempotent process setup. Safe to call from every entry point.

    Returns a dict of what was applied, so the Admin page and the doctor can
    show it rather than the operator having to trust that it happened.
    """
    global _initialised  # noqa: PLW0603
    state: dict[str, str] = {}

    if not _initialised:
        count = load_dotenv()
        state["dotenv"] = f"{count} variables loaded" if count else "no .env file"

    state["tls"] = _inject_os_trust_store()

    # An explicit CA bundle always wins over both of the above. Some plants
    # hand you a .pem rather than installing into the OS store.
    if bundle := os.environ.get("FORGE_CA_BUNDLE"):
        os.environ.setdefault("SSL_CERT_FILE", bundle)
        state["ca_bundle"] = bundle

    if strict_tls_probe:
        # Fail loudly at boot rather than at the first agent tool call.
        ctx = ssl.create_default_context()
        state["tls_verify_mode"] = ctx.verify_mode.name

    if not _initialised:
        _log.info("forge.bootstrap", extra={"state": state})
        _initialised = True
    return state


def status() -> dict[str, str]:
    """Current bootstrap state without re-applying anything."""
    return {"initialised": str(_initialised)}
