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
