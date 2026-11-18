# Cortes de live ao vivo — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Colar o link de uma live em andamento e ver clipes prontos aparecendo na grade enquanto ela acontece.

**Architecture:** Um gravador (yt-dlp em subprocesso) grava a live em partes `.ts`; um monitor (thread fora da fila de tarefas) corta janelas de 5 min (+60 s de sobreposição) e processa cada janela como um **mini-projeto** com o pipeline que já existe (`transcribe` → `analyze` → seleção), anexa transcrição/momentos ao projeto principal com tempos da linha do tempo da live e gera o clipe com `render_clip` lendo o vídeo da janela (`moment["source"]`). No fim, as partes viram um `video.mp4` único (buracos com preto/silêncio) e os `source` saem.

**Tech Stack:** Python 3.11, yt-dlp (subprocesso), ffmpeg/ffprobe, FastAPI, pytest, JS ES modules.

**Spec:** `docs/superpowers/specs/2026-11-18-live-ao-vivo-design.md`

## Global Constraints
- Linha do tempo da live = segundos desde o início da **primeira** parte gravada; toda parte guarda seu `offset` nela (relógio de parede).
- Config `live:`: `window_seconds: 300`, `overlap_seconds: 60`, `max_retries: 20`, `retry_wait: 10` (dobra até 60), `max_clips_per_window: 3`, `review_per_window: 3`.
- Uma live ativa por vez. A live **nunca** ocupa `jobs.running` depois de criada.
- Janela só vira clipe se a análise dela for `complete` (IA avaliou). Senão: estado `pending_ai`, tentada de novo a cada ciclo sem refazer transcrição.
- Ids de momentos ao vivo: `L{n:03d}-{id}` (n = número da janela).
- Textos da interface em português; status: `🔴 Ao vivo · gravando H:MM:SS · N clipes`.

## Review Focus
- Live acaba sozinha (yt-dlp sai com código 0 e a plataforma diz que não está mais ao vivo) → finaliza, não tenta retomar 20 vezes. (Task 4)
- Janela atravessando o fim de uma parte (queda) → a janela é cortada no fim da parte; a próxima começa no início da parte nova. (Task 1)
- Momento da janela N que se sobrepõe a um momento da janela N−1 (sobreposição de 60 s) → não entra duas vezes. (Task 2)
- App fechado no meio → `live.json` em disco permite **Retomar**; janelas `done` não são refeitas. (Task 4/7)
- Processamento mais lento que a live → janelas acumulam e são processadas em ordem, uma por vez. (Task 4)

---

### Task 1: Configuração e estado da live

**Files:** Create `corteviral/live/__init__.py`, `corteviral/live/state.py`; Modify `corteviral/config.py`; Test `tests/test_live_state.py`

**Interfaces — Produces:**
- `LiveSettings` em config (`Settings.live`), campos acima.
- `Part(BaseModel)`: `file: str`, `offset: float`, `duration: float = 0.0`.
- `Window(BaseModel)`: `n: int`, `part: int`, `start: float`, `end: float` (linha do tempo), `state: Literal["pending","pending_ai","done","failed"] = "pending"`, `clips: list[str] = []`, `tries: int = 0`.
- `LiveState(BaseModel)`: `url`, `active: bool`, `opts: dict` (min_seconds, max_seconds, profile), `parts: list[Part]`, `windows: list[Window]`, `started_at: float`, `ended: bool = False`; `load(path) -> LiveState | None`, `save(path)`, `recorded_until() -> float` (offset+duration da última parte), `next_window(window: int, overlap: int) -> Window | None`.
- `next_window`: começa em `max(fim da última janela − overlap, offset da parte)` dentro da mesma parte; só devolve janela se a parte tiver `window` segundos novos a partir do fim da última janela, **ou** se a parte já terminou (não é a última) e sobra ≥ 20 s; nunca atravessa duas partes.

- [ ] Testes: `test_first_window_needs_full_window` (parte com 299 s → None; 300 s → Window(0, 0, 300)); `test_next_window_overlaps_previous` (após 0–300, parte com 600 s → 240–600); `test_window_never_crosses_parts` (parte 0 de 0–450 terminada + parte 1 offset 500 → janela 240–450 na parte 0, depois 500–800 na parte 1); `test_state_roundtrip` (save/load iguais); `test_live_settings_defaults`.
- [ ] Rodar → FAIL; implementar; rodar → PASS; commit `feat(live): configuração e estado da live (partes e janelas)`.

### Task 2: Processar uma janela (mini-projeto)

**Files:** Create `corteviral/live/window.py`; Test `tests/test_live_window.py`

**Interfaces — Consumes:** Task 1 (`Window`, `LiveState`). `transcribe(project, settings, on_progress)`, `analyze(project, settings, n, profile, on_progress)`, `load_moments`, `render_clip`, `Transcript`.
**Produces:**
- `cut_window(part_path: Path, part_offset: float, win: Window, dest: Path) -> Path` — cria `dest/` com `video.mp4` (corte **preciso**: re-encode `libx264 -preset ultrafast -crf 18`, áudio aac) e `audio.wav` (16 kHz mono, mesmo formato do ingest); devolve o vídeo.
- `window_project(main: Project, win: Window, video: Path) -> Project` — Project em `main.dir/live/win-{n:03d}` com `video_path`, `duration`, `ingest_params`, `streamer` e `layout_detect` copiados do principal.
- `process_window(main: Project, state: LiveState, win: Window, settings: Settings, *, transcribe_fn=transcribe, analyze_fn=analyze, render_fn=render_clip) -> list[dict]` — transcreve (se ainda não), anexa ao `transcript.json` principal só palavras/segmentos com início ≥ fim da janela anterior (tempo + `win.start`), roda `analyze_fn(wp, s_win, n=None, profile=...)` com `analysis.max_auto_clips = live.max_clips_per_window`, `deep.top_k = live.review_per_window`, faixa de duração de `state.opts`. Se `moments.json` da janela não estiver `complete` → `win.state = "pending_ai"`, devolve `[]`. Senão: desloca tempos por `win.start`, descarta os que se sobrepõem (> 30 %) a momentos existentes do principal, dá id `L{n:03d}-{id}`, `source = {"video": str(video), "offset": win.start}`, anexa a `moments` e `all` do principal (cria se não existir), renderiza cada um, `win.state = "done"`, `win.clips = ids`.

- [ ] Testes (fakes para transcribe/analyze/render; `cut_window` com vídeo sintético lavfi de 30 s): `test_cut_window_is_exact` (corte 10–20 → duração 10 ± 0,1 s, audio.wav existe); `test_process_window_appends_transcript_and_moments` (tempos deslocados por `win.start`, ids `L001-…`, `source` gravado, render chamado por clipe); `test_overlap_moment_not_duplicated`; `test_incomplete_analysis_marks_pending_ai_and_renders_nothing`; `test_overlap_words_not_duplicated_in_transcript`.
- [ ] FAIL → implementar → PASS → commit `feat(live): processa cada janela da live como mini-projeto`.

### Task 3: Clipe lê o vídeo da janela

**Files:** Modify `corteviral/pipeline/render.py` (`render_clip`, `preview_spec`), `corteviral/server/app.py` (`/frame` e `/video` de projeto ao vivo: usam o vídeo da última janela se não houver `video_path`); Test `tests/test_render.py`

**Interfaces — Produces:** Se `moment.get("source")`: vídeo = `source["video"]`, `start/end` de leitura = `moment["start"] − source["offset"]` (palavras da legenda continuam pela linha do tempo principal). Hash do cache inclui `source`.
- [ ] Teste `test_render_uses_moment_source_video` (vídeo sintético; momento 105–110 com source offset 100 → clipe de ~5 s, sem `video_path` no projeto). FAIL → implementar → PASS → commit `feat(live): clipe ao vivo é gerado a partir do vídeo da janela`.

### Task 4: Gravador e monitor

**Files:** Create `corteviral/live/recorder.py`, `corteviral/live/monitor.py`; Test `tests/test_live_monitor.py`

**Interfaces — Produces:**
- `Recorder(url, live_dir, settings, popen=subprocess.Popen)`: `start(n: int) -> Path` (sobe `python -m yt_dlp -f "b[height<=H]/b" --hls-use-mpegts --no-part -o live_dir/part-{n:03d}.ts URL`, gravando do agora — **sem** `--live-from-start`; cookies como em `_ydl_base_opts`), `running() -> bool`, `returncode`, `stop()`, `recorded(path) -> float` (ffprobe da duração do `.ts` crescendo; 0 se ainda não dá).
- `LiveMonitor(project, settings, recorder, process=process_window, is_live=probe_is_live, clock=time.time, sleep=time.sleep)`: `step() -> None` (um ciclo), `run()` (laço até parar), `stop()`. Estado em `project.dir / "live.json"`.
- `step`: atualiza `duration` da parte atual; se gravador caiu: `is_live(url)` falso → `ended=True`, finaliza (Task 5) e para; verdadeiro → nova parte com `offset = clock() − started_at` após `retry_wait` (dobra até 60), até `max_retries` seguidas sem gravar nada; processa **uma** janela por chamada: primeiro `pending_ai` antigas (em ordem), senão `next_window`.
- `probe_is_live(url, settings) -> bool` em `corteviral/pipeline/ingest.py` (reaproveita `_probe_url`).
- Registro único: `ACTIVE: dict[str, LiveMonitor]` em `monitor.py`; `start_monitor(project, settings, opts) -> LiveMonitor` recusa se já houver um ativo (`LiveError("já existe uma live sendo acompanhada")`).

- [ ] Testes com gravador/processo/relógio falsos: `test_step_processes_windows_in_order_one_per_step`; `test_recorder_crash_while_live_starts_new_part_with_wall_offset`; `test_live_ended_finalizes_without_retrying`; `test_pending_ai_retried_before_new_windows`; `test_only_one_live_at_a_time`; `test_state_survives_reload` (novo monitor lê `live.json`, não refaz janelas `done`).
- [ ] FAIL → implementar → PASS → commit `feat(live): gravador e monitor da live fora da fila de tarefas`.

### Task 5: Finalizar a live

**Files:** Create `corteviral/live/finalize.py`; Test `tests/test_live_finalize.py`

**Interfaces — Produces:** `finalize(project: Project, state: LiveState) -> Path` — junta as partes em `video.mp4` com buracos (`offset[i+1] − (offset[i]+duration[i])` > 0,5 s) preenchidos com preto + silêncio do mesmo tamanho (re-encode único), grava `video_path`, `duration`, extrai `audio.wav` (formato do ingest), remove `source` de todos os momentos, `state.active=False`, `ended=True`.
- [ ] Testes com 2 partes sintéticas (5 s, buraco de 3 s, 5 s): `test_finalize_fills_gap_and_keeps_timeline` (duração 13 ± 0,3 s; frame em 6,5 s é preto); `test_finalize_strips_sources_and_sets_video_path`.
- [ ] FAIL → implementar → PASS → commit `feat(live): no fim da live junta as partes num vídeo só`.

### Task 6: Servidor

**Files:** Modify `corteviral/server/app.py`; Test `tests/test_server.py`

**Interfaces — Consumes:** Tasks 4–5. **Produces:**
- Job `ingest`: se `is_url(source)` e `probe_is_live` → cria o projeto (título da live), grava `live.json` com `opts` do pedido, `start_monitor` (fábrica injetável `create_app(..., live_factory=None)`), job termina `done` com `job["live"] = True`, `project`.
- `GET /api/live` → `{"active": bool, "project", "recorded", "clips", "next_in", "state"}` do monitor ativo (ou de `live.json` inativo com `can_resume`).
- `POST /api/live/stop` → para o gravador, finaliza (em thread), 200.
- `POST /api/live/resume?project=` → se ainda ao vivo, novo monitor continuando o `live.json`.
- `/api/projects`: campo `live: bool` (live.json ativo).
- Job `analyze` com `merge: bool` (botão "Analisar a live inteira"): roda `analyze` normal (desvio do spec,
  decidido: reavalia a live inteira com a IA em vez de pular os já avaliados — mais simples; o usuário pediu a
  análise, então o gasto de cota é esperado) e depois junta: mantém todos os momentos anteriores (mesmos ids e campos), acrescenta os novos sem sobreposição > 30 %; com `auto`, renderiza só os novos.
- [ ] Testes: `test_ingest_live_starts_monitor_and_frees_job_slot` (fábrica falsa; segundo job logo depois não dá 409); `test_live_status_and_stop`; `test_projects_list_marks_live`; `test_analyze_merge_keeps_existing_moments`.
- [ ] FAIL → implementar → PASS → commit `feat(live): servidor inicia, mostra e para a live`.

### Task 7: Interface

**Files:** Modify `corteviral/server/static/js/views/clips.js`, `js/app.js`, `js/sidebar.js`, `js/sheets/newvideo.js`, `css/app.css`

- Clipes de projeto ao vivo: faixa `🔴 Ao vivo · gravando H:MM:SS · N clipes · próxima análise em X min` + **Parar** (confirma); polling de `/api/live` a cada 20 s enquanto a tela estiver aberta; quando `clips` aumenta, redesenha a grade e mostra toast `Novo clipe: <título>`.
- Live inativa com `can_resume` → faixa com **Retomar**. Live encerrada → botão **Analisar a live inteira** (`kind: "analyze", merge: true, auto: true`).
- Barra lateral: projeto com `live: true` mostra `🔴` no ícone e "ao vivo" no status.
- Novo vídeo: quando o job termina com `live`, toast `Live ao vivo: os clipes vão aparecendo enquanto ela acontece.` e abre a tela Clipes.
- [ ] Verificação: `node --check` em todos os JS + `pytest tests/test_ui_assets.py`; abrir no navegador um projeto com `live.json` falso ativo e conferir faixa, botões e ausência de erros no console. Commit `feat(live): tela mostra a live, novos clipes e os botões Parar/Retomar`.

### Task 8: Documentação e teste real

- README: seção "Live ao vivo" (como usar, limites: grava do agora, uma por vez, PC ligado, cota do Groq).
- Teste real: uma live pública de verdade por ~12 min (`window_seconds` 300) → pelo menos 1 janela `done`; conferir `live.json`, transcrição anexada, e (se houver momento ≥ min_score) um clipe na grade; **Parar** → `video.mp4` gerado.
- Commit `docs: como usar os cortes de live ao vivo`.
