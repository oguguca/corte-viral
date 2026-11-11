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


def test_cli_analyze(tmp_path, monkeypatch, capsys):
    from corteviral.project import Project
    cfg = write_cfg(tmp_path)
    p = Project.create(tmp_path / "projects", "x.mp4", "x")
    monkeypatch.setattr("corteviral.cli.analyze", lambda project, settings, n=None, profile=None, on_progress=None: [
        {"id": "m1", "start": 65.0, "end": 100.0, "score": 91, "reason": "explodiu o carro", "ai": True, "hook": "olha isso"}])
    assert main(["analyze", str(p.dir), "--config", str(cfg), "--n", "3", "--provider", "none"]) == 0
    out = capsys.readouterr().out
    assert "91" in out and "1:05" in out and "explodiu o carro" in out


def test_cli_render(tmp_path, monkeypatch, capsys):
    from corteviral.pipeline.analyze import MOMENTS
    from corteviral.project import Project
    from corteviral.utils import write_json_atomic
    cfg = write_cfg(tmp_path)
    p = Project.create(tmp_path / "projects", "x.mp4", "x")
    write_json_atomic(p.path(MOMENTS), {"moments": [{"id": "m1", "start": 0, "end": 3}, {"id": "m2", "start": 5, "end": 9}]})
    seen = {}

    def fake_render_all(project, settings, moments, preview=False, on_progress=None):
        seen.update(ids=[m["id"] for m in moments], preview=preview)
        return [project.path("clips") / f"{m['id']}.mp4" for m in moments]
    monkeypatch.setattr("corteviral.cli.render_all", fake_render_all)
    assert main(["render", str(p.dir), "--config", str(cfg), "--ids", "m2", "--preview"]) == 0
    assert seen == {"ids": ["m2"], "preview": True}
    assert "m2.mp4" in capsys.readouterr().out
