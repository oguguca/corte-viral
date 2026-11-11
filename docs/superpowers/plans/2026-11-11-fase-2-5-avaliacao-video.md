# Fase 2.5 — Avaliação em Duas Etapas (IA assiste o vídeo): Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** os 15 melhores candidatos da triagem são "assistidos" pela IA (vídeo + áudio), recebendo nota por critério, corte sugerido e justificativa detalhada; o usuário dá 👍/👎 e a IA usa esses exemplos.

**Architecture:** `deep.py` gera o mini-vídeo (ffmpeg) e chama `GeminiClient.review_video`; `analyze` passa a ser triagem → seleção top_k → revisão profunda → fronteiras → seleção final; `feedback.py` guarda exemplos; API/UI ganham critérios, selo "IA assistiu" e 👍/👎.

**Tech Stack:** FFmpeg, Gemini REST (`inline_data` video/mp4, `mediaResolution: MEDIA_RESOLUTION_LOW`, `responseSchema`), FastAPI, JS.

**Spec:** `docs/superpowers/specs/2026-11-11-corte-viral-design.md` (seção 5.1)

## Global Constraints

- `DeepSettings`: `enabled=True`, `top_k=15`, `margin=15` s, `model=None` (padrão = modelo do LLM), `weights={gancho:.25, emocao:.25, visual:.15, final:.20, contexto:.15}`, `max_examples=6`.
- Mini-vídeo: `fps=1, scale=-2:360`, libx264 crf 32 veryfast, AAC mono 48k; ≤ 4 MB (se passar, reduz para 240p).
- Só o provedor `gemini` assiste vídeo; `anthropic`/`none` pulam a etapa 2 (nota = triagem).
- Falha (rede, cota, JSON) num trecho → mantém a triagem daquele trecho (`ai: "text"`); 429 encerra a etapa 2 para o resto.
- Corte sugerido: `inicio/fim` relativos ao mini-vídeo → absolutos → `snap_bounds` com a faixa do perfil; se a IA sugerir algo fora do mini-vídeo ou com fim ≤ início, usa os limites da triagem.
- Momento final ganha: `criteria` (5 notas), `description`, `reason` (motivo), `risk`, `ai: "video" | "text" | false`.
- Feedback: `data/feedback.json` `[{streamer, label: "good"|"bad", description, reason, criteria, text, ts}]`; até 6 exemplos no prompt (3+3, mesmo streamer primeiro, mais recentes).
- Cache da análise inclui `deep` (parâmetros); feedback não invalida o cache (botão "Reavaliar" força).

## Review Focus

1. **Cota do Gemini esgota na etapa 2** → momentos restantes ficam com a nota da triagem; análise conclui; não vira cache completo. Teste em Task 2.
2. **IA sugere corte absurdo** (fim antes do início, fora do mini-vídeo, 3 s de duração) → usa limites seguros dentro do perfil. Teste em Task 2.
3. **Trecho no início/fim do VOD** (margem ultrapassa 0 ou a duração) → mini-vídeo recortado aos limites e conversão de tempo correta. Teste em Task 1.
4. **Feedback de outro streamer / sem feedback** → exemplos do mesmo streamer primeiro; nenhum exemplo não quebra o prompt. Teste em Task 3.
5. **Mini-vídeo grande demais** (> 4 MB) → re-gera em 240p. Teste em Task 1.

---

### Task 1: Mini-vídeo e cliente de revisão em vídeo

**Files:** Create `corteviral/pipeline/deep.py`, `tests/test_deep.py`; Modify `corteviral/llm.py` (`GeminiClient.review_video`), `corteviral/config.py` (`DeepSettings`).

**Interfaces:**
- `make_mini_clip(video: Path, start: float, end: float, out: Path, height: int = 360) -> Path` (recorta a [max(0,start), end]).
- `mini_window(moment: dict, duration: float, margin: float) -> tuple[float, float]`.
- `GeminiClient.review_video(video_bytes: bytes, context: dict) -> dict` com `context = {profile, streamer, transcript, window, examples}`; retorno validado `{descricao, motivo, risco, gancho, emocao, visual, final, contexto, inicio, fim}`; 429 → `LLMRateLimited`; outros → `LLMError`; registra uso.

- [ ] Testes: `test_mini_window_clamped_to_vod`; `test_make_mini_clip_is_1fps_360p_with_audio` (vídeo sintético); `test_mini_clip_too_big_falls_back_to_240p` (limite forçado baixo); `test_review_video_request_shape` (urlopen mockado: `inline_data` mime video/mp4, `mediaResolution`, `responseSchema`, chave só no header, uso registrado); `test_review_video_invalid_json_is_llm_error`.
- [ ] Ver falhar → implementar → ver passar → commit `feat: IA assiste mini-vídeo do trecho (Gemini multimodal)`.

### Task 2: Etapa 2 no `analyze`

**Files:** Modify `corteviral/pipeline/analyze.py`; Create `corteviral/pipeline/deep_review.py`, `tests/test_deep_review.py`.

**Interfaces:**
- `criteria_score(criteria: dict, weights: dict) -> int`.
- `apply_review(moment, review, window, transcript, profile_range) -> dict` (nota, corte, textos).
- `deep_review(project, moments, transcript, settings, client, on_progress) -> tuple[list[dict], dict]` — revisa os `top_k` de maior nota; stats `{video, text, rate_limited, errors}`.

- [ ] Testes: `test_criteria_score_weighted`; `test_apply_review_converts_and_snaps_cut`; `test_absurd_cut_falls_back_to_triage_bounds`; `test_quota_midway_keeps_triage_for_rest`; `test_only_top_k_are_reviewed`; `test_analyze_runs_deep_stage_and_marks_ai_video` (cliente falso).
- [ ] Ver falhar → implementar → ver passar → commit `feat: avaliação em duas etapas com nota por critério e corte sugerido`.

### Task 3: Aprender com o usuário (👍/👎)

**Files:** Create `corteviral/feedback.py`, `tests/test_feedback.py`; Modify `deep_review.py` (exemplos no contexto).

**Interfaces:** `FeedbackStore(path)`: `add(streamer, moment, label)`, `examples(streamer, k=6) -> list[dict]`, `label_for(project_id, moment_id)`.

- [ ] Testes: `test_add_and_examples_prefer_same_streamer`; `test_examples_balanced_good_bad`; `test_relabel_replaces`; `test_no_feedback_empty_examples`.
- [ ] Ver falhar → implementar → ver passar → commit `feat: feedback 👍/👎 calibra a avaliação`.

### Task 4: API e interface

**Files:** Modify `server/app.py` (`POST /api/projects/{name}/moments/{id}/feedback`, job analyze aceita `force`), `index.html` (5 barras de critério, selo "IA assistiu", 👍/👎, descrição/motivo/risco, "Reavaliar"), `README.md`, `config.example.yaml`.

- [ ] Testes: `test_api_feedback_saves_label`; `test_api_analyze_force_recomputes`.
- [ ] Ver falhar → implementar → ver passar → commit `feat: critérios, selo de vídeo e feedback na interface`.
- [ ] Verificação real: VOD do Flow Games com etapa 2; comparar top 5 antes/depois; anotar tokens.
- [ ] Revisão final → correções → push.
