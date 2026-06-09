"""
Path-containment guard tests (``paths.safe_resolve`` / ``is_within_any``).

This is the single security boundary every filesystem-touching endpoint
defers to, so a regression here is a traversal bug.  The sibling-prefix case
is the precise hole a naive ``str.startswith`` left open in the old
``/delete`` route — keep it.
"""

import pytest

from paths import safe_resolve, is_within_any


@pytest.fixture
def media(tmp_path):
    root = tmp_path / "dnd_media"
    (root / "Maps").mkdir(parents=True)
    (root / "Maps" / "a.png").write_text("x")
    return root


def test_accepts_legit_absolute(media):
    assert safe_resolve(media, str(media / "Maps" / "a.png")) == \
        (media / "Maps" / "a.png").resolve()


def test_accepts_legit_relative(media):
    assert safe_resolve(media, "Maps/a.png") == (media / "Maps" / "a.png").resolve()


def test_empty_path_resolves_to_root(media):
    assert safe_resolve(media, "") == media.resolve()


def test_rejects_relative_traversal(media):
    assert safe_resolve(media, "../../etc/passwd") is None


def test_rejects_absolute_traversal(media):
    # The /delete vuln: an absolute path under the root that '..'-escapes it.
    assert safe_resolve(media, str(media) + "/../../secret") is None


def test_rejects_sibling_prefix(media, tmp_path):
    # str.startswith() would WRONGLY accept this: ``…/dnd_media_evil`` shares
    # the ``…/dnd_media`` prefix but is a different directory.
    sibling = tmp_path / "dnd_media_evil"
    sibling.mkdir()
    (sibling / "x").write_text("y")
    assert safe_resolve(media, str(sibling / "x")) is None


def test_rejects_absolute_outside(media):
    assert safe_resolve(media, "/etc/hosts") is None


def test_is_within_any_hits_a_later_root(media, tmp_path):
    usb = tmp_path / "usb"
    usb.mkdir()
    f = usb / "f.mp4"
    f.write_text("z")
    assert is_within_any([media, usb], str(f)) == f.resolve()


def test_is_within_any_miss_returns_none(media, tmp_path):
    assert is_within_any([media], str(tmp_path / "elsewhere")) is None
