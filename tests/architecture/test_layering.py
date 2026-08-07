"""Executable architecture rules.

A slide claiming clean architecture is worth nothing. A test that fails the
build when a dependency points the wrong way is worth a great deal, and it is
the thing to show a judge who asks whether the layering is real.

Two rules:
  1. Dependencies point inward, always.
  2. No Use Case Pack vocabulary leaks into domain/ or application/ -- that is
     what makes "swap the pack, change no code" true rather than aspirational.

Both are enforced by walking the AST import graph, so they hold for the whole
tree rather than for the files someone remembered to check.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[2] / "src" / "forge"

# Inner to outer. A layer may import itself and anything to its left.
LAYERS: tuple[str, ...] = ("domain", "application", "infrastructure", "interfaces")

# The domain layer's only permitted third-party import.
#
# Pydantic is a data-modelling library: it declares shapes and validates them,
# it performs no I/O, and it binds us to no framework. Allowing it keeps the
# boundary models self-validating, which is worth more than import purity.
# The allowance is explicit and narrow ON PURPOSE -- if it were left to
# convention, `import requests` would appear in domain/ by hour 20.
DOMAIN_THIRD_PARTY_ALLOWLIST: frozenset[str] = frozenset({"pydantic"})

# Vocabulary belonging to a specific Use Case Pack. None of it may appear in
# the inner layers, in code or in strings.
PACK_VOCABULARY: tuple[str, ...] = (
    "wheel", "brake", "weld", "fastener_count", "lug", "rim", "tpms", "erpnext",
)

# Files whose whole job is to talk about a pack or an adapter by name.
PACK_VOCAB_EXEMPT: frozenset[str] = frozenset()


def python_files(layer: str) -> list[Path]:
    root = SRC / layer
    return sorted(root.rglob("*.py")) if root.exists() else []


def imports_of(path: Path) -> list[tuple[str, int]]:
    """Every module imported by `path`, with line numbers."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    out: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            out.extend((alias.name, node.lineno) for alias in node.names)
        # Relative imports stay inside the module's own package, so only
        # absolute `from X import ...` can cross a layer boundary.
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            out.append((node.module, node.lineno))
    return out


def stdlib_modules() -> frozenset[str]:
    import sys  # noqa: PLC0415

    return frozenset(sys.stdlib_module_names)


# ---------------------------------------------------------------------------
# Rule 1: dependencies point inward
# ---------------------------------------------------------------------------
@pytest.mark.parametrize("layer", LAYERS)
def test_dependencies_point_inward(layer: str) -> None:
    allowed = set(LAYERS[: LAYERS.index(layer) + 1])
    violations: list[str] = []

    for path in python_files(layer):
        for module, lineno in imports_of(path):
            if not module.startswith("forge."):
                continue
            parts = module.split(".")
            if len(parts) < 2:
                continue
            target = parts[1]
            if target in LAYERS and target not in allowed:
                violations.append(
                    f"{path.relative_to(SRC.parent.parent)}:{lineno} "
                    f"{layer} imports {target} (outward)"
                )

    assert not violations, "dependencies must point inward:\n  " + "\n  ".join(violations)


def test_domain_imports_nothing_but_stdlib_and_the_allowlist() -> None:
    """The innermost layer stays free of frameworks and I/O."""
    std = stdlib_modules()
    violations: list[str] = []

    for path in python_files("domain"):
        for module, lineno in imports_of(path):
            root = module.split(".")[0]
            if root == "forge" or root in std or root in DOMAIN_THIRD_PARTY_ALLOWLIST:
                continue
            violations.append(
                f"{path.relative_to(SRC.parent.parent)}:{lineno} imports '{module}'"
            )

    assert not violations, (
        "domain/ may import only the standard library and "
        f"{sorted(DOMAIN_THIRD_PARTY_ALLOWLIST)}:\n  " + "\n  ".join(violations)
    )


def test_domain_performs_no_io() -> None:
    """Belt and braces: no I/O call sites in the innermost layer.

    Import rules alone would not catch `open(...)`, which is a builtin.
    """
    forbidden = {"open", "print", "input"}
    violations: list[str] = []

    for path in python_files("domain"):
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        for node in ast.walk(tree):
            if (
                isinstance(node, ast.Call)
                and isinstance(node.func, ast.Name)
                and node.func.id in forbidden
            ):
                violations.append(
                    f"{path.relative_to(SRC.parent.parent)}:{node.lineno} calls "
                    f"{node.func.id}()"
                )

    assert not violations, "domain/ must not perform I/O:\n  " + "\n  ".join(violations)


# ---------------------------------------------------------------------------
# Rule 2: pack isolation -- what makes the live pack switch real
# ---------------------------------------------------------------------------
def _docstring_nodes(tree: ast.Module) -> set[int]:
    """id() of every Constant node that is a docstring."""
    out: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            body = getattr(node, "body", [])
            if (
                body
                and isinstance(body[0], ast.Expr)
                and isinstance(body[0].value, ast.Constant)
                and isinstance(body[0].value.value, str)
            ):
                out.add(id(body[0].value))
    return out


def _pack_terms_in(text: str) -> list[str]:
    """Word-boundary match, so 'rim' does not fire on 'primary'."""
    import re  # noqa: PLC0415

    lowered = text.lower()
    return [t for t in PACK_VOCABULARY if re.search(rf"\b{re.escape(t)}\b", lowered)]


@pytest.mark.parametrize("layer", ["domain", "application"])
def test_no_pack_vocabulary_in_inner_layers(layer: str) -> None:
    """Adding an inspection task must be a folder, not a code change.

    If the inner layers branch on 'wheel', the pack switch is a demo trick. If
    they do not, the switch is a property of the architecture. This test is the
    difference, and it is why the claim survives hostile questioning.

    Scope is deliberately *executable code and runtime string values* --
    identifiers, attributes, and non-docstring literals. Docstrings are exempt:
    they explain why the code is shaped as it is, and a rule that forced us to
    describe a wheel-off failure mode without saying "wheel" would buy
    compliance by making the code harder to understand. What matters is that no
    behaviour keys off pack vocabulary, and that is what is checked.
    """
    violations: list[str] = []

    for path in python_files(layer):
        if path.name in PACK_VOCAB_EXEMPT:
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        docstrings = _docstring_nodes(tree)
        rel = path.relative_to(SRC.parent.parent)

        for node in ast.walk(tree):
            found: list[str] = []
            what = ""
            if isinstance(node, ast.Name):
                found, what = _pack_terms_in(node.id), f"identifier '{node.id}'"
            elif isinstance(node, ast.Attribute):
                found, what = _pack_terms_in(node.attr), f"attribute '.{node.attr}'"
            elif isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
                found, what = _pack_terms_in(node.name), f"definition '{node.name}'"
            elif isinstance(node, ast.arg):
                found, what = _pack_terms_in(node.arg), f"parameter '{node.arg}'"
            elif (
                isinstance(node, ast.Constant)
                and isinstance(node.value, str)
                and id(node) not in docstrings
            ):
                found = _pack_terms_in(node.value)
                what = f"string literal {node.value[:60]!r}"

            if found:
                violations.append(
                    f"{rel}:{getattr(node, 'lineno', 0)} {what} contains {found}"
                )

    assert not violations, (
        f"{layer}/ must be pack-agnostic; move this to packs/<id>/:\n  "
        + "\n  ".join(violations)
    )


# ---------------------------------------------------------------------------
# Rule 3: ports are abstract
# ---------------------------------------------------------------------------
def test_every_port_is_an_abstract_base_class() -> None:
    """A 'port' that can be instantiated is just a class.

    Every external system sits behind an ABC with at least a real and a fake
    implementation; the fakes are what make tests fast and the demo reliable.
    """
    port_dir = SRC / "application" / "ports"
    if not port_dir.exists():
        pytest.skip("ports package not created yet")

    offenders: list[str] = []
    for path in sorted(port_dir.glob("*.py")):
        if path.name == "__init__.py":
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
        classes = [n for n in tree.body if isinstance(n, ast.ClassDef)]

        # Exception hierarchies are resolved transitively: a module defines a
        # base like LLMError, then subclasses it for each retry class. Matching
        # only on `Exception` would miss every subclass.
        exception_names = {"Exception", "ValueError", "RuntimeError", "TypeError", "KeyError"}
        for cls in classes:
            bases = {
                b.id if isinstance(b, ast.Name) else getattr(b, "attr", "") for b in cls.bases
            }
            if bases & exception_names or any(b.endswith("Error") for b in bases):
                exception_names.add(cls.name)

        for cls in classes:
            if cls.name.startswith("_"):
                continue
            bases = {
                b.id if isinstance(b, ast.Name) else getattr(b, "attr", "")
                for b in cls.bases
            }
            # Value objects and typed exceptions legitimately share these
            # modules -- a port's signature is meaningless without the types it
            # exchanges. The rule constrains the *interfaces*, not the data
            # that travels through them.
            is_dataclass = any(
                getattr(d, "id", getattr(getattr(d, "func", d), "id", "")) == "dataclass"
                for d in cls.decorator_list
            )
            if is_dataclass or cls.name in exception_names:
                continue
            has_abstract_method = any(
                isinstance(n, ast.FunctionDef | ast.AsyncFunctionDef)
                and any(
                    getattr(d, "id", getattr(d, "attr", "")) == "abstractmethod"
                    for d in n.decorator_list
                )
                for n in cls.body
            )
            is_protocol = "Protocol" in bases
            is_abc = "ABC" in bases and has_abstract_method
            if not (is_protocol or is_abc):
                offenders.append(f"{path.name}:{cls.lineno} {cls.name}")

    assert not offenders, (
        "ports must be ABCs with abstract methods, or Protocols:\n  " + "\n  ".join(offenders)
    )


def test_port_dataclass_annotations_resolve_at_runtime() -> None:
    """Every port DTO's field types must be importable at runtime.

    `from __future__ import annotations` makes annotations lazy strings, so a
    field type parked in a TYPE_CHECKING block still *looks* fine -- the module
    imports, the dataclass constructs, the tests pass. It breaks later and
    elsewhere: the first time anything introspects the type at runtime
    (serialisation, a FastAPI response model, `typing.get_type_hints`) it raises
    NameError far from the import that caused it.

    The rule: a name used in a dataclass FIELD annotation is imported at
    runtime; only names used solely in method signatures go under TYPE_CHECKING.
    This test is what keeps that rule true.
    """
    import dataclasses  # noqa: PLC0415
    import sys  # noqa: PLC0415
    import typing  # noqa: PLC0415

    from forge.application import ports  # noqa: PLC0415

    failures: list[str] = []
    for name in ports.__all__:
        obj = getattr(ports, name)
        if not (isinstance(obj, type) and dataclasses.is_dataclass(obj)):
            continue
        module_ns = vars(sys.modules[obj.__module__])
        try:
            typing.get_type_hints(obj, globalns=dict(module_ns))
        except Exception as exc:  # noqa: BLE001 - reporting the failure IS the test
            failures.append(f"{name} ({obj.__module__}): {type(exc).__name__}: {exc}")

    assert not failures, (
        "port dataclass annotations must resolve at runtime; move these imports "
        "out of the TYPE_CHECKING block:\n  " + "\n  ".join(failures)
    )
