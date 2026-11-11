# Fase 5 — Textos de Postagem, Gancho, Revisão e Exportação: Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** fechar o ciclo de postagem — cada clipe ganha gancho, título, descrição e hashtags por plataforma (TikTok, Shorts, Reels, Kwai); o gancho e o crédito "@streamer" são queimados no vídeo (opcionais); na revisão o usuário aprova/descarta, ajusta início/fim e edita os textos; "Exportar aprovados" gera os MP4 finais + um `.txt` por clipe com os textos de cada rede numa pasta pronta para postar, avisando sobre trechos já exportados e streamer não autorizado.

**Architecture:** `metadata.py` (IA gera os textos; sem IA, gera textos simples a partir da justificativa/transcrição); `overlays` em `captions.py` (eventos ASS de gancho e crédito no mesmo arquivo da legenda); `review.py` (status, ajuste de corte, validação); `export.py` (render final + cópia + `.txt` + `history.json`); API/UI ganham painel de revisão por clipe e botão de exportar.

**Tech Stack:** Gemini (JSON schema), ASS/libass, FastAPI, JS.

**Spec:** `docs/superpowers/specs/2026-11-11-corte-viral-design.md` (seções 8, 9 e 12)

## Global Constraints

- Plataformas: `tiktok`, `shorts`, `reels`, `kwai`. Para cada uma: `title` (≤ 80 chars; Shorts ≤ 100 com " #shorts" no fim), `description` (≤ 300), `hashtags` (5–10, sempre com `#`, sem espaços, mistura ampla + nicho + streamer). `hook` único por clipe (≤ 8 palavras).
- Idioma dos textos = idioma da transcrição (padrão pt).
- Crédito: se o preset do streamer tem `credit_handle`, a descrição de todas as plataformas termina com "Créditos: @handle".
- Sem IA (ou falha): `hook` = gancho da triagem ou 6 primeiras palavras; `title` = gancho + " 😱"; descrição = justificativa; hashtags = `#gta6 #gta #cortes #gaming #viral` + streamer.
- Gancho no vídeo: primeiros 3,0 s, no topo da área segura da gameplay (y≈700 em 1920 no layout split; 300 nos outros), fonte da legenda, texto branco em caixa preta (`BorderStyle 3`), quebra em até 2 linhas, encolhe se não couber. Liga/desliga: `moment["hook_on"]` > `CaptionStyle.hook` (padrão `True`).
- Crédito no vídeo: "@handle" pequeno (36 px), canto inferior direito da área segura, o clipe inteiro, só se `credit_handle` e `CaptionStyle.credit` (padrão `True`).
- Revisão: `status` ∈ `pending|approved|discarded`; ajuste de corte: `0 ≤ start < end ≤ duração do VOD`, duração entre 5 e 180 s (422 fora disso); ao mudar o corte, `text` é recalculado da transcrição e o clipe antigo deixa de valer (o hash do render já muda).
- Exportação: só `approved`; pasta `exports/<slug-do-projeto>/`; arquivos `NN-<slug-do-titulo>.mp4` + `.txt` (NN = ordem por nota); render final 1080x1920 (reaproveita cache); `history.json` em `data/` com `{source, start, end, file, ts}`; aviso (não bloqueio) se sobreposição > 50 % com trecho já exportado da mesma fonte; aviso se streamer não está `authorized`.
- Electron: **fora desta fase** — a interface atual (janela própria do Edge + servidor local) já entrega todas as telas da seção 9; empacotar em Electron/instalador fica como etapa opcional.

## Review Focus

1. **IA devolve hashtags sem `#`, com espaço ou repetidas, título longo demais** → normalizados (adiciona `#`, remove espaços/duplicatas, corta no limite sem quebrar palavra). Teste em Task 1.
2. **Gancho/crédito com `{` `}` `\` ou muito longo** → escapado e encolhido/quebrado dentro da área segura. Teste em Task 2.
3. **Ajuste de corte inválido** (fim antes do início, fora do vídeo, 2 s, 10 min) → 422 e nada salvo. Teste em Task 3.
4. **Exportar com títulos iguais ou com caracteres proibidos no Windows** (`?`, `:`, `"`, `|`) → nomes de arquivo únicos e válidos. Teste em Task 4.
5. **Exportar duas vezes o mesmo trecho** → segunda vez avisa duplicata; a pasta não fica com arquivos pela metade se o render falhar. Teste em Task 4.

---

### Task 1: Textos de postagem por plataforma

**Files:** Create `corteviral/metadata.py`, `tests/test_metadata.py`; Modify `corteviral/llm.py` (`GeminiClient.write_posts`).

**Interfaces:**
- `PLATFORMS = ("tiktok", "shorts", "reels", "kwai")`.
- `normalize_post(post: dict, platform: str, credit: str | None) -> dict`; `normalize_hashtags(tags) -> list[str]`; `fit(text, limit) -> str`.
- `fallback_posts(moment, streamer, credit) -> dict` → `{"hook": str, "posts": {plat: {title, description, hashtags}}}`.
- `GeminiClient.write_posts(items: list[dict], language: str) -> list[dict]` (lote; schema por item `{id, hook, tiktok, shorts, reels, kwai}`).
- `generate_posts(project, moments, settings, client=AUTO) -> dict` → grava `moment["meta"]` em `moments.json` (`moments` e `all`); stats `{ai, fallback}`.

- [ ] Testes: `test_normalize_hashtags`; `test_fit_cuts_on_word_boundary`; `test_shorts_title_gets_shorts_tag`; `test_credit_appended_to_descriptions`; `test_fallback_posts_without_ai`; `test_generate_posts_uses_ai_and_saves`; `test_generate_posts_ai_failure_falls_back`; `test_write_posts_request_shape`.
- [ ] Ver falhar → implementar → ver passar → commit `feat: gera gancho, título, descrição e hashtags por plataforma`.

### Task 2: Gancho e crédito no vídeo

**Files:** Modify `corteviral/pipeline/captions.py` (`build_overlay_events`), `corteviral/config.py` (`CaptionStyle.hook`, `CaptionStyle.credit`), `corteviral/pipeline/render.py`; Create tests em `tests/test_captions.py`, `tests/test_render_captions.py`.

**Interfaces:** `build_ass(words, style, ..., hook: str | None = None, credit: str | None = None, layout: str = "split")`.

- [ ] Testes: `test_hook_event_first_3s_boxed_in_safe_zone`; `test_hook_escaped_and_shrunk`; `test_credit_event_whole_clip`; `test_ass_without_words_but_with_hook_still_has_events`; `test_render_burns_hook_top_band` (FFmpeg real: faixa do gancho muda só nos 3 s iniciais); `test_hook_toggle_per_moment`.
- [ ] Ver falhar → implementar → ver passar → commit `feat: gancho e crédito queimados no clipe`.

### Task 3: Revisão (status e ajuste de corte)

**Files:** Create `corteviral/review.py`, `tests/test_review.py`; Modify `server/app.py` (`PUT /api/projects/{name}/moments/{id}` aceita `status`, `start`, `end`, `meta`, `hook_on`).

**Interfaces:** `apply_patch(project, moment_id, patch: dict) -> dict` (valida e atualiza `moments`+`all`, recalcula `text`); `ReviewError`.

- [ ] Testes: `test_status_values`; `test_trim_valid_recomputes_text`; `test_trim_invalid_rejected` (fim<início, fora do VOD, 2 s, 600 s); `test_meta_edit_normalized`; `test_api_patch_returns_422_on_invalid_trim`.
- [ ] Ver falhar → implementar → ver passar → commit `feat: revisão com aprovar, descartar e ajuste de corte`.

### Task 4: Exportação em lote

**Files:** Create `corteviral/export.py`, `tests/test_export.py`; Modify `server/app.py` (job `kind: "export"`, `GET /api/projects/{name}/export-check`), `cli.py` (`export`).

**Interfaces:** `export_approved(project, settings, on_progress=None) -> dict` → `{folder, files: [...], duplicates: [...], unauthorized: bool}`; `posts_txt(moment) -> str`; `HistoryStore(path)` (`overlaps(source, start, end) -> list`, `add(...)`).

- [ ] Testes: `test_posts_txt_has_all_platforms`; `test_export_names_unique_and_windows_safe`; `test_export_only_approved_and_writes_txt` (render real, vídeo sintético); `test_second_export_reports_duplicate`; `test_failed_render_leaves_no_partial_files`; `test_unauthorized_streamer_flagged`; `test_api_export_job`; `test_cli_export`.
- [ ] Ver falhar → implementar → ver passar → commit `feat: exporta clipes aprovados com textos de postagem`.

### Task 5: Interface de revisão

**Files:** Modify `index.html`, `README.md`, `config.example.yaml`.

- Em cada momento: botões **Aprovar / Descartar** (estado visível), **ajuste de corte** (início/fim com −1 s/+1 s e "usar posição do vídeo"), **Textos** (gancho + abas TikTok/Shorts/Reels/Kwai com título, descrição, hashtags editáveis e botão copiar), chave **Gancho no vídeo**; filtro **Todos / Aprovados / Descartados**; botões **Gerar textos com IA** e **Exportar aprovados (N)**; aviso de streamer não autorizado com opção de marcar como autorizado; resultado da exportação com link "Abrir pasta" e avisos de duplicata.
- [ ] Verificação: JS válido; fluxo real no VOD do Flow Games (gerar textos, aprovar 2, exportar) conferindo os arquivos e um quadro com gancho.
- [ ] Revisão final → correções → push.
