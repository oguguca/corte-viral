import shutil
import subprocess
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from corteviral.server.app import STATIC, create_app


def test_static_assets_served(settings_tmp):
    c = TestClient(create_app(settings_tmp))
    css = c.get("/static/css/app.css")
    js = c.get("/static/js/app.js")
    assert css.status_code == 200 and "text/css" in css.headers["content-type"]
    assert js.status_code == 200 and "javascript" in js.headers["content-type"]
    page = c.get("/").text
    assert 'type="module"' in page and "/static/js/app.js" in page and "/static/css/app.css" in page


def test_static_cannot_escape_folder(settings_tmp):
    c = TestClient(create_app(settings_tmp))
    assert c.get("/static/../app.py").status_code == 404


@pytest.mark.skipif(not shutil.which("node"), reason="node não instalado")
def test_js_modules_parse():
    files = sorted((Path(STATIC) / "js").rglob("*.js"))
    assert files
    for f in files:
        r = subprocess.run(["node", "--check", "--input-type=module"], input=f.read_text(encoding="utf-8"),
                           capture_output=True, text=True, encoding="utf-8")
        assert r.returncode == 0, f"{f.name}: {r.stderr[:400]}"


def test_classic_interface_removed(settings_tmp):
    c = TestClient(create_app(settings_tmp))
    assert c.get("/classico").status_code == 404
    assert "classico" not in (Path(STATIC) / "js" / "sheets" / "settings.js").read_text(encoding="utf-8")
