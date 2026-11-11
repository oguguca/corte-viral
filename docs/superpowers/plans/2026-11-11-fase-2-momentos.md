# Fase 2 — Detecção e Pontuação de Momentos: Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** a partir de um projeto transcrito, gerar `moments.json` com os N melhores momentos (nota 0–100, justificativa curta, início/fim em pontos naturais da fala), visíveis e reproduzíveis na interface.

**Architecture:** quatro etapas puras e cacheadas (`signals` → `candidates` → `scoring` → `boundaries`) chamadas por `analyze(project, settings)`. Sinais locais (áudio, palavras de reação, ritmo de fala, chat quando houver) bastam para pontuar sem IA; um provedor de LLM plugável (`none` | `gemini` | `anthropic`) refina a nota e escreve a justificativa. A API/UI da Fase 1 ganha "Analisar" e a lista de momentos.

**Tech Stack:** Python 3.11, numpy (já instalado via faster-whisper), yt-dlp (chat), urllib (Gemini REST), SDK `anthropic` (opcional), FastAPI, HTML/JS da Fase 1.

**Spec:** `docs/superpowers/specs/2026-11-11-corte-viral-design.md` (seções 3, 5, 12)

## Global Constraints

- Funciona **inteiro sem chave de API** (`llm.provider: none`); a IA só melhora nota e justificativa.
- Provedor padrão: `gemini`, modelo `gemini-3.5-flash-lite`, chave `GEMINI_API_KEY` no `.env`; `anthropic` usa `ANTHROPIC_API_KEY` e modelo `claude-opus-5-5`. Chaves nunca em código, log ou `config.yaml`.
- Falha de LLM (rede, cota, JSON inválido) **nunca** derruba a análise: o candidato fica com a nota de sinais e `ai: false`.
- Nota final: `0.60·llm + 0.25·áudio + 0.15·chat` quando há LLM; sem LLM: `0.55·reação + 0.30·áudio + 0.15·chat`. Sem chat, o peso do chat é redistribuído proporcionalmente. Pesos no config.
- Perfis de duração: `viral` 15–60 s, `monetizacao` 61–120 s (padrão `viral`; configurável e escolhível na UI).
- Fronteiras: início em pausa ≥ 300 ms ou início de segmento antes do ponto; fim em pausa ≥ 300 ms / fim de frase depois do ponto.
- Clipes escolhidos não se sobrepõem (> 30 % de sobreposição → fica o de maior nota).
- `moments.json`: `[{id, start, end, score, reason, parts:{audio,chat,reaction,llm}, ai, text, status:"pending"}]`, ordenado por nota.
- Commits PT-BR Conventional Commits, sem atribuição; push ao fim da fase.

## Review Focus

1. **Cota do Gemini grátis estourada (HTTP 429) no meio da análise** → os candidatos restantes ficam só com sinais, a análise conclui e a UI avisa "IA indisponível para X momentos". Teste em Task 4.
2. **VOD sem chat (arquivo local, Kick, vídeo que não foi live)** → pesos redistribuídos, nenhum erro. Teste em Task 1/4.
3. **Transcrição esparsa (react/gameplay com longos silêncios)** → candidatos ainda saem dos picos de áudio; janelas sem fala não viram clipe vazio. Teste em Task 2.
4. **VOD mais curto que a duração mínima do perfil** → devolve os momentos possíveis, sem quebrar. Teste em Task 3.
5. **Resposta do LLM com IDs faltando, nota fora de 0–100 ou texto fora do JSON** → validação descarta só o item ruim. Teste em Task 4.

---

### Task 1: Sinais por segundo (áudio, reação, ritmo, chat)

**Files:** Create `corteviral/pipeline/signals.py`, `corteviral/pipeline/chat.py`, `tests/test_signals.py`; Modify `corteviral/config.py` (`AnalysisSettings`).

**Interfaces:**
- Produces: `Signals(BaseModel)`: `duration:int`, `audio:list[float]`, `reaction:list[float]`, `speech:list[float]`, `chat:list[float] | None` (z-scores ≥ 0, 1 valor/segundo); `compute_signals(project, settings) -> Signals` (grava `signals.json`, etapa `signals`).
- `audio_loudness(wav: Path) -> list[float]` (dB RMS por segundo, lendo o WAV 16 kHz em blocos com `wave`+numpy, sem carregar 3 h na memória).
- `robust_z(values, window=60) -> list[float]` (desvio em relação à mediana móvel / MAD; negativos → 0).
- `reaction_curve(words, duration, lexicon) -> list[float]` — soma por segundo de pesos do léxico (`kkk+`, `haha`, `meu deus`, `não acredito`, `caralho`, `olha isso`, `mano`, `socorro`, `nossa`, `que isso`, `ai meu`, `cara`… configurável, com peso) + bônus por palavra em CAIXA ALTA / `!`.
- `chat.fetch_chat(project, settings) -> list[float] | None` — mensagens/s via yt-dlp (`writesubtitles` com `live_chat` no YouTube, `rechat` na Twitch), usando os cookies da sessão; `None` se indisponível.

- [ ] Testes: `test_robust_z_flags_spike` (série plana com pico → pico > 3, resto ≈ 0); `test_audio_loudness_one_value_per_second` (WAV sintético 5 s silêncio + 1 s tom alto → 6 valores, índice 5 o maior); `test_reaction_curve_counts_lexicon_at_word_time` (palavras "kkkkk" em 10.2 s e "MEU DEUS" em 30.5 s → picos em 10 e 30); `test_chat_none_when_unavailable`; `test_signals_cached_by_params`.
- [ ] Ver falhar → implementar → ver passar → commit `feat: adiciona sinais de áudio, reação, ritmo e chat`.

### Task 2: Candidatos

**Files:** Create `corteviral/pipeline/candidates.py`, `tests/test_candidates.py`.

**Interfaces:**
- Consumes: `Signals`, `Transcript`.
- Produces: `Candidate(BaseModel)`: `id:str`, `peak:float`, `start:float`, `end:float`, `parts:dict[str,float]` (audio/chat/reaction 0–100), `text:str`; `find_candidates(signals, transcript, settings) -> list[Candidate]`.
- Combinação por segundo `c = w_a·audio + w_r·reaction + w_c·chat` (suavizada 3 s); picos com distância mínima 20 s acima do limiar; janela inicial `[pico − pre, pico + post]` com `pre/post` do perfil (`viral`: 25/12 s; `monetizacao`: 60/30 s); complementado por janelas deslizantes de texto de 45 s com maior densidade de reação; descarta janelas com < 8 palavras; máximo `max_candidates` (padrão 60).
- `parts` normalizados 0–100 pelo percentil 99 do VOD.

- [ ] Testes: `test_peak_becomes_candidate_around_it`; `test_peaks_closer_than_min_distance_merge`; `test_window_without_speech_is_dropped` (Review Focus 3); `test_respects_max_candidates`; `test_text_is_words_inside_window`.
- [ ] Ver falhar → implementar → ver passar → commit `feat: gera candidatos a partir dos picos de sinais`.

### Task 3: Fronteiras naturais e seleção

**Files:** Create `corteviral/pipeline/boundaries.py`, `tests/test_boundaries.py`.

**Interfaces:**
- Produces: `snap_bounds(start, end, transcript, min_s, max_s) -> tuple[float, float]`; `select(moments: list[dict], n: int, max_overlap=0.3) -> list[dict]`.

- [ ] Testes: `test_start_snaps_back_to_pause_before`; `test_end_snaps_forward_to_sentence_end`; `test_duration_clamped_to_profile` (viral 15–60, monetizacao 61–120); `test_short_vod_returns_what_fits` (Review Focus 4); `test_select_drops_overlapping_lower_score`.
- [ ] Ver falhar → implementar → ver passar → commit `feat: ajusta início e fim dos momentos em pausas naturais`.

### Task 4: Pontuação com LLM plugável

**Files:** Create `corteviral/llm.py`, `corteviral/pipeline/scoring.py`, `tests/test_scoring.py`, `tests/test_llm.py`; Modify `config.py` (`LLMSettings`: `provider: Literal["none","gemini","anthropic"]="gemini"`, `model: str|None`, `batch_size: int = 8`, `timeout: int = 90`), `.env.example` (`GEMINI_API_KEY=`).

**Interfaces:**
- `class LLMError(Exception)`; `class LLMClient(Protocol): def score(self, items: list[dict], profile: str, language: str) -> list[dict]` (cada saída `{id, score:int 0-100, reason:str ≤ 140, hook:str ≤ 60, standalone:bool}`).
- `make_client(settings) -> LLMClient | None` — `None` para `none` ou chave ausente (com aviso).
- `GeminiClient` (REST `v1beta/models/{model}:generateContent`, header `x-goog-api-key`, `responseMimeType: application/json`, `responseSchema`); `AnthropicClient` (SDK oficial, `claude-opus-5-5`, `output_config.format` com JSON schema, `effort: "low"`).
- `score_candidates(candidates, transcript, settings, client, on_progress) -> list[dict]` — lotes de `batch_size`; prompt em PT-BR com critérios (humor, tensão, frase de efeito, gancho nos 3 primeiros segundos, faz sentido sem contexto, perfil); combina pesos; `reason` sem IA = composição dos sinais ("pico de áudio + reação 'MEU DEUS'").

- [ ] Testes: `test_no_llm_uses_signal_weights_and_builds_reason`; `test_llm_scores_merge_with_weights`; `test_llm_429_midway_keeps_remaining_with_signals` (Review Focus 1); `test_invalid_items_dropped_individually` (Review Focus 5); `test_missing_chat_redistributes_weights` (Review Focus 2); `test_gemini_request_shape` (urlopen mockado: URL, header, JSON schema, chave nunca na URL).
- [ ] Ver falhar → implementar → ver passar → commit `feat: pontua momentos com sinais e IA plugável (Gemini/Anthropic)`.

### Task 5: `analyze`, CLI, API e interface

**Files:** Create `corteviral/pipeline/analyze.py`, `tests/test_analyze.py`; Modify `corteviral/cli.py` (comando `analyze <projeto> [--n 10] [--profile viral|monetizacao] [--provider ...]`), `corteviral/server/app.py` (job tipo `analyze`; `GET /api/projects/{name}/moments`), `server/static/index.html` (botão "Encontrar momentos", seletor de perfil e N, lista de momentos com nota/justificativa/selo "IA", clique toca o trecho e para no fim), `README.md`.

**Interfaces:**
- `analyze(project, settings, n=10, profile="viral", on_progress=None) -> list[dict]` — roda signals → candidates → scoring → boundaries → select; grava `moments.json`; etapa `scoring` cacheada por (parâmetros + provedor + modelo).

- [ ] Testes: `test_analyze_end_to_end_without_llm` (projeto sintético com WAV + transcrição com "MEU DEUS" e pico de áudio → momento cobrindo o pico, 15–60 s); `test_cli_analyze`; `test_api_moments_endpoint`; `test_api_analyze_job`.
- [ ] Ver falhar → implementar → ver passar → commit `feat: analisa momentos pela CLI e pela interface`.
- [ ] Verificação real: rodar no VOD do usuário (`react-gta-6-…`) com `gemini` e com `none`; conferir na UI que os 10 momentos fazem sentido; anotar tempo e uso do Gemini.
- [ ] Revisão final (code-reviewer, modelo mais capaz) → correções → push.
