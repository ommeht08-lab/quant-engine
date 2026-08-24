"""
Group M: repository-root `pyproject.toml` contract — the Vercel Python
Function backend's dependency/runtime-version declaration (see that
file's own top-of-file comment for the full rationale). This parses the
actual on-disk TOML, not a copy of its expected content, so it fails the
moment the file drifts from this contract.

Uses stdlib `tomllib` on Python >= 3.11 (this repository's CI target)
and falls back to `tomli` on older interpreters (a guaranteed transitive
dependency of `pytest` itself below 3.11 — see requirements-dev.txt) —
no new dependency was added to write this test.
"""

from pathlib import Path

try:
    import tomllib
except ModuleNotFoundError:  # Python < 3.11
    import tomli as tomllib

REPO_ROOT = Path(__file__).resolve().parents[1]
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"

# The minimal set genuinely required by src.api.main's import graph, as
# re-verified after extracting DEFAULT_SP500_TOP_100_TICKERS out of
# src.backtesting.historical_tester (see src/utils/ticker_universe.py).
# upstash-redis is included deliberately: it activates src.utils.cache's
# existing @cached statement/risk-free-rate caching against the same
# Upstash database the frontend already uses (see pyproject.toml's own
# comment on that dependency) -- it is not forbidden the way psycopg2/
# alpaca/scipy/uvicorn are, none of which this service ever needs.
EXPECTED_DEPENDENCY_NAMES = {
    "fastapi", "pandas", "numpy", "requests", "yfinance", "python-dotenv", "upstash-redis",
}
FORBIDDEN_DEPENDENCY_NAMES = {"scipy", "psycopg2-binary", "psycopg2", "alpaca-py", "alpaca", "uvicorn"}


def _load_pyproject() -> dict:
    with open(PYPROJECT_PATH, "rb") as f:
        return tomllib.load(f)


def _dependency_name(requirement: str) -> str:
    """`"fastapi==0.128.8"` -> `"fastapi"` — this file only ever uses
    exact `==` pins (see the file's own comment on why), so splitting on
    the first `==` is sufficient; no need for a full PEP 508 parser."""
    return requirement.split("==")[0].strip()


class TestPythonVersionConstraint:
    def test_requires_python_is_present(self):
        config = _load_pyproject()
        assert "requires-python" in config["project"]

    def test_pinned_to_the_312_line_specifically(self):
        """Must be narrower than a bare ">=3.12" (which would also
        silently accept 3.13/3.14, newer minors this codebase has never
        been run against) — Vercel's Python runtime starts at 3.12."""
        requires_python = _load_pyproject()["project"]["requires-python"]
        assert requires_python == ">=3.12,<3.13"

    def test_the_constraint_actually_admits_312_and_excludes_313(self):
        """Belt-and-suspenders over the exact-string check above: parse
        the constraint with the same specifier semantics pip itself
        uses, and confirm 3.12.x is IN and 3.11.x/3.13.x are OUT, in
        case the exact string ever gets reformatted (e.g. added
        whitespace) without changing its actual meaning."""
        from packaging.specifiers import SpecifierSet

        requires_python = _load_pyproject()["project"]["requires-python"]
        spec = SpecifierSet(requires_python)
        assert spec.contains("3.12.0")
        assert spec.contains("3.12.9")
        assert not spec.contains("3.13.0")
        assert not spec.contains("3.11.9")


class TestMinimalDependencySet:
    def test_dependencies_key_present(self):
        config = _load_pyproject()
        assert "dependencies" in config["project"]

    def test_dependency_names_match_exactly(self):
        """Not just "contains the required ones" -- an EXACT match, so an
        accidentally reintroduced scipy/psycopg2/alpaca-py/uvicorn entry
        (or any other unreviewed addition) fails this test immediately
        rather than silently bloating the deployed bundle."""
        config = _load_pyproject()
        names = {_dependency_name(dep) for dep in config["project"]["dependencies"]}
        assert names == EXPECTED_DEPENDENCY_NAMES

    def test_no_forbidden_dependency_present(self):
        config = _load_pyproject()
        names = {_dependency_name(dep) for dep in config["project"]["dependencies"]}
        assert not (names & FORBIDDEN_DEPENDENCY_NAMES)

    def test_every_dependency_is_exactly_pinned(self):
        """An unpinned entry (e.g. bare "pandas") could silently resolve
        to a newer major on a fresh Vercel build than this repository's
        own venv/tests were ever run against — see the file's own
        comment on why pandas 3.0/numpy 2.5/scipy 1.18 were deliberately
        avoided."""
        config = _load_pyproject()
        for dep in config["project"]["dependencies"]:
            assert "==" in dep, f"{dep!r} is not exactly pinned"


class TestEntrypointConfiguration:
    def test_tool_vercel_entrypoint_points_at_the_existing_main_module(self):
        """Confirms Vercel is pointed at src/api/main.py's own `app`
        without any code having been moved, copied, or duplicated."""
        config = _load_pyproject()
        assert config["tool"]["vercel"]["entrypoint"] == "src.api.main:app"

    def test_entrypoint_resolves_to_a_file_that_actually_exists_and_defines_app(self):
        entrypoint = _load_pyproject()["tool"]["vercel"]["entrypoint"]
        module_path, attr = entrypoint.split(":")
        resolved = REPO_ROOT / (module_path.replace(".", "/") + ".py")
        assert resolved.is_file()
        assert attr == "app"
        assert "app = FastAPI(" in resolved.read_text()
