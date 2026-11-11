from corteviral.cli import main
from tests.conftest import FakeModel, write_cfg


def test_cli_ingest_runs_both_stages(sample_video, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("corteviral.pipeline.transcribe.WhisperModel", FakeModel.ok)
    cfg = write_cfg(tmp_path)
    assert main(["ingest", str(sample_video), "--config", str(cfg)]) == 0
    out = capsys.readouterr().out
    assert "transcript.json" in out
    assert "3 palavras" in out


def test_cli_error_exit_code(tmp_path, capsys):
    assert main(["ingest", r"C:\nao\existe.mp4", "--config", str(write_cfg(tmp_path))]) == 1
    assert "não encontrado" in capsys.readouterr().err


def test_cli_model_override_and_force(sample_video, tmp_path, monkeypatch, capsys):
    created = []

    def factory(model, **kw):
        created.append(model)
        return FakeModel.ok(model, **kw)

    monkeypatch.setattr("corteviral.pipeline.transcribe.WhisperModel", factory)
    cfg = write_cfg(tmp_path)
    main(["ingest", str(sample_video), "--config", str(cfg), "--model", "small"])
    main(["ingest", str(sample_video), "--config", str(cfg), "--model", "small"])
    assert created == ["small"]  # segunda vez veio do cache
    main(["ingest", str(sample_video), "--config", str(cfg), "--model", "small", "--force"])
    assert created == ["small", "small"]


def test_cli_info(sample_video, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("corteviral.pipeline.transcribe.WhisperModel", FakeModel.ok)
    cfg = write_cfg(tmp_path)
    main(["ingest", str(sample_video), "--config", str(cfg)])
    proj = next((tmp_path / "projects").iterdir())
    capsys.readouterr()
    assert main(["info", str(proj)]) == 0
    out = capsys.readouterr().out
    assert "transcribe" in out and "pt" in out
