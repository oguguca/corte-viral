"""Filtros do FFmpeg para os layouts verticais (funções puras, sem executar nada)."""
from __future__ import annotations

from corteviral.config import RenderSettings
from corteviral.presets import LayoutPreset

DEADZONE = 0.04   # fração da largura: movimento menor que isso não mexe o recorte
ALPHA = 0.3       # suavização (média móvel exponencial) do acompanhamento do rosto


def _even(v: float) -> int:
    return max(2, int(round(v / 2)) * 2)


def _px(region: list[float] | None, src: tuple[int, int]) -> tuple[int, int, int, int]:
    sw, sh = src
    if not region:
        return 0, 0, sw, sh
    x, y, w, h = region
    return int(x * sw), int(y * sh), int(w * sw), int(h * sh)


def cover_crop(region: tuple[int, int, int, int], aspect: float) -> tuple[int, int, int, int]:
    """Maior recorte com o aspecto dado, centrado na região (dimensões pares)."""
    x, y, w, h = region
    if w / h > aspect:
        ch = h - h % 2
        cw = _even(min(w, ch * aspect))
        cw = min(cw, w - w % 2)
    else:
        cw = w - w % 2
        ch = _even(min(h, cw / aspect))
        ch = min(ch, h - h % 2)
    cx = x + (w - cw) // 2
    cy = y + (h - ch) // 2
    return cx, cy, cw, ch


def _crop(c: tuple[int, int, int, int]) -> str:
    x, y, w, h = c
    return f"crop={w}:{h}:{x}:{y}"


def follow_expr(track: list[tuple[float, float]], src_w: int, crop_w: int) -> str:
    """Expressão x(t) do recorte que acompanha o rosto: zona morta + suavização + interpolação linear."""
    if not track:
        return str((src_w - crop_w) // 2)
    pos = track[0][1]
    pts = []
    for t, cx in track:
        if abs(cx - pos) > DEADZONE:
            pos += ALPHA * (cx - pos)
        x = min(max(pos * src_w - crop_w / 2, 0.0), float(src_w - crop_w))
        pts.append((float(t), x))
    expr = f"{pts[-1][1]:.1f}"
    for (t0, x0), (t1, x1) in reversed(list(zip(pts, pts[1:]))):
        span = max(t1 - t0, 1e-3)
        expr = f"if(lt(t,{t1:.2f}),{x0:.1f}+({x1:.1f}-{x0:.1f})*(t-{t0:.2f})/{span:.2f},{expr})"
    return expr


def build_filter(preset: LayoutPreset, src: tuple[int, int], rs: RenderSettings,
                 track: list[tuple[float, float]] | None = None, preview: bool = False) -> str:
    k = 0.5 if preview else 1.0
    W, H = _even(rs.width * k), _even(rs.height * k)
    tail = f",fps={rs.fps},format=yuv420p[v]"
    layout = preset.layout if not (preset.layout == "split" and not preset.facecam) else "fallback"

    if layout == "split":
        cam_h = _even(H * rs.facecam_ratio)
        div = _even(rs.divider_px * k) if rs.divider_px else 0
        game_h = H - cam_h - div
        cam = cover_crop(_px(preset.facecam, src), W / cam_h)
        game = cover_crop(_px(preset.gameplay, src), W / game_h)
        color = rs.divider_color.replace("#", "0x")
        return (f"[0:v]split=2[a][b];"
                f"[a]{_crop(cam)},scale={W}:{cam_h},setsar=1,pad={W}:{cam_h + div}:0:0:color={color}[top];"
                f"[b]{_crop(game)},scale={W}:{game_h},setsar=1[bot];"
                f"[top][bot]vstack=inputs=2{tail}")

    if layout == "face-follow":
        sw, sh = src
        crop_w = _even(sh * 9 / 16)
        return f"[0:v]crop={crop_w}:{sh}:'{follow_expr(track or [], sw, crop_w)}':0,scale={W}:{H},setsar=1{tail}"

    # fallback: conteúdo inteiro centralizado sobre ele mesmo ampliado e desfocado
    region = _crop(_px(preset.gameplay, src))
    return (f"[0:v]{region},split=2[g1][g2];"
            f"[g1]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},boxblur=20:2[bg];"
            f"[g2]scale={W}:-2,setsar=1[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2{tail}")
