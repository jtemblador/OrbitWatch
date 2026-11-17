"""Suite-wide setup: run against the frozen `stations` fixture (Phase 11.3a).

Before any test module is collected (test_api.py builds the app's propagator at
import time), point `tle_fetcher.DATA_DIR` — the default GPFetcher cache dir — at
a temp dir seeded from `tests/fixtures/stations_omm.json`. Effects:
  * the suite passes on a fresh, offline clone (the real cache is gitignored);
  * it is deterministic — same elements every run, on every machine and in CI;
  * tests can no longer read or rewrite the developer's real
    `backend/data/tle/` cache (a past bug class — see key_information.md).

Set ORBITWATCH_TEST_LIVE_CACHE=1 to run against the real cache instead (e.g. to
check the suite against today's live stations group). Tests anchor their "now"
to the loaded catalog (`fixture_catalog.catalog_now`), so both modes work.
"""

import os
import shutil
import sys
import tempfile
from pathlib import Path

# Make `backend` importable and `fixture_catalog` importable as a top-level
# module from the test files (pytest's rootdir/prepend import mode).
_TESTS = Path(__file__).parent
sys.path.insert(0, str(_TESTS.parent))
sys.path.insert(0, str(_TESTS))

_tmp_cache: str | None = None


def pytest_configure(config):
    global _tmp_cache
    if os.environ.get("ORBITWATCH_TEST_LIVE_CACHE"):
        return
    import importlib
    import fixture_catalog

    _tmp_cache = tempfile.mkdtemp(prefix="orbitwatch-fixture-tle-")
    fixture_catalog.seed_cache(Path(_tmp_cache))
    # Some test files import the fetcher as `core.tle_fetcher` (backend/ on
    # sys.path) and others as `backend.core.tle_fetcher` — two distinct module
    # objects, so redirect both.
    sys.path.insert(0, str(_TESTS.parent / "backend"))
    for name in ("backend.core.tle_fetcher", "core.tle_fetcher"):
        importlib.import_module(name).DATA_DIR = Path(_tmp_cache)


def pytest_unconfigure(config):
    if _tmp_cache:
        shutil.rmtree(_tmp_cache, ignore_errors=True)
