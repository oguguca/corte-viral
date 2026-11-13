import pytest
from fastapi.testclient import TestClient

from corteviral import ffmpeg
from corteviral.config import RenderSettings
from corteviral.divider_band import BandError, BandStore, DividerBand, band_box, visible_box


def _png(path, band=True):
    """PNG 1080×1920 transparente com uma faixa rosa de 1080×138 em y=506 (como a do usuário)."""
    bg = "color=c=black@0.0:s=1080x1920,format=rgba"
    graph = (f"{bg}[bg];color=c=0xff2d8a:s=1080x138,format=rgba[b];[bg][b]overlay=0:506:format=auto,format=rgba"
             if band else bg)
    ffmpeg.run(["-y", "-f", "lavfi", "-i", "nullsrc=s=16x16", "-filter_complex", graph, "-frames:v", "1", str(path)])
    return path


def test_visible_box_finds_the_band(tmp_path):
    assert visible_box(_png(tmp_path / "f.png")) == (1080, 138, 0, 506)


def test_fully_transparent_image_is_refused(tmp_path):
    with pytest.raises(BandError):
        visible_box(_png(tmp_path / "vazia.png", band=False))


def test_set_image_crops_and_turns_on(tmp_path):
    s = BandStore(tmp_path)
    b = s.set_image(_png(tmp_path / "f.png"))
    assert b.enabled and (b.w, b.h) == (1080, 138) and s.image.exists() and s.active()
    assert visible_box(s.image) == (1080, 138, 0, 0)  # guardou só a parte visível


def test_band_is_centered_on_the_divider_and_follows_adjustments():
    rs = RenderSettings()  # câmera 30 % (576 px) + divisa de 6 px → meio em 579
    x, y, w, h = band_box(DividerBand(enabled=True, w=1080, h=138), rs)
    assert (x, w, h) == (0, 1080, 138) and abs((y + h / 2) - 579) <= 1
    _, y2, _, _ = band_box(DividerBand(enabled=True, w=1080, h=138, offset=100), rs)
    assert y2 == y + 100
    x3, _, w3, h3 = band_box(DividerBand(enabled=True, w=1080, h=138, scale=0.5), rs)
    assert (w3, h3) == (540, 68) and x3 == 270  # metade, arredondada para par
    assert band_box(DividerBand(enabled=True, w=1080, h=138), rs, 0.5)[2] == 540  # prévia em 540


def test_render_with_band_and_hook_below_it(rs_band, sample_video):
    from corteviral.pipeline.render import hook_top_for, preview_spec, render_clip
    rs, p, m = rs_band
    rs.paths.data_dir.mkdir(parents=True, exist_ok=True)
    BandStore(rs.paths.data_dir).set_image(_png(rs.paths.data_dir / "f.png"))
    spec = preview_spec(p, m, rs)
    assert spec["band"]["w"] == 540 and spec["hook_top"] >= 506 + 138  # gancho abaixo da faixa
    assert hook_top_for("split", True, rs.render) < spec["hook_top"]
    out = render_clip(p, m, rs)
    assert out.exists() and ffmpeg.probe_duration(out) > 1
    BandStore(rs.paths.data_dir).save(offset=50)
    assert render_clip(p, m, rs).exists()  # mudou o ajuste: gera de novo sem erro


@pytest.fixture
def rs_band(settings_tmp, sample_video):
    from tests.test_export import _m, _project
    rs = settings_tmp
    rs.render.presets_dir = rs.paths.data_dir / "presets"
    rs.render.encoder = "libx264"
    rs.captions.enabled = False
    p = _project(rs, sample_video, [_m(1, 0.5, 3.0)])
    from corteviral.pipeline.analyze import load_moments
    return rs, p, load_moments(p)["moments"][0]


def test_api_band(settings_tmp, tmp_path):
    from corteviral.server.app import create_app
    c = TestClient(create_app(settings_tmp))
    assert c.get("/api/divider-band").json()["has_image"] is False
    assert c.put("/api/divider-band", json={"enabled": True}).status_code == 422  # sem imagem
    r = c.post("/api/divider-band/image", json={"path": str(_png(tmp_path / "f.png"))}).json()
    assert r["enabled"] and r["image_url"]
    assert c.get(r["image_url"]).headers["content-type"] == "image/png"
    r = c.put("/api/divider-band", json={"offset": -40, "scale": 1.2}).json()
    assert r["offset"] == -40 and r["scale"] == 1.2
    assert c.put("/api/divider-band", json={"scale": 9}).status_code == 422
    assert c.post("/api/divider-band/image", json={"path": str(tmp_path / "nao.txt")}).status_code == 422


def test_band_works_without_camera_layout_and_pushes_hook(tmp_path):
    from corteviral.pipeline.render import hook_top_for
    rs = RenderSettings()
    b = DividerBand(enabled=True, w=1080, h=138, offset=100)   # faixa em 610–748
    assert hook_top_for("fallback", False, rs) == 686
    assert hook_top_for("fallback", False, rs, b) == band_box(b, rs)[1] + 138 + 30  # gancho desce
    far = DividerBand(enabled=True, w=1080, h=138, offset=600)
    assert hook_top_for("fallback", False, rs, far) == 686  # faixa longe: gancho fica onde estava
