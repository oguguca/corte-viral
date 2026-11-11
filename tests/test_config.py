import pytest
from pydantic import ValidationError

from corteviral.config import load_settings


def test_defaults_when_file_missing(tmp_path):
    s = load_settings(tmp_path / "nao_existe.yaml")
    assert s.whisper.model == "medium"
    assert s.whisper.compute_type == "int8"
    assert s.whisper.cpu_fallback_model == "small"


def test_yaml_overrides(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("whisper:\n  model: small\n  language: pt\n", encoding="utf-8")
    s = load_settings(p)
    assert s.whisper.model == "small" and s.whisper.language == "pt"
    assert s.whisper.device == "cuda"


def test_unknown_key_rejected(tmp_path):
    p = tmp_path / "c.yaml"
    p.write_text("whisper:\n  modelo: small\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_settings(p)
