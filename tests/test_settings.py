"""
Settings persistence tests — deep-merge semantics (so a new default key shows
up without wiping a user's saved value, and an unknown legacy key survives a
read) and a save → load round-trip.
"""

import settings


def test_deep_merge_overrides_nested_keeps_siblings():
    merged = settings._deep_merge(
        {"a": 1, "b": {"x": 1, "y": 2}}, {"b": {"y": 9}})
    assert merged == {"a": 1, "b": {"x": 1, "y": 9}}


def test_deep_merge_keeps_unknown_keys():
    # Old (RPi-era) keys are preserved on read rather than dropped.
    merged = settings._deep_merge({"a": 1}, {"legacy": "kept"})
    assert merged["legacy"] == "kept"


def test_load_returns_defaults_when_file_absent(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "SETTINGS_FILE", tmp_path / "nope" / "s.json")
    monkeypatch.setattr(settings, "_FALLBACK_FILE", tmp_path / "s.json")
    data = settings.load()
    assert data["splash"]["theme"] == "arcane"
    assert data["grid"]["size"] == 55


def test_save_then_load_roundtrip(tmp_path, monkeypatch):
    f = tmp_path / "settings.json"
    monkeypatch.setattr(settings, "SETTINGS_FILE", f)
    monkeypatch.setattr(settings, "_FALLBACK_FILE", f)
    settings.save({"splash": {"theme": "flame"}})
    loaded = settings.load()
    assert loaded["splash"]["theme"] == "flame"
    # Untouched sections still get their defaults merged in.
    assert loaded["grid"]["type"] == "square"
