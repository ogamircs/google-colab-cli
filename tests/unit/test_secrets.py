from __future__ import annotations

import sys
from pathlib import Path

import pytest

from colab_cli.core.secrets import build_secrets_setup_code, parse_secrets_file
from colab_cli.errors import ConfigError


class TestParseSecretsFile:
    def test_basic_key_value(self, tmp_path: Path) -> None:
        f = tmp_path / ".env"
        f.write_text("API_KEY=abc123\nDB_HOST=localhost\n")
        assert parse_secrets_file(f) == {"API_KEY": "abc123", "DB_HOST": "localhost"}

    def test_comments_and_blank_lines_ignored(self, tmp_path: Path) -> None:
        f = tmp_path / ".env"
        f.write_text("# comment\n\nKEY=val\n  \n# another comment\n")
        assert parse_secrets_file(f) == {"KEY": "val"}

    def test_double_quoted_value(self, tmp_path: Path) -> None:
        f = tmp_path / ".env"
        f.write_text('KEY="hello world"\n')
        assert parse_secrets_file(f) == {"KEY": "hello world"}

    def test_single_quoted_value(self, tmp_path: Path) -> None:
        f = tmp_path / ".env"
        f.write_text("KEY='hello world'\n")
        assert parse_secrets_file(f) == {"KEY": "hello world"}

    def test_value_containing_equals(self, tmp_path: Path) -> None:
        f = tmp_path / ".env"
        f.write_text("KEY=a=b=c\n")
        assert parse_secrets_file(f) == {"KEY": "a=b=c"}

    def test_missing_equals_raises(self, tmp_path: Path) -> None:
        f = tmp_path / ".env"
        f.write_text("NOEQUALS\n")
        with pytest.raises(ConfigError, match="expected KEY=VALUE"):
            parse_secrets_file(f)

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="not found"):
            parse_secrets_file(tmp_path / "nonexistent")

    def test_empty_file(self, tmp_path: Path) -> None:
        f = tmp_path / ".env"
        f.write_text("")
        assert parse_secrets_file(f) == {}

    def test_whitespace_around_key_and_value(self, tmp_path: Path) -> None:
        f = tmp_path / ".env"
        f.write_text("  KEY  =  value  \n")
        assert parse_secrets_file(f) == {"KEY": "value"}


class TestBuildSecretsSetupCode:
    def test_basic_secret_accessible(self) -> None:
        code = build_secrets_setup_code({"MY_KEY": "my_value"})
        ns: dict = {}
        exec(code, ns)
        import google.colab.userdata as userdata
        assert userdata.get("MY_KEY") == "my_value"
        # Clean up sys.modules
        for mod in ["google.colab.userdata", "google.colab", "google"]:
            sys.modules.pop(mod, None)

    def test_missing_key_raises_key_error(self) -> None:
        code = build_secrets_setup_code({"A": "1"})
        ns: dict = {}
        exec(code, ns)
        import google.colab.userdata as userdata
        with pytest.raises(KeyError, match="MISSING"):
            userdata.get("MISSING")
        for mod in ["google.colab.userdata", "google.colab", "google"]:
            sys.modules.pop(mod, None)

    def test_empty_secrets_still_valid(self) -> None:
        code = build_secrets_setup_code({})
        ns: dict = {}
        exec(code, ns)
        import google.colab.userdata as userdata
        with pytest.raises(KeyError):
            userdata.get("ANY")
        for mod in ["google.colab.userdata", "google.colab", "google"]:
            sys.modules.pop(mod, None)

    def test_special_characters_in_value(self) -> None:
        code = build_secrets_setup_code({"KEY": "val'with\"quotes\nnewline"})
        ns: dict = {}
        exec(code, ns)
        import google.colab.userdata as userdata
        assert userdata.get("KEY") == "val'with\"quotes\nnewline"
        for mod in ["google.colab.userdata", "google.colab", "google"]:
            sys.modules.pop(mod, None)

    def test_multiple_secrets(self) -> None:
        code = build_secrets_setup_code({"A": "1", "B": "2", "C": "3"})
        ns: dict = {}
        exec(code, ns)
        import google.colab.userdata as userdata
        assert userdata.get("A") == "1"
        assert userdata.get("B") == "2"
        assert userdata.get("C") == "3"
        for mod in ["google.colab.userdata", "google.colab", "google"]:
            sys.modules.pop(mod, None)
