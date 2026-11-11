# Fase 3 — Corte + Formato Vertical 9:16: Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** transformar cada momento em um MP4 vertical 1080x1920 (facecam em cima + gameplay embaixo, ou recorte seguindo o rosto, ou fundo desfocado), com regiões detectadas automaticamente, ajustáveis na interface e salvas por streamer.

**Architecture:** `detect.py` amostra ~24 quadros do VOD (ffmpeg → numpy) e encontra (a) câmeras = rostos estáveis no tempo (YuNet/OpenCV) expandidos até a moldura e (b) a janela da gameplay = maior região com movimento. `presets.py` guarda as regiões normalizadas (0–1) por streamer. `layout.py` monta o filtergraph do FFmpeg (função pura, testável). `render.py` corta e codifica com NVENC (fallback libx264), com cache por clipe. API/UI ganham editor de regiões, "Gerar clipe" e player vertical.

**Tech Stack:** OpenCV 5 (`FaceDetectorYN` + modelo YuNet 230 KB em `models/face/`, fallback Haar embutido), numpy, FFmpeg (filtros crop/scale/vstack/boxblur, `h264_nvenc`), FastAPI, JS.

**Spec:** `docs/superpowers/specs/2026-11-11-corte-viral-design.md` (seções 6 e 12)

## Global Constraints

- Saída: 1080x1920, 30 fps (ou fps de origem se ≤ 30), H.264 yuv420p + AAC 160k, `-movflags +faststart`.
- Layout `split`: câmera em cima com `facecam_ratio = 0.30` (576 px), divisória 6 px (`#000000`, configurável), gameplay embaixo (1338 px). Recortes preenchem a área (cover), sem barras pretas.
- Layout `face-follow`: recorte 9:16 do quadro inteiro acompanhando o rosto (amostra a cada 1 s, média móvel exponencial α=0.3, zona morta 4 % da largura).
- Layout `fallback`: quadro (ou janela da gameplay) com largura 1080 centralizado sobre o próprio vídeo ampliado e desfocado. Usado automaticamente quando não há câmera detectada nem preset.
- Regiões sempre normalizadas 0–1 (`[x, y, w, h]`) — independem da resolução do VOD.
- Preset por streamer em `presets/streamers/<slug>.json` (versionável, sem dados pessoais): `{facecam, gameplay, layout, credit_handle, authorized}`. Projeto guarda `streamer` (vem do canal no yt-dlp; editável).
- Encoder: `h264_nvenc -preset p5 -cq 23`; se falhar, `libx264 -preset veryfast -crf 21` com aviso. Prévia: 540x960, `-cq 30`.
- Cache por clipe: `clips/<id>.mp4` + `clips/<id>.json` com hash de (início, fim, layout, regiões, config de render); mesmo hash → não re-renderiza.
- Arquivos de modelo em `models/` (ignorado pelo git).

## Review Focus

1. **Vários rostos estáveis (react com 4 câmeras)** → todas viram opções; a padrão é a de maior confiança; o usuário escolhe na UI. Teste em Task 1.
2. **Rosto de personagem do jogo aparecendo às vezes** → não vira câmera (precisa aparecer em ≥ 50 % dos quadros na mesma posição). Teste em Task 1.
3. **VOD sem câmera (só gameplay)** → layout `fallback` automático, sem erro. Teste em Task 3/4.
4. **Região salva inválida (fora de 0–1, largura zero)** → recortada para dentro do quadro / rejeitada pela API com 422. Teste em Task 2.
5. **NVENC indisponível ou ocupado** → cai para libx264 e conclui. Teste em Task 4.

---

### Task 1: Detecção de câmeras e da janela da gameplay

**Files:** Create `corteviral/vision/__init__.py`, `corteviral/vision/frames.py`, `corteviral/vision/detect.py`, `tests/test_detect.py`; Modify `pyproject.toml` (`opencv-python-headless>=4.9`).

**Interfaces:**
- `frames.grab(video: Path, times: list[float], width: int = 640) -> list[np.ndarray]` (BGR, via ffmpeg rawvideo; tempos inválidos ignorados).
- `detect.FaceBox = tuple[float, float, float, float, float]` (x, y, w, h normalizados, confiança).
- `detect.cluster_faces(per_frame: list[list[FaceBox]], min_share: float = 0.5, tol: float = 0.04) -> list[dict]` → `[{"face": [x,y,w,h], "share": float, "conf": float}]` ordenado por (share, conf) desc.
- `detect.cam_rect(face: list[float]) -> list[float]` → retângulo da câmera: largura 3.5×, altura 2.2× a do rosto, centrado no rosto (rosto a 45 % da altura), limitado a 0–1.
- `detect.motion_region(frames) -> list[float] | None` → bbox da maior componente com desvio-padrão temporal alto (limiar Otsu), ignorando áreas < 15 % do quadro.
- `detect.analyze_layout(video: Path, duration: float, models_dir: Path) -> dict` → `{"cams": [{"rect", "face", "share", "conf"}], "gameplay": [x,y,w,h] | None, "source": [w, h]}`.

- [ ] Testes: `test_cluster_keeps_stable_faces_only` (4 posições fixas em 10/10 quadros + 1 rosto aleatório em 2/10 → 4 clusters); `test_cluster_orders_by_share_then_conf`; `test_cam_rect_expands_and_clamps` (rosto na borda direita → rect dentro de 0–1); `test_motion_region_finds_moving_window` (quadros sintéticos: fundo fixo + retângulo de ruído em 10–80 % × 15–95 % → bbox ≈ esse, tol 3 %); `test_grab_returns_frames_from_sample_video`.
- [ ] Ver falhar → implementar → ver passar → commit `feat: detecta câmeras e janela da gameplay no VOD`.

### Task 2: Presets por streamer

**Files:** Create `corteviral/presets.py`, `tests/test_presets.py`; Modify `corteviral/pipeline/ingest.py` (guardar `channel` do yt-dlp em `project.data["streamer"]`), `config.py` (`RenderSettings`).

**Interfaces:**
- `Region = list[float]` validado (4 números, w,h > 0.01, recortado a 0–1).
- `LayoutPreset(BaseModel)`: `layout: Literal["split","face-follow","fallback"]="split"`, `facecam: Region | None`, `gameplay: Region | None`, `credit_handle: str = ""`, `authorized: bool = False`.
- `load_preset(presets_dir, streamer) -> LayoutPreset | None`; `save_preset(presets_dir, streamer, preset)`; `resolve_layout(project, settings) -> LayoutPreset` (preset do streamer → senão detecção automática salva em `project.data["layout_detect"]` → senão `fallback`).
- `RenderSettings`: `width=1080, height=1920, fps=30, facecam_ratio=0.30, divider_px=6, divider_color="#000000", encoder="h264_nvenc", cq=23, audio_bitrate="160k", presets_dir="presets/streamers"`.

- [ ] Testes: `test_region_clamped_and_validated`; `test_save_load_roundtrip_by_slug` ("Flow Games" → `flow-games.json`); `test_resolve_prefers_preset_then_detection_then_fallback`; `test_ingest_stores_streamer_from_channel`.
- [ ] Ver falhar → implementar → ver passar → commit `feat: presets de layout por streamer`.

### Task 3: Filtergraph dos layouts

**Files:** Create `corteviral/pipeline/layout.py`, `tests/test_layout.py`.

**Interfaces:**
- `cover_crop(region_px: tuple[int,int,int,int], target_aspect: float) -> tuple[int,int,int,int]` — maior recorte com o aspecto alvo dentro da região, centrado (pares).
- `build_filter(preset: LayoutPreset, src: tuple[int,int], rs: RenderSettings, track: list[tuple[float,float]] | None = None, preview: bool = False) -> str` — filtergraph com saída `[v]`.
- `follow_expr(track, src_w, crop_w) -> str` — expressão `x(t)` interpolada linearmente entre amostras suavizadas.

- [ ] Testes: `test_cover_crop_keeps_aspect_and_inside`; `test_split_filter_dimensions` (soma das alturas = 1920; câmera 576; contém `vstack`); `test_fallback_filter_has_blur_and_overlay`; `test_follow_expr_smooths_and_clamps`; `test_preview_scales_to_540x960`.
- [ ] Ver falhar → implementar → ver passar → commit `feat: monta filtros FFmpeg dos layouts verticais`.

### Task 4: Renderização

**Files:** Create `corteviral/pipeline/render.py`, `tests/test_render.py`.

**Interfaces:**
- `render_clip(project, moment: dict, settings, preview=False, on_progress=None) -> Path` — corta `[start, end]` do vídeo, aplica layout, codifica; cache por hash; NVENC → libx264.
- `render_all(project, settings, ids: list[str] | None = None, preview=False, on_progress=None) -> list[Path]`.

- [ ] Testes (ffmpeg real, vídeo sintético 8 s): `test_render_split_outputs_1080x1920_with_duration` (momento 1–6 s → 5 s ± 0.2, 1080x1920, tem áudio); `test_render_fallback_without_cam`; `test_render_cached_second_time`; `test_nvenc_failure_falls_back_to_x264` (encoder forçado inválido → conclui com libx264); `test_preview_is_540x960`.
- [ ] Ver falhar → implementar → ver passar → commit `feat: renderiza clipes verticais 9:16`.

### Task 5: CLI, API e interface

**Files:** Modify `corteviral/cli.py` (`detect`, `render`), `corteviral/server/app.py`, `server/static/index.html`, `README.md`; Create tests em `tests/test_server.py`, `tests/test_cli.py`.

**Interfaces/rotas:**
- `GET /api/projects/{name}/frame?t=` → JPEG 960 px (para o editor).
- `GET /api/projects/{name}/layout` → `{preset, detected, streamer}` (roda detecção se ainda não houver); `PUT /api/projects/{name}/layout` → salva `LayoutPreset` no streamer (422 em região inválida); `PUT /api/projects/{name}/streamer`.
- Job `kind: "render"` `{project, ids?, preview?}`; `GET /api/projects/{name}/clips/{id}` → MP4 (Range).
- UI: painel "Layout do vertical" com quadro do vídeo e dois retângulos arrastáveis/redimensionáveis (Câmera em amarelo, Gameplay em azul), lista de câmeras detectadas para escolher, seletor de layout, prévia ao vivo do recorte 9:16; em cada momento: "Gerar clipe" / "Ver clipe" (player vertical); botão "Gerar todos".
- Corrige o adiado da Fase 2: mudar só "Quantos" não repontua — `moments.json` guarda todos os momentos pontuados (`all`) e a seleção é refeita sem IA.

- [ ] Testes: `test_api_layout_detect_and_save`, `test_api_layout_rejects_invalid_region`, `test_api_frame_jpeg`, `test_api_render_job_and_clip_endpoint`, `test_cli_render`, `test_changing_n_does_not_call_llm`.
- [ ] Ver falhar → implementar → ver passar → commit `feat: editor de layout e geração de clipes na interface`.
- [ ] Verificação real: VOD do Flow Games → detectar (4 câmeras + janela), gerar 3 clipes, conferir visualmente um quadro de cada.
- [ ] Revisão final → correções → push.
