"""Testes do parser de versão e fluxo do updater (sem rede real)."""
from __future__ import annotations

from core.updater import _parse_version, has_update, UpdateInfo


def test_parse_simple():
    assert _parse_version("1.0.0") == (1, 0, 0)


def test_parse_strips_v_prefix():
    assert _parse_version("v2.3.4") == (2, 3, 4)


def test_parse_handles_suffix():
    """1.0.0-rc1 deve virar (1,0,0) — não quebrar."""
    assert _parse_version("1.0.0-rc1") == (1, 0, 0)
    assert _parse_version("1.2.3-beta") == (1, 2, 3)


def test_parse_handles_garbage():
    assert _parse_version("abc") == (0,)
    assert _parse_version("1.x.3") == (1, 0, 3)


def test_version_ordering():
    assert _parse_version("1.0.10") > _parse_version("1.0.9")
    assert _parse_version("2.0.0") > _parse_version("1.99.99")
    assert _parse_version("1.0.1") > _parse_version("1.0")


def test_has_update_false_for_same_version():
    from version import __version__
    info = UpdateInfo(version=__version__, url="x")
    assert not has_update(info)


def test_has_update_true_for_higher():
    from version import __version__
    parts = list(_parse_version(__version__))
    parts[-1] += 1
    higher = ".".join(str(x) for x in parts)
    assert has_update(UpdateInfo(version=higher, url="x"))


def test_has_update_false_for_lower():
    info = UpdateInfo(version="0.0.1", url="x")
    assert not has_update(info)
