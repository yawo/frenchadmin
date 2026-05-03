"""Unit tests for crossreference.alias_detector."""

from unittest import mock

from crossreference import alias_detector
from crossreference.alias_detector import (
    CODE_FAMILY_MAP,
    extract_code_family_from_mention,
    infer_code_family,
    invalidate_extended_alias_cache,
)


def test_infer_code_family_core_cgi():
    family, alias, parents = infer_code_family(
        "article 1745 du code général des impôts"
    )
    assert family == "CGI"
    assert alias == "code general des impots"
    assert set(CODE_FAMILY_MAP["CGI"]["parent_text_ids"]).issubset(set(parents))


def test_infer_code_family_core_lpf():
    family, alias, parents = infer_code_family(
        "article L. 247 du livre des procédures fiscales"
    )
    assert family == "LPF"
    assert "LEGITEXT000006069583" in parents


def test_extract_code_family_from_mention_core():
    assert extract_code_family_from_mention("1745 du code général des impôts") == "CGI"
    assert extract_code_family_from_mention(
        "L. 247 du livre des procédures fiscales"
    ) == "LPF"


def test_infer_code_family_unknown_returns_none():
    family, alias, parents = infer_code_family("some random text")
    assert family is None
    assert alias is None
    assert parents == []


def test_extended_alias_cache_respects_length_cap(monkeypatch):
    """Simulate a catalog regression: if code_label is abnormally long the
    extended alias map must skip it instead of polluting detection.
    """
    invalidate_extended_alias_cache()

    long_label = "code du travail " + ("x" * 200)

    class _FakeCursor:
        def execute(self, sql):
            self._rows = [
                ("LEGITEXT000006072050", "code du travail"),
                ("LEGITEXT999999999999", long_label),
            ]

        def fetchall(self):
            return self._rows

    class _FakeConn:
        def cursor(self):
            return _FakeCursor()

        def __enter__(self):
            return self

        def __exit__(self, *_):
            return False

    monkeypatch.setattr(
        "database.database_manage.get_connection",
        lambda: _FakeConn(),
    )

    aliases = alias_detector._load_extended_aliases_from_catalog()
    assert "code du travail" in aliases
    assert all(len(k) <= alias_detector._EXTENDED_ALIAS_MAX_LEN for k in aliases)
    assert not any(k.startswith("code du travail x") for k in aliases)

    invalidate_extended_alias_cache()
