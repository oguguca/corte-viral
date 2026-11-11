import json
import subprocess

import pytest

from corteviral import ffmpeg
from corteviral.config import Settings


@pytest.fixture
def settings_tmp(tmp_path):
    s = Settings()
    s.paths.projects_dir = tmp_path / "projects"
    s.paths.models_dir = tmp_path / "models"
    return s


@pytest.fixture(scope="session")
def _sample_video_master(tmp_path_factory):
    """MP4 sintético de 8 s (barras de teste + tom 440 Hz), gerado uma vez por sessão."""
    out = tmp_path_factory.mktemp("media") / "vídeo teste ção.mp4"
    subprocess.run(
        [ffmpeg.binary("ffmpeg"), "-y", "-loglevel", "error",
         "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30:duration=8",
         "-f", "lavfi", "-i", "sine=frequency=440:duration=8",
         "-c:v", "libx264", "-preset", "ultrafast", "-c:a", "aac", "-shortest", str(out)],
        check=True,
    )
    return out


@pytest.fixture
def sample_video(_sample_video_master, tmp_path):
    """Cópia por teste (os testes podem criar hardlinks para ela)."""
    dst = tmp_path / _sample_video_master.name
    dst.write_bytes(_sample_video_master.read_bytes())
    return dst


def probe_audio(path):
    out = subprocess.run(
        [ffmpeg.binary("ffprobe"), "-v", "error", "-select_streams", "a:0",
         "-show_entries", "stream=sample_rate,channels", "-of", "json", str(path)],
        check=True, capture_output=True, text=True,
    ).stdout
    stream = json.loads(out)["streams"][0]
    return {"sample_rate": int(stream["sample_rate"]), "channels": int(stream["channels"])}


# --- Transcrição: modelo falso do faster-whisper ------------------------------
from types import SimpleNamespace

from corteviral.project import Project


def _fake_segments():
    words = [("Olha", 0.5, 0.8, 0.9), (" isso", 0.8, 1.1, 0.95), (" aqui!", 1.2, 1.9, 0.88)]
    yield SimpleNamespace(
        start=0.5, end=1.9, text=" Olha isso aqui!",
        words=[SimpleNamespace(word=w, start=s, end=e, probability=p) for w, s, e, p in words],
    )


class FakeModel:
    """Fábricas compatíveis com WhisperModel(model, device=..., compute_type=..., download_root=...)."""

    @staticmethod
    def ok(model, device="cuda", compute_type="int8", **kw):
        return SimpleNamespace(
            transcribe=lambda audio, **opts: (_fake_segments(), SimpleNamespace(language="pt", duration=8.0))
        )

    @staticmethod
    def explode(*a, **kw):
        raise AssertionError("o modelo não deveria ser carregado")

    @staticmethod
    def cuda_fails_on_first_inference(created: list):
        def factory(model, device="cuda", compute_type="int8", **kw):
            created.append((model, device))

            def transcribe(audio, **opts):
                def gen():
                    if device == "cuda":
                        raise RuntimeError("CUDA failed with error out of memory")
                    yield from _fake_segments()
                return gen(), SimpleNamespace(language="pt", duration=8.0)

            return SimpleNamespace(transcribe=transcribe)

        return factory


@pytest.fixture
def project_with_audio(settings_tmp):
    p = Project.create(settings_tmp.paths.projects_dir, "fake.mp4", "fake")
    p.path("audio.wav").write_bytes(b"RIFF")
    p.data["duration"] = 8.0
    p.save()
    return p


def write_cfg(tmp_path):
    """config.yaml mínimo apontando projects/models para dentro de tmp_path."""
    cfg = tmp_path / "config.yaml"
    projects = (tmp_path / "projects").as_posix()
    models = (tmp_path / "models").as_posix()
    cfg.write_text(f"paths:\n  projects_dir: '{projects}'\n  models_dir: '{models}'\n", encoding="utf-8")
    return cfg


@pytest.fixture(autouse=True)
def _no_real_gpu_preflight(monkeypatch, request):
    """Os testes rápidos nunca carregam o modelo real para testar a GPU."""
    if "slow" not in request.keywords:
        monkeypatch.setattr("corteviral.pipeline.transcribe._gpu_preflight", lambda settings: True)
