"""Legendas no estilo TikTok em ASS: 1–3 palavras por vez, palavra falada destacada com "pop"."""
from __future__ import annotations

from corteviral.config import CaptionStyle

PAUSE = 0.35            # s de silêncio que quebra o grupo
HOLD = 0.3              # s que o último grupo fica na tela depois da fala
JOIN_GAP = 0.6          # grupos mais próximos que isso ficam colados (sem piscar)
FRAME_PAD = 20  # px de folga para a legenda nunca encostar na borda do vídeo
SAFE_TOP, SAFE_BOTTOM, SAFE_LEFT, SAFE_RIGHT = 210, 1440, 140, 140  # zona segura em 1080x1920 (simétrica: legenda centralizada e longe dos botões laterais)
_SENTENCE_END = (".", "!", "?", "…")


def ass_time(t: float) -> str:
    cs = max(0, int(round(t * 100)))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def ass_escape(text: str) -> str:
    # o libass não aceita "\\" como barra literal (e "\N" quebraria a linha): usa uma barra parecida
    return text.replace("\\", "⧵").replace("{", r"\{").replace("}", r"\}")


def ass_color(hex_color: str) -> str:
    h = hex_color.lstrip("#")
    r, g, b = h[0:2], h[2:4], h[4:6]
    return f"&H00{b}{g}{r}".upper()


def clip_words(words, start: float, end: float) -> list[dict]:
    """Palavras do trecho [start, end) com tempos relativos ao início do clipe."""
    out = []
    for w in words:
        ws = w["start"] if isinstance(w, dict) else w.start
        we = w["end"] if isinstance(w, dict) else w.end
        text = w["w"] if isinstance(w, dict) else w.w
        if start <= ws < end:
            out.append({"w": text, "start": round(ws - start, 3), "end": round(min(we, end) - start, 3)})
    return out


def chunk_words(words: list[dict], max_words: int, max_chars: int) -> list[list[dict]]:
    chunks: list[list[dict]] = []
    cur: list[dict] = []
    for w in words:
        if cur:
            too_many = len(cur) >= max_words
            too_long = len(" ".join(x["w"] for x in cur + [w])) > max_chars
            paused = w["start"] - cur[-1]["end"] >= PAUSE
            if too_many or too_long or paused:
                chunks.append(cur)
                cur = []
        cur.append(w)
        if w["w"].rstrip().endswith(_SENTENCE_END):
            chunks.append(cur)
            cur = []
    if cur:
        chunks.append(cur)
    return chunks


def _header(style: CaptionStyle, width: int, height: int) -> str:
    return "\n".join([
        "[Script Info]", "ScriptType: v4.00+", f"PlayResX: {width}", f"PlayResY: {height}",
        "WrapStyle: 0", "ScaledBorderAndShadow: yes", "",
        "[V4+ Styles]",
        "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, "
        "Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
        "Alignment, MarginL, MarginR, MarginV, Encoding",
        f"Style: Default,{style.font},{style.size},{ass_color(style.color)},{ass_color(style.highlight)},"
        f"{ass_color(style.outline_color)},&H80000000,-1,0,0,0,100,100,0,0,1,{style.outline},{style.shadow},"
        f"2,{SAFE_LEFT},{SAFE_RIGHT},0,1",
        # gancho: texto branco em caixa preta (BorderStyle 3) no topo da gameplay
        f"Style: Hook,{style.font},{round(style.size * HOOK_SCALE)},&H00FFFFFF,&H00FFFFFF,&H00000000,"
        f"&H00000000,-1,0,0,0,100,100,0,0,3,14,0,8,{SAFE_LEFT},{SAFE_RIGHT},0,1",
        # crédito discreto no canto inferior direito da zona segura
        f"Style: Credit,{style.font},{CREDIT_SIZE},&H30FFFFFF,&H30FFFFFF,&H00000000,&H00000000,"
        f"-1,0,0,0,100,100,0,0,1,2,0,3,{SAFE_LEFT},{SAFE_RIGHT},0,1",
        "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ])


CHAR_W = 0.56  # largura média de caractere (fração do tamanho) em MAIÚSCULAS de fonte pesada + contorno (medido)


HOOK_SCALE = 0.8      # gancho um pouco menor que a legenda
HOOK_SECONDS = 3.0
HOOK_TOP = {"split": 640, "default": 300}  # topo da área da gameplay (abaixo da câmera no split)
CREDIT_SIZE = 36


def credit_y(caption_band: tuple[float, float] | None) -> float:
    """Base do crédito (em 1080x1920). Fica no lugar de sempre; se a legenda estiver ali, sai da frente."""
    cy = float(SAFE_BOTTOM)
    if caption_band:
        ctop, cbot = caption_band
        if cy > ctop - 8 and cy - CREDIT_SIZE < cbot + 8:  # encostaria na legenda
            below = cbot + 8 + CREDIT_SIZE
            cy = below if below <= 1920 - FRAME_PAD else ctop - 8
    return cy


def _overlay_events(style: CaptionStyle, hook: str | None, credit: str | None, duration: float | None,
                    layout: str, sx: float, sy: float, hook_top: float | None = None,
                    caption_band: tuple[float, float] | None = None) -> list[str]:
    out = []
    x = round((SAFE_LEFT + (1080 - SAFE_RIGHT)) / 2 * sx)
    safe_w = (1080 - SAFE_LEFT - SAFE_RIGHT) * sx
    if hook and hook.strip():
        text = hook.strip().upper() if style.uppercase else hook.strip()
        size = style.size * HOOK_SCALE * sy
        est = len(text) * size * CHAR_W
        scale = min(100, int(2 * safe_w / est * 100)) if est else 100  # cabe em até 2 linhas
        fit = rf"\fscx{scale}\fscy{scale}" if scale < 100 else ""
        top = round((hook_top if hook_top is not None else HOOK_TOP.get(layout, HOOK_TOP["default"])) * sy)
        end = min(HOOK_SECONDS, duration) if duration else HOOK_SECONDS
        out.append(f"Dialogue: 1,{ass_time(0)},{ass_time(end)},Hook,,0,0,0,,"
                   "{" + rf"\an8\pos({x},{top}){fit}" + "}" + ass_escape(text))
    credit = " ".join((credit or "").split())  # sem quebras de linha (não pode criar linhas novas no .ass)
    if credit and duration:
        handle = credit if credit.startswith("@") else "@" + credit
        right = round((1080 - SAFE_RIGHT) * sx)
        out.append(f"Dialogue: 1,{ass_time(0)},{ass_time(duration)},Credit,,0,0,0,,"
                   "{" + rf"\an3\pos({right},{round(credit_y(caption_band) * sy)})" + "}" + ass_escape(handle))
    return out


def build_ass(words: list[dict], style: CaptionStyle, width: int = 1080, height: int = 1920,
              hook: str | None = None, credit: str | None = None, duration: float | None = None,
              layout: str = "split", hook_top: float | None = None) -> str:
    sx, sy = width / 1080, height / 1920
    size = style.size * sy
    x = round((SAFE_LEFT + (1080 - SAFE_RIGHT)) / 2 * sx)
    safe_w = (1080 - SAFE_LEFT - SAFE_RIGHT) * sx
    # ancorada pela base (\an2). Posição livre (o usuário escolhe), só não deixa a linha sair do vídeo.
    bottom = style.position * height + size / 2
    bottom = round(min(max(bottom, FRAME_PAD * sy + size), (1920 - FRAME_PAD) * sy))
    hl, prim = ass_color(style.highlight), ass_color(style.color)
    lines = [_header(style, width, height)]
    band = (bottom / sy - style.size * 1.25, bottom / sy) if words else None  # faixa ocupada pela legenda
    lines += _overlay_events(style, hook, credit, duration, layout, sx, sy, hook_top, band)
    for ev in caption_events(words, style):
        texts = [ass_escape(t) for t in ev["words"]]
        # grupo largo demais (ex.: palavra enorme): encolhe para caber, em vez de vazar pelas bordas
        est = len(" ".join(ev["words"])) * size * CHAR_W
        scale = min(100, int(safe_w / est * 100)) if est else 100
        fit = rf"\fscx{scale}\fscy{scale}" if scale < 100 else ""
        big = round(scale * 1.15)
        pop = rf"\fscx{big}\fscy{big}\t(0,80,\fscx{scale}\fscy{scale})" if style.pop else ""
        restore = rf"\c{prim}" + (rf"\fscx{scale}\fscy{scale}" if (style.pop or fit) else "")
        parts = ["{" + rf"\c{hl}{pop}" + "}" + t + "{" + restore + "}" if j == ev["hl"] else t
                 for j, t in enumerate(texts)]
        lines.append(f"Dialogue: 0,{ass_time(ev['start'])},{ass_time(ev['end'])},Default,,0,0,0,,"
                     "{" + rf"\an2\pos({x},{bottom}){fit}" + "}" + " ".join(parts))
    return "\n".join(lines) + "\n"


def caption_events(words: list[dict], style: CaptionStyle) -> list[dict]:
    """Quadros da legenda: o grupo de palavras na tela e qual delas está em destaque (hl), com início/fim.

    Fonte única para o .ass do vídeo final e para a prévia ao vivo da interface.
    """
    out = []
    chunks = chunk_words(words, style.max_words, style.max_chars)
    for ci, chunk in enumerate(chunks):
        nxt = chunks[ci + 1][0]["start"] if ci + 1 < len(chunks) else None
        last_end = chunk[-1]["end"]
        chunk_end = nxt if nxt is not None and nxt - last_end < JOIN_GAP else last_end + HOLD
        texts = [w["w"].upper() if style.uppercase else w["w"] for w in chunk]
        for i, w in enumerate(chunk):
            start = w["start"]
            end = chunk[i + 1]["start"] if i + 1 < len(chunk) else chunk_end
            if end > start:
                out.append({"start": round(start, 3), "end": round(end, 3), "words": texts, "hl": i})
    return out
