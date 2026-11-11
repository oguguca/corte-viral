# Fase 4 — Legendas Animadas: Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** queimar no clipe vertical legendas no estilo TikTok — 1 a 3 palavras por vez, palavra falada destacada em cor com "pop" de escala — opcionais em três níveis (config, geração, por clipe), com estilo editável na interface e texto corrigível por clipe.

**Architecture:** `captions.py` (puro) agrupa as palavras do trecho e gera o `.ass`; `caption_style.py` guarda o estilo global (`data/caption_style.json` sobre os padrões do config); `render.py` acrescenta o filtro `ass` ao filtergraph e inclui legenda+estilo no hash do cache; API/UI ganham liga/desliga, edição do texto e um painel de estilo com prévia no canvas 9:16 existente.

**Tech Stack:** ASS/libass (filtro `ass` do FFmpeg), fontes do Windows (Arial Black padrão; Impact, Segoe UI Black, Verdana, Bahnschrift), FastAPI, JS.

**Spec:** `docs/superpowers/specs/2026-11-11-corte-viral-design.md` (seções 6 — zona segura — e 7)

## Global Constraints

- Estilo padrão (`CaptionStyle`): `enabled=True`, `font="Arial Black"`, `size=78` (px em 1080x1920), `color="#FFFFFF"`, `highlight="#FFE600"`, `outline_color="#000000"`, `outline=6`, `shadow=2`, `uppercase=True`, `max_words=3`, `max_chars=20`, `position=0.70` (fração da altura, centro da linha), `pop=True`.
- Zona segura comum (spec seção 6): texto nunca acima de y=210 nem abaixo de y=1440 (em 1920), margem esquerda 60, direita 140. `position` é limitada a esse intervalo.
- Agrupamento: até `max_words` palavras e `max_chars` caracteres; quebra em pausa ≥ 0,35 s ou fim de frase (`.`, `!`, `?`, `…`); cada grupo fica na tela do início da 1ª palavra até o início do próximo grupo (ou fim da última palavra + 0,3 s, se o próximo estiver longe).
- Destaque: um evento ASS por palavra do grupo; a palavra ativa usa `highlight` e, com `pop`, entra com `\fscx115\fscy115` animando para 100 % em 80 ms.
- Tempos relativos ao início do clipe (o render corta com `-ss`), em formato ASS `H:MM:SS.cc`.
- Prévia (540x960): mesmo `.ass` com `PlayResX/PlayResY` de 1080x1920 (o libass escala).
- Liga/desliga: `moment["captions"]` (por clipe) > pedido de geração (`captions` no job) > `style.enabled`.
- Edição de texto: `moment["caption_words"]` (lista de `{w,start,end}`) substitui as palavras da transcrição naquele clipe; o texto editado é realinhado aos tempos originais (mesmo número de palavras → troca 1:1; senão distribui proporcionalmente no intervalo).
- Caminho do `.ass` no filtro: barras `/`, `:` escapado (`C\:/...`) e entre aspas simples.

## Review Focus

1. **Palavras com `{`, `}` ou `\`** (ASS interpreta como comando) → escapadas e exibidas literalmente. Teste em Task 1.
2. **Trecho sem fala** → clipe sai sem legenda e sem erro (arquivo `.ass` sem eventos ou filtro omitido). Teste em Task 3.
3. **Pasta do projeto com acento/espaço** (ex.: `C:\Users\João\Desktop\...`) → caminho do `.ass` escapado corretamente e o FFmpeg renderiza. Teste em Task 3.
4. **Palavra muito longa (> max_chars)** → vira grupo sozinha, sem quebrar o layout; quebra de linha automática do libass (WrapStyle 0) dentro das margens. Teste em Task 1.
5. **Texto editado com número diferente de palavras** → realinhado sem tempos invertidos ou sobrepostos. Teste em Task 2.

---

### Task 1: Gerador do `.ass`

**Files:** Create `corteviral/pipeline/captions.py`, `tests/test_captions.py`; Modify `corteviral/config.py` (`CaptionStyle` em `Settings.captions`).

**Interfaces:**
- `chunk_words(words: list[dict], max_words: int, max_chars: int) -> list[list[dict]]` (palavras com `w,start,end` relativas ao clipe).
- `ass_time(t: float) -> str`; `ass_escape(text: str) -> str`; `ass_color(hex: str) -> str` (`#RRGGBB` → `&H00BBGGRR`).
- `build_ass(words: list[dict], style: CaptionStyle, width=1080, height=1920) -> str`.
- `clip_words(transcript_words, start, end) -> list[dict]` (recorta e torna relativo).

- [ ] Testes: `test_chunk_respects_max_words_and_pauses`; `test_chunk_breaks_on_sentence_end`; `test_long_word_alone`; `test_ass_time_format` (`3725.456 → "1:02:05.46"`); `test_ass_color_bgr`; `test_ass_escape_braces_and_backslash`; `test_build_ass_one_event_per_word_with_highlight` (3 palavras → 3 `Dialogue`, cada uma com o `\c` de destaque só na ativa); `test_build_ass_position_clamped_to_safe_zone`; `test_build_ass_uppercase_option`; `test_clip_words_relative_times`.
- [ ] Ver falhar → implementar → ver passar → commit `feat: gera legendas ASS palavra por palavra`.

### Task 2: Estilo salvo e texto editado

**Files:** Create `corteviral/caption_style.py`, `tests/test_caption_style.py`.

**Interfaces:** `load_style(settings) -> CaptionStyle` (config + `data/caption_style.json`); `save_style(settings, style)`; `realign(original: list[dict], text: str) -> list[dict]`.

- [ ] Testes: `test_style_override_file_wins_over_config`; `test_save_style_validates`; `test_realign_same_count_keeps_times`; `test_realign_different_count_distributes_monotonic`; `test_realign_empty_text_returns_empty`.
- [ ] Ver falhar → implementar → ver passar → commit `feat: estilo de legenda salvo e texto editável`.

### Task 3: Legenda no render

**Files:** Modify `corteviral/pipeline/render.py`; Create `tests/test_render_captions.py`.

**Interfaces:** `render_clip(..., captions: bool | None = None)`; `render_all(..., captions=None)`; `ass_filter_path(path: Path) -> str`.

- [ ] Testes (FFmpeg real, vídeo sintético): `test_render_with_captions_burns_text` (quadro com legenda difere do sem legenda na faixa da legenda e é igual fora dela); `test_render_without_speech_has_no_caption_events`; `test_caption_path_with_accents_and_spaces` (projeto em `tmp/João Teste/...`); `test_caption_change_invalidates_cache`; `test_moment_toggle_overrides_job_and_style`.
- [ ] Ver falhar → implementar → ver passar → commit `feat: queima legendas animadas nos clipes`.

### Task 4: API e interface

**Files:** Modify `server/app.py` (`GET/PUT /api/caption-style`; `PUT /api/projects/{name}/moments/{id}` com `captions` e `caption_text`; job render aceita `captions`), `index.html` (painel "Legendas": liga/desliga global, fonte, tamanho, cor do destaque, cor do texto, posição, palavras por vez, maiúsculas; prévia desenhada no canvas 9:16; em cada momento: chave "Legenda" e "Editar legenda"), `cli.py` (`render --no-captions`), `README.md`, `config.example.yaml`.

- [ ] Testes: `test_api_caption_style_roundtrip_and_validation`; `test_api_moment_caption_toggle_and_text`; `test_api_render_job_captions_flag`; `test_cli_render_no_captions`.
- [ ] Ver falhar → implementar → ver passar → commit `feat: painel de legendas e edição por clipe na interface`.
- [ ] Verificação real: gerar 2 clipes do VOD do Flow Games com legenda; conferir quadros (posição na zona segura, destaque, acentos "ção").
- [ ] Revisão final → correções → push.
