#!/usr/bin/env python3
"""Tests for scripts/validate_socrates.py pure helpers (Task 8.3/8.5) — the
.env credential loader and the distinct-object id extraction. Offline; no
network and no live fetch (main() is never invoked)."""

import os
import sys

import pandas as pd
import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "backend"))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "scripts"))
import validate_socrates as runner  # noqa: E402


@pytest.fixture(autouse=True)
def _clean_env():
    """Remove OW_TEST_* keys the .env loader may set, so tests don't leak state."""
    yield
    for k in [k for k in os.environ if k.startswith("OW_TEST_")]:
        del os.environ[k]


class TestLoadDotenv:
    def test_missing_file_is_noop(self, tmp_path):
        runner._load_dotenv(str(tmp_path / "nope.env"))   # must not raise

    def test_basic_key_value(self, tmp_path):
        (tmp_path / ".env").write_text("OW_TEST_A=hello\n")
        runner._load_dotenv(str(tmp_path / ".env"))
        assert os.environ["OW_TEST_A"] == "hello"

    def test_inline_comment_stripped_when_unquoted(self, tmp_path):
        # the pass-3 fix: a trailing ' # note' must not become part of the value
        (tmp_path / ".env").write_text("OW_TEST_B=secret # work note\n")
        runner._load_dotenv(str(tmp_path / ".env"))
        assert os.environ["OW_TEST_B"] == "secret"

    def test_quoted_value_preserves_hash(self, tmp_path):
        # a real password containing '#' must survive if quoted
        (tmp_path / ".env").write_text('OW_TEST_C="pa#ss word"\n')
        runner._load_dotenv(str(tmp_path / ".env"))
        assert os.environ["OW_TEST_C"] == "pa#ss word"

    def test_special_chars_unquoted_intact(self, tmp_path):
        # the real password shape (no space/#) is untouched
        (tmp_path / ".env").write_text("OW_TEST_F=Beetlejuice12!!\n")
        runner._load_dotenv(str(tmp_path / ".env"))
        assert os.environ["OW_TEST_F"] == "Beetlejuice12!!"

    def test_does_not_override_existing_env(self, tmp_path, monkeypatch):
        monkeypatch.setenv("OW_TEST_D", "already-set")
        (tmp_path / ".env").write_text("OW_TEST_D=from-file\n")
        runner._load_dotenv(str(tmp_path / ".env"))
        assert os.environ["OW_TEST_D"] == "already-set"

    def test_comments_and_blanks_ignored(self, tmp_path):
        (tmp_path / ".env").write_text("# a comment\n\nOW_TEST_E=v\nnokey\n")
        runner._load_dotenv(str(tmp_path / ".env"))
        assert os.environ["OW_TEST_E"] == "v"


class TestDistinctIds:
    def test_unique_pair_ids(self):
        df = pd.DataFrame({"norad_id_1": [1, 2, 1], "norad_id_2": [2, 3, 2]})
        assert sorted(runner._distinct_ids(df)) == [1, 2, 3]

    def test_ids_are_python_ints(self):
        df = pd.DataFrame({"norad_id_1": [25544], "norad_id_2": [12345]})
        assert all(isinstance(i, int) for i in runner._distinct_ids(df))
