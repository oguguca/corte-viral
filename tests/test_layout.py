import re

from corteviral.config import RenderSettings
from corteviral.pipeline.layout import build_filter, cover_crop, follow_expr
from corteviral.presets import LayoutPreset

SRC = (1920, 1080)


def test_cover_crop_keeps_aspect_and_inside():
    x, y, w, h = cover_crop((1617, 201, 253, 196), 1080 / 576)
    assert abs(w / h - 1080 / 576) < 0.02
    assert 1617 <= x and x + w <= 1617 + 253 and 201 <= y and y + h <= 201 + 196
    assert w % 2 == 0 and h % 2 == 0
    x, y, w, h = cover_crop((0, 0, 1920, 1080), 1080 / 1338)
    assert h == 1080 and abs(x - (1920 - w) / 2) <= 2


def test_split_filter_dimensions():
    rs = RenderSettings()
    f = build_filter(LayoutPreset(facecam=[0.84, 0.18, 0.13, 0.18], gameplay=[0.016, 0.16, 0.81, 0.8]), SRC, rs)
    assert "vstack" in f and f.rstrip().endswith("[v]")
    assert "scale=1080:576" in f and "scale=1080:1338" in f
    assert "pad=1080:582" in f  # câmera + divisória de 6 px
    assert 576 + 6 + 1338 == 1920


def test_split_without_facecam_falls_back():
    f = build_filter(LayoutPreset(layout="split", facecam=None), SRC, RenderSettings())
    assert "boxblur" in f and "vstack" not in f


def test_fallback_filter_has_blur_and_overlay():
    f = build_filter(LayoutPreset(layout="fallback", gameplay=[0.016, 0.16, 0.81, 0.8]), SRC, RenderSettings())
    assert "boxblur" in f and "overlay" in f and "scale=1080:1920" in f


def test_follow_expr_smooths_and_clamps():
    track = [(0.0, 0.5), (1.0, 0.52), (2.0, 0.9), (3.0, 0.9), (4.0, 0.99)]
    expr = follow_expr(track, src_w=1920, crop_w=606)
    nums = [float(n) for n in re.findall(r"(?<![\w.])\d+(?:\.\d+)?", expr)]
    assert all(0 <= n <= 1920 for n in nums)
    assert "if(lt(t" in expr
    # zona morta: 0.50 → 0.52 não move; movimento para 0.9 é suavizado (não salta direto)
    first_x = 0.5 * 1920 - 303
    assert f"{first_x:.1f}" in expr
    assert f"{0.9 * 1920 - 303:.1f}" not in expr.split(",")[0]


def test_face_follow_filter_uses_expression():
    f = build_filter(LayoutPreset(layout="face-follow"), SRC, RenderSettings(), track=[(0, 0.4), (1, 0.6)])
    assert "crop=608:1080:'" in f and "scale=1080:1920" in f  # 1080*9/16=607.5 → par 608


def test_preview_scales_to_540x960():
    f = build_filter(LayoutPreset(facecam=[0.84, 0.18, 0.13, 0.18]), SRC, RenderSettings(), preview=True)
    assert "scale=540:288" in f and "pad=540:292" in f and "scale=540:668" in f  # alturas sempre pares


def test_cover_crop_anchor_keeps_face_area():
    region = (1617, 201, 253, 196)  # câmera: rosto fica a ~45 % da altura
    _, y_center, _, h = cover_crop(region, 1080 / 576)
    _, y_anch, _, h2 = cover_crop(region, 1080 / 576, anchor_y=0.42)
    assert h == h2 and y_anch < y_center  # sobe o recorte para não cortar a cabeça
    face_y = 201 + 0.42 * 196
    assert y_anch <= face_y - 0.4 * h2


def test_split_anchors_facecam_near_face():
    f = build_filter(LayoutPreset(facecam=[0.8, 0.2, 0.15, 0.2]), SRC, RenderSettings())
    cam_crop = re.search(r"\[a\]crop=(\d+):(\d+):(\d+):(\d+)", f).groups()
    w, h, x, y = map(int, cam_crop)
    region_top, region_h = int(0.2 * 1080), int(0.2 * 1080)
    assert y < region_top + (region_h - h) // 2  # acima do centro


def test_follow_expr_is_shallow_for_long_clips():
    track = [(float(t), 0.3 + 0.4 * (t % 7) / 7) for t in range(120)]
    expr = follow_expr(track, src_w=1920, crop_w=608)
    depth = max_depth = 0
    for ch in expr:
        depth += ch == "("
        depth -= ch == ")"
        max_depth = max(max_depth, depth)
    assert max_depth < 40  # o parser do FFmpeg recusa ~100 níveis


def test_follow_filter_runs_in_ffmpeg_for_120s_track():
    from corteviral import ffmpeg
    track = [(float(t), 0.2 + 0.6 * ((t * 37) % 100) / 100) for t in range(120)]
    graph = build_filter(LayoutPreset(layout="face-follow"), SRC, RenderSettings(), track=track, preview=True)
    ffmpeg.run(["-f", "lavfi", "-i", "color=c=gray:size=1920x1080:rate=10:duration=2",
                "-filter_complex", graph, "-map", "[v]", "-f", "null", "-"])
