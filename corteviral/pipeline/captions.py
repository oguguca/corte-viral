"""Legendas no estilo TikTok em ASS: 1–3 palavras por vez, palavra falada destacada com "pop"."""
from __future__ import annotations

from corteviral.config import CaptionStyle

PAUSE = 0.35            # s de silêncio que quebra o grupo
HOLD = 0.3              # s que o último grupo fica na tela depois da fala
JOIN_GAP = 0.6          # grupos mais próximos que isso ficam colados (sem piscar)
SAFE_TOP, SAFE_BOTTOM, SAFE_LEFT, SAFE_RIGHT = 210, 1440, 60, 140  # zona segura em 1080x1920
_SENTENCE_END = (".", "!", "?", "…")


def ass_time(t: float) -> str:
    cs = max(0, int(round(t * 100)))
    h, rem = divmod(cs, 360000)
    m, rem = divmod(rem, 6000)
    s, cs = divmod(rem, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def ass_escape(text: str) -> str:
    return text.replace("\\", r"\\").replace("{", r"\{").replace("}", r"\}")


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
        f"5,{SAFE_LEFT},{SAFE_RIGHT},0,1",
        "", "[Events]",
        "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text",
    ])


def build_ass(words: list[dict], style: CaptionStyle, width: int = 1080, height: int = 1920) -> str:
    sy = height / 1920
    x = round((SAFE_LEFT + (width - SAFE_RIGHT)) / 2)
    y = round(min(max(style.position * height, SAFE_TOP * sy), SAFE_BOTTOM * sy))
    hl = ass_color(style.highlight)
    pop = r"\fscx115\fscy115\t(0,80,\fscx100\fscy100)" if style.pop else ""
    lines = [_header(style, width, height)]
    chunks = chunk_words(words, style.max_words, style.max_chars)
    for ci, chunk in enumerate(chunks):
        nxt = chunks[ci + 1][0]["start"] if ci + 1 < len(chunks) else None
        last_end = chunk[-1]["end"]
        chunk_end = nxt if nxt is not None and nxt - last_end < JOIN_GAP else last_end + HOLD
        texts = [ass_escape(w["w"].upper() if style.uppercase else w["w"]) for w in chunk]
        for i, w in enumerate(chunk):
            start = w["start"]
            end = chunk[i + 1]["start"] if i + 1 < len(chunk) else chunk_end
            if end <= start:
                continue
            parts = ["{" + rf"\c{hl}{pop}" + "}" + t + "{" + r"\r" + "}" if j == i else t
                     for j, t in enumerate(texts)]
            lines.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Default,,0,0,0,,"
                         "{" + rf"\pos({x},{y})" + "}" + " ".join(parts))
    return "\n".join(lines) + "\n"
