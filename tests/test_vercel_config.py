"""
Group L: Vercel project-separation contract.

Two Vercel projects share this repository: the existing frontend project
(Root Directory `frontend/`, reading `frontend/vercel.json`) and the
proposed backend project (Root Directory `.`, reading the repository-
root `vercel.json`). A Vercel project's Root Directory is sandboxed — it
cannot read files outside it — so these two files are the ONLY place
each project's settings can live, and they must never merge back into
one: a stray `"framework": "nextjs"` or npm build/install command at the
repository root would silently break Vercel's Python/FastAPI
auto-detection for the backend project (see `pyproject.toml`'s
`[tool.vercel] entrypoint`), and a `functions` key inside
`frontend/vercel.json` would be dead, confusing configuration the
frontend project can never reach.

This parses the actual on-disk JSON files — not a copy of their expected
content — so it fails the moment either file drifts from this contract,
regardless of who or what edited it.
"""

import json
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
ROOT_VERCEL_JSON = REPO_ROOT / "vercel.json"
FRONTEND_VERCEL_JSON = REPO_ROOT / "frontend" / "vercel.json"
EVALUATE_ROUTE_TS = REPO_ROOT / "frontend" / "src" / "app" / "api" / "evaluate" / "[ticker]" / "route.ts"


def _load(path: Path) -> dict:
    return json.loads(path.read_text())


class TestFrontendVercelConfig:
    def test_file_exists(self):
        assert FRONTEND_VERCEL_JSON.is_file()

    def test_declares_nextjs_framework(self):
        config = _load(FRONTEND_VERCEL_JSON)
        assert config.get("framework") == "nextjs"

    def test_declares_npm_install_and_build_commands(self):
        config = _load(FRONTEND_VERCEL_JSON)
        assert "npm" in config.get("installCommand", "")
        assert "npm" in config.get("buildCommand", "")

    def test_declares_next_output_directory(self):
        config = _load(FRONTEND_VERCEL_JSON)
        assert config.get("outputDirectory") == ".next"

    def test_has_no_python_function_configuration(self):
        """A `functions` key here would be unreachable dead configuration
        (frontend/'s Root Directory sandbox can't see repo-root src/) and
        a sign the two files drifted back together."""
        config = _load(FRONTEND_VERCEL_JSON)
        assert "functions" not in config


class TestRepoRootVercelConfig:
    def test_file_exists(self):
        assert ROOT_VERCEL_JSON.is_file()

    def test_does_not_declare_nextjs_framework(self):
        config = _load(ROOT_VERCEL_JSON)
        assert config.get("framework") != "nextjs"

    def test_has_no_npm_install_or_build_commands(self):
        config = _load(ROOT_VERCEL_JSON)
        assert "installCommand" not in config
        assert "buildCommand" not in config

    def test_has_no_next_output_directory(self):
        config = _load(ROOT_VERCEL_JSON)
        assert "outputDirectory" not in config

    def test_declares_the_python_entrypoint_function(self):
        config = _load(ROOT_VERCEL_JSON)
        assert "src/api/main.py" in config.get("functions", {})

    def test_exclude_files_fits_vercel_configuration_limit(self):
        """Vercel rejects a function ``excludeFiles`` glob longer than
        256 characters before it creates the project or starts a build."""
        config = _load(ROOT_VERCEL_JSON)
        exclude = config["functions"]["src/api/main.py"]["excludeFiles"]
        assert len(exclude) <= 256

    def test_excludes_frontend_tests_validation_and_trading_code(self):
        config = _load(ROOT_VERCEL_JSON)
        exclude = config["functions"]["src/api/main.py"]["excludeFiles"]
        for must_exclude in ("frontend/**", "tests/**", "validation/**", "src/trading/**"):
            assert must_exclude in exclude

    def test_excludes_backtesting_now_that_the_import_seam_is_removed(self):
        """src.api.main no longer reaches src.backtesting.historical_tester
        at all (DEFAULT_SP500_TOP_100_TICKERS moved to
        src/utils/ticker_universe.py) -- confirmed by the import-graph
        trace in the deployment prep report. Nothing under
        src/backtesting/ is needed in the deployed bundle."""
        config = _load(ROOT_VERCEL_JSON)
        exclude = config["functions"]["src/api/main.py"]["excludeFiles"]
        assert "src/backtesting/**" in exclude

    def test_env_exclusion_is_a_single_generic_glob(self):
        """A generic `.env*` catches every variant (.env, .env.local,
        .env.production, anything else) in one pattern, rather than an
        enumerated list that silently misses a new variant later."""
        config = _load(ROOT_VERCEL_JSON)
        exclude = config["functions"]["src/api/main.py"]["excludeFiles"]
        assert ".env*" in exclude
        # The old enumerated form must not linger alongside the generic
        # one -- that would just be redundant, unmaintained duplication.
        assert ".env.example" not in exclude
        assert "frontend/.env.local" not in exclude

    def test_excludes_db_and_database_heartbeat_now_that_the_import_seam_is_removed(self):
        """src.utils.db (psycopg2) and src.utils.database_heartbeat are
        both unreachable from src.api.main's import graph -- confirmed
        by the import-graph trace -- and neither belongs in the
        valuation deployment now that DEFAULT_SP500_TOP_100_TICKERS no
        longer routes sector_medians.py through src.backtesting.
        historical_tester (which used to be the only path that pulled
        src.utils.db in transitively)."""
        config = _load(ROOT_VERCEL_JSON)
        exclude = config["functions"]["src/api/main.py"]["excludeFiles"]
        assert "src/utils/db.py" in exclude
        assert "src/utils/database_heartbeat.py" in exclude

    def test_keeps_the_utils_modules_the_deployed_path_actually_needs(self):
        """The exclusion list must be surgical, not a blanket
        `src/utils/**` -- cache.py, macro.py, and ticker_universe.py
        (plus the package's own __init__.py) are genuinely imported by
        src.api.main's own import graph and must stay reachable."""
        config = _load(ROOT_VERCEL_JSON)
        exclude = config["functions"]["src/api/main.py"]["excludeFiles"]
        assert "src/utils/**" not in exclude
        for must_stay_reachable in (
            "src/utils/cache.py",
            "src/utils/macro.py",
            "src/utils/ticker_universe.py",
            "src/utils/__init__.py",
        ):
            assert must_stay_reachable not in exclude

    def test_declares_the_layered_backend_hard_stop_duration(self):
        """40s -- deliberately well below Vercel Hobby's own ceiling of
        300s (confirmed against
        https://vercel.com/docs/functions/limitations#max-duration) so
        THIS value, not the platform default, is what actually bounds
        the backend. It is the innermost layer of the three-layer
        timeout contract: backend hard stop (40s) < frontend upstream
        wait (45s) < frontend function limit (60s) -- see
        TestLayeredTimeoutOrdering below for the full cross-file proof.
        Choosing 40s (not 300s) means Vercel itself terminates a runaway
        backend invocation and returns a platform 504 that is DESIGNED
        to normally arrive while the frontend is still within its own
        45s wait window, so the frontend observes and normalizes that
        failure instead of giving up first while the backend keeps
        running, wastefully, up to the old 300s ceiling. (This ordering
        does not itself guarantee real-world arrival timing -- see
        TestLayeredTimeoutOrdering's docstring below.)"""
        config = _load(ROOT_VERCEL_JSON)
        assert config["functions"]["src/api/main.py"]["maxDuration"] == 40

    def test_declares_no_memory_key(self):
        """Vercel does NOT support a `memory` key in vercel.json at all --
        their own docs state: "You cannot set your memory size using
        vercel.json. If you try to do so, you will receive a warning at
        build time... Hobby users will always use the default memory
        size of 2 GB (1 vCPU)." Hobby memory is a fixed, non-configurable
        2 GB/1 vCPU -- this test guards against a future edit
        reintroducing an unsupported (and build-warning-producing) key
        under the mistaken belief that it does something."""
        config = _load(ROOT_VERCEL_JSON)
        assert "memory" not in config["functions"]["src/api/main.py"]

    def test_only_declares_infrastructure_and_python_function_keys(self):
        """Every top-level key must be either `$schema` or the
        Python-specific `functions` key -- nothing that belongs to a
        Node/Next.js build (framework, installCommand, buildCommand,
        outputDirectory, or anything else)."""
        allowed_top_level_keys = {"$schema", "functions"}
        config = _load(ROOT_VERCEL_JSON)
        assert set(config.keys()) <= allowed_top_level_keys


class TestConfigurationsCannotAccidentallyMix:
    def test_backend_config_carries_none_of_the_frontend_specific_keys(self):
        backend = _load(ROOT_VERCEL_JSON)
        nextjs_only_keys = {"framework", "installCommand", "buildCommand", "outputDirectory"}
        assert not (nextjs_only_keys & backend.keys())

    def test_frontend_config_carries_none_of_the_backend_specific_keys(self):
        frontend = _load(FRONTEND_VERCEL_JSON)
        assert "functions" not in frontend

    def test_backend_config_text_contains_no_frontend_build_commands(self):
        """Belt-and-suspenders over the structural checks above: even a
        value nested somewhere unexpected (not just a top-level key)
        would be caught by scanning the raw file text."""
        backend_text = ROOT_VERCEL_JSON.read_text()
        for forbidden in ("npm ci", "npm run build", ".next", "nextjs"):
            assert forbidden not in backend_text

    def test_frontend_config_text_contains_no_python_function_reference(self):
        frontend_text = FRONTEND_VERCEL_JSON.read_text()
        assert "src/api/main.py" not in frontend_text
        assert "excludeFiles" not in frontend_text


class TestLayeredTimeoutOrdering:
    """
    The three-layer hard-duration contract, read from the two REAL files
    that each own one layer, not copied constants:

        backend platform hard limit (vercel.json maxDuration):        40s
      < frontend upstream wait (route.ts's VALUATION_BACKEND_REQUEST_
        TIMEOUT_MS, i.e. how long the frontend's own fetch waits for
        the Python backend before giving up):                         45s
      < frontend route's own function limit (route.ts's exported
        maxDuration -- how long Vercel lets the ROUTE's function run
        before killing it):                                           60s

    Why this exact ordering, not just "all three configured": Vercel
    terminates a backend invocation that exceeds ITS maxDuration and
    returns a platform 504 to whoever's waiting on it. This ordering is
    DESIGNED so that, because 40s is strictly less than the frontend's
    45s wait, the platform 504 normally arrives while the frontend is
    still waiting -- it observes and normalizes that platform 504 itself
    (see backend-response.ts) instead of already having given up. This
    configuration only guarantees the ORDERING of the declared limits,
    not real-world response timing -- runtime scheduling, process
    teardown, and network propagation can consume part of the 5s margin.
    If propagation ever exceeds it, the frontend's own AbortError path
    still returns the same controlled 504 VALUATION_BACKEND_TIMEOUT, so
    the observable outcome is identical either way. Separately, because
    the frontend's 45s wait is strictly less than the route's own 60s
    function limit, the route keeps 15s of headroom after its own fetch
    gives up to build and return a controlled response before Vercel
    would kill the ROUTE function too.

    Parses the actual vercel.json and route.ts source text (plain JSON
    load + regex, no TypeScript parser dependency) so this fails the
    moment any of the three drifts without the others being
    reconsidered -- deliberately NOT three independent constant checks:
    the ordering tests below read all three real values together in one
    assertion chain.
    """

    def _route_text(self) -> str:
        assert EVALUATE_ROUTE_TS.is_file(), f"Route file not found: {EVALUATE_ROUTE_TS}"
        return EVALUATE_ROUTE_TS.read_text()

    def _backend_max_duration_seconds(self) -> int:
        config = _load(ROOT_VERCEL_JSON)
        return config["functions"]["src/api/main.py"]["maxDuration"]

    def _frontend_route_max_duration_seconds(self) -> int:
        match = re.search(r"export const maxDuration = (\d+);", self._route_text())
        assert match, "export const maxDuration = <N>; not found in route.ts"
        return int(match.group(1))

    def _frontend_backend_timeout_ms(self) -> int:
        match = re.search(
            r"VALUATION_BACKEND_REQUEST_TIMEOUT_MS = ([\d_]+);", self._route_text()
        )
        assert match, "VALUATION_BACKEND_REQUEST_TIMEOUT_MS = <N>; not found in route.ts"
        return int(match.group(1).replace("_", ""))

    def test_each_exact_value(self):
        """The three raw values, individually -- a precise starting
        point before the combined-ordering tests below, so a failure
        there immediately shows WHICH layer drifted."""
        assert self._backend_max_duration_seconds() == 40
        assert self._frontend_backend_timeout_ms() == 45_000
        assert self._frontend_route_max_duration_seconds() == 60

    def test_backend_max_duration_stays_within_the_verified_hobby_ceiling(self):
        """40s is a deliberate choice well inside Vercel Hobby's own
        default/maximum of 300s -- see
        https://vercel.com/docs/functions/limitations#max-duration --
        not a value Hobby forces on us."""
        assert self._backend_max_duration_seconds() <= 300

    def test_full_three_layer_ordering(self):
        """The actual contract, as one combined chain over all three
        REAL values -- not three constants tested in isolation. This is
        what would have caught the original failure mode: a browser-
        facing proxy giving up after 45s while the backend was left
        free to run for 300s, with no ordering relationship enforced
        between the two at all."""
        backend_ms = self._backend_max_duration_seconds() * 1000
        frontend_wait_ms = self._frontend_backend_timeout_ms()
        route_limit_ms = self._frontend_route_max_duration_seconds() * 1000

        assert backend_ms < frontend_wait_ms < route_limit_ms

    def test_exact_headroom_at_each_layer_boundary(self):
        """Documents the specific margins this deployment chose, not
        merely that SOME margin exists at each boundary."""
        backend_ms = self._backend_max_duration_seconds() * 1000
        frontend_wait_ms = self._frontend_backend_timeout_ms()
        route_limit_ms = self._frontend_route_max_duration_seconds() * 1000

        # Backend's hard stop (40s) is 5s inside the frontend's wait
        # (45s) -- this margin is DESIGNED to let the backend platform
        # response arrive while the frontend is still waiting, but it
        # does not guarantee real-world propagation timing (see
        # TestLayeredTimeoutOrdering's class docstring above).
        assert frontend_wait_ms - backend_ms == 5_000
        # The route keeps 15s after its own fetch gives up (45s) to
        # build and return a controlled response before ITS OWN 60s
        # function limit would kill it too.
        assert route_limit_ms - frontend_wait_ms == 15_000
