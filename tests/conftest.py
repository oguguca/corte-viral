import pytest

from corteviral.config import Settings


@pytest.fixture
def settings_tmp(tmp_path):
    s = Settings()
    s.paths.projects_dir = tmp_path / "projects"
    s.paths.models_dir = tmp_path / "models"
    return s
