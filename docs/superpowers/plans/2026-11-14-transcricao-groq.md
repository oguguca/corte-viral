# Transcrição na nuvem (Groq) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Sem GPU, transcrever pela API grátis do Groq (`whisper-large-v3-turbo`) com timestamps por palavra, caindo na CPU local quando não houver chave, internet ou cota.

**Architecture:** Novo módulo `corteviral/pipeline/groq_transcribe.py` (corta o áudio em pedaços com ffmpeg, envia por HTTP multipart via `urllib`, converte a resposta em `Segment`/`Word`). `transcribe.py` escolhe o motor (`auto | local | groq`) e reaproveita o `_Checkpoint` e o fallback de CPU que já existem.

**Tech Stack:** Python 3, stdlib `urllib`, ffmpeg (`libopus`), pydantic, pytest.

**Spec:** `docs/superpowers/specs/2026-11-14-transcricao-nuvem-design.md`

## Global Constraints

- Nada pago; só a API grátis do Groq. Endpoint: `https://api.groq.com/openai/v1/audio/transcriptions`.
- Chave em `GROQ_API_KEY` no `.env`, lida com `corteviral.llm._key(name, env_file)`; nunca logar a chave.
- Sem nova dependência em `pyproject.toml`.
- Com GPU funcionando (`engine: auto`), o comportamento atual não muda.
- Textos de interface em português; mensagens de progresso exatas: `"transcrevendo na nuvem (Groq)"`,
  `"Cota do Groq esgotada, usando CPU"`, `"Groq indisponível, usando CPU"`.

## Review Focus

- Vídeo mais curto que um pedaço, ou último pedaço parcial → pedaço com `min(chunk, duration - offset)`; sem pedido vazio no fim. (Task 2)
- `duration == 0` no projeto → não dá para fatiar: usa `ffmpeg.probe_duration(audio.wav)`; se ainda 0, vai para o motor local. (Task 3)
- Pedaço só de silêncio (Groq devolve `words: []`/`segments: []`) → segue para o próximo, sem erro. (Task 2)
- Interrupção no meio do Groq e nova execução → retoma no próximo pedaço, sem palavras duplicadas. (Task 3)
- Chave inválida (HTTP 401/403) → não derruba: CPU com `"Groq indisponível, usando CPU"`. (Task 3)

---

### Task 1: Configuração

**Files:**
- Modify: `corteviral/config.py` (`WhisperSettings`)
- Modify: `config.yaml`, `config.example.yaml` (bloco `whisper`), `.env.example`
- Test: `tests/test_config.py`

**Interfaces:**
- Produces: `WhisperSettings.engine: Literal["auto","local","groq"] = "auto"`,
  `WhisperSettings.groq_model: str = "whisper-large-v3-turbo"`, `WhisperSettings.groq_chunk_seconds: int = 600` (`ge=60, le=1200`).

- [ ] **Step 1: Write the failing test**

```python
def test_whisper_engine_defaults(tmp_path):
    s = load_settings(tmp_path / "nao_existe.yaml")
    assert s.whisper.engine == "auto"
    assert s.whisper.groq_model == "whisper-large-v3-turbo"
    assert s.whisper.groq_chunk_seconds == 600

def test_whisper_engine_rejects_unknown(tmp_path):
    p = tmp_path / "c.yaml"; p.write_text("whisper:\n  engine: nuvem\n", encoding="utf-8")
    with pytest.raises(Exception):
        load_settings(p)
```

- [ ] **Step 2:** `.venv/Scripts/python.exe -m pytest tests/test_config.py -v` → FAIL (atributo inexistente).
- [ ] **Step 3:** Adicionar os 3 campos. Em `config.yaml`/`config.example.yaml` adicionar com comentários:
  `engine: auto  # auto: GPU se houver; senão Groq (grátis, precisa GROQ_API_KEY no .env); senão CPU` e
  `groq_model: whisper-large-v3-turbo`. Em `.env.example`: `# Grátis em console.groq.com → API Keys (transcrição rápida sem GPU)` + `GROQ_API_KEY=`.
- [ ] **Step 4:** Rodar `tests/test_config.py` → PASS.
- [ ] **Step 5:** `git commit -m "feat(transcrição): opção de motor auto/local/groq na config"`

### Task 2: Cliente Groq

**Files:**
- Create: `corteviral/pipeline/groq_transcribe.py`
- Test: `tests/test_groq_transcribe.py`

**Interfaces:**
- Consumes: `ffmpeg.run(args)`, `Segment`, `Word`.
- Produces:
  - `class GroqError(Exception)`; `class GroqQuotaExceeded(GroqError)`; `class GroqAuthError(GroqError)`.
  - `HttpFn = Callable[[Path, str, str, str | None], dict]` — `(chunk_path, api_key, model, language) -> verbose_json`.
  - `post_chunk(path: Path, api_key: str, model: str, language: str | None, timeout: int = 180, sleep=time.sleep) -> dict` —
    multipart com campos `model`, `response_format=verbose_json`, `timestamp_granularities[]=word`,
    `timestamp_granularities[]=segment`, `temperature=0`, `language` (se dado), `file`. Header `Authorization: Bearer <key>`.
    HTTP 429: se `retry-after` ≤ 60 s, dorme e tenta de novo (até 2 vezes); senão `GroqQuotaExceeded`.
    401/403 → `GroqAuthError`. Outros HTTP/`URLError`/timeout → `GroqError`.
  - `chunk_plan(start: float, duration: float, chunk: int) -> list[tuple[float, float]]` — `(offset, length)` cobrindo `[start, duration)`, último parcial; ignora sobra < 0.5 s.
  - `encode_chunk(audio: Path, offset: float, length: float, dest: Path) -> None` — `ffmpeg -y -ss off -t len -i audio -ac 1 -ar 16000 -c:a libopus -b:a 32k dest.ogg`.
  - `parse_response(data: dict, offset: float) -> tuple[list[Segment], list[Word], str | None]` — soma `offset`, arredonda a 3 casas, `p=1.0`, descarta palavra vazia; idioma: `{"portuguese":"pt","english":"en","spanish":"es"}` pelo nome em minúsculas, senão o valor se tiver 2 letras, senão `None`.

- [ ] **Step 1: Write the failing tests**

```python
def test_chunk_plan_last_partial():
    assert chunk_plan(0, 1300, 600) == [(0, 600), (600, 600), (1200, 100)]

def test_chunk_plan_short_video_and_resume():
    assert chunk_plan(0, 8, 600) == [(0, 8)]
    assert chunk_plan(600, 1300, 600) == [(600, 600), (1200, 100)]
    assert chunk_plan(0, 600.3, 600) == [(0, 600)]

def test_parse_response_offsets_words_and_segments():
    data = {"language": "Portuguese",
            "segments": [{"start": 0.0, "end": 1.5, "text": " olá pessoal"}],
            "words": [{"word": " olá", "start": 0.1, "end": 0.5}, {"word": "pessoal", "start": 0.6, "end": 1.4},
                      {"word": " ", "start": 1.4, "end": 1.4}]}
    segs, words, lang = parse_response(data, 600.0)
    assert lang == "pt"
    assert segs[0].start == 600.0 and segs[0].text == "olá pessoal"
    assert [(w.w, w.start, w.p) for w in words] == [("olá", 600.1, 1.0), ("pessoal", 600.6, 1.0)]

def test_parse_response_silent_chunk():
    assert parse_response({"language": "pt", "segments": [], "words": []}, 0) == ([], [], "pt")

def test_post_chunk_429_short_retry_then_ok(tmp_path, monkeypatch): ...
    # urlopen falso: 1º HTTPError 429 com retry-after "2", 2º devolve JSON; sleep chamado com 2; resultado == JSON

def test_post_chunk_429_long_raises_quota(tmp_path, monkeypatch): ...
    # retry-after "3600" → GroqQuotaExceeded, sleep não chamado

def test_post_chunk_401_raises_auth(tmp_path, monkeypatch): ...
    # → GroqAuthError

def test_post_chunk_sends_word_granularity(tmp_path, monkeypatch): ...
    # captura o Request: corpo contém b'timestamp_granularities[]' e b'word' e b'verbose_json';
    # header Authorization == "Bearer k"
```

- [ ] **Step 2:** `.venv/Scripts/python.exe -m pytest tests/test_groq_transcribe.py -v` → FAIL (módulo inexistente).
- [ ] **Step 3:** Implementar o módulo (monkeypatch em `groq_transcribe.urllib.request.urlopen` nos testes).
- [ ] **Step 4:** Rodar → PASS.
- [ ] **Step 5:** `git commit -m "feat(transcrição): cliente da API grátis do Groq com pedaços e timestamps por palavra"`

### Task 3: Escolha do motor e integração

**Files:**
- Modify: `corteviral/pipeline/transcribe.py` (`transcribe`, novo `_run_groq`)
- Test: `tests/test_transcribe.py`

**Interfaces:**
- Consumes: Task 1 campos; Task 2 `post_chunk`, `chunk_plan`, `encode_chunk`, `parse_response`, `GroqError`, `GroqQuotaExceeded`; `llm._key`.
- Produces: `transcribe(project, settings, on_progress=None, model_factory=None, groq_http: HttpFn | None = None, env_file: Path = Path(".env")) -> Transcript`.
  Com Groq: `Transcript.model == settings.whisper.groq_model`, `Transcript.device == "groq"`.

Regras:
- Motor: `groq` se `engine == "groq"` e há chave; em `auto`, `groq` se há chave **e** (`device == "cpu"` ou o teste da GPU falhar); caso contrário, o fluxo local de hoje. Com `model_factory` fake e `engine == "auto"`, tratar a GPU como ok (os testes atuais não mudam).
- `_run_groq(project, settings, ckpt, duration, on_progress, api_key, http) -> str`: para cada `(offset, length)` de `chunk_plan(ckpt.resume_at, duration, groq_chunk_seconds)`: `encode_chunk` em `project.path("groq_chunk.ogg")`, `http(...)`, `parse_response`, adiciona segmentos/palavras ao `ckpt`, **`ckpt.resume_at = offset + length`**, `ckpt.save()`, progresso `(offset+length)/duration, "transcrevendo na nuvem (Groq)"`. Apagar `groq_chunk.ogg` no final (somar a `PARTIAL, RESUME_WAV` na limpeza).
- `duration` 0 → `ffmpeg.probe_duration(audio.wav)`; se continuar 0, motor local.
- `GroqQuotaExceeded` → progresso `"Cota do Groq esgotada, usando CPU"`; outro `GroqError` → `"Groq indisponível, usando CPU"`. Em ambos: `ckpt.save()` e continua com `cpu_fallback_model` na CPU a partir de `ckpt.resume_at` (mesmo caminho do fallback de GPU).
- Cache: `mark_stage` quando o resultado veio do Groq ou do modelo principal local; fallback de CPU continua sem cache.

- [ ] **Step 1: Write the failing tests** (fixture: `settings_tmp.whisper.engine = "groq"`, `.env` em `tmp_path` com `GROQ_API_KEY=k`, `monkeypatch` em `transcribe.encode_chunk` para só criar o arquivo; `project_with_audio` tem `duration=8.0`, então use `groq_chunk_seconds=60` e `duration=130` quando precisar de 3 pedaços)

```python
def test_groq_engine_produces_words_with_offsets(...):
    # http falso devolve 1 palavra em 1.0–1.5 por pedaço; duration=130, chunk=60
    t = transcribe(p, s, groq_http=fake, env_file=env)
    assert [w.start for w in t.words] == [1.0, 61.0, 121.0]
    assert t.model == "whisper-large-v3-turbo" and t.device == "groq"

def test_groq_quota_falls_back_to_cpu_from_checkpoint(...):
    # http: 1º pedaço ok, 2º levanta GroqQuotaExceeded; model_factory registra (model, device) e o áudio de retomada
    # assert factory chamado com ("small", "cpu"); palavras do 1º pedaço preservadas; progresso recebeu "Cota do Groq esgotada, usando CPU"

def test_groq_auth_error_falls_back_to_cpu(...):
    # GroqAuthError no 1º pedaço → transcrição completa via CPU; progresso "Groq indisponível, usando CPU"

def test_groq_resume_skips_done_chunks(...):
    # 1ª execução: http levanta KeyboardInterrupt no 2º pedaço → pytest.raises
    # 2ª execução: http registra offsets recebidos → começa em 60; sem palavras duplicadas

def test_auto_without_key_uses_local(...):
    # engine auto, device cpu, sem GROQ_API_KEY → http nunca chamado; model_factory usado

def test_auto_with_key_and_cpu_device_uses_groq(...):
    # engine auto, device cpu, com chave → http chamado, model_factory nunca chamado

def test_groq_result_is_cached(...):
    # 2ª execução com http que explode → devolve o mesmo Transcript
```

- [ ] **Step 2:** `.venv/Scripts/python.exe -m pytest tests/test_transcribe.py -v` → novos FAIL, antigos PASS.
- [ ] **Step 3:** Implementar as regras acima em `transcribe.py`.
- [ ] **Step 4:** `.venv/Scripts/python.exe -m pytest -q` → tudo PASS (exceto os marcados como lentos/reais, se já pulados hoje).
- [ ] **Step 5:** `git commit -m "feat(transcrição): usa o Groq sem GPU e cai na CPU sem chave, sem cota ou sem internet"`

### Task 4: Documentação e teste real

**Files:**
- Modify: `README.md` (seção de instalação/IA: como criar a chave em console.groq.com → API Keys, colar `GROQ_API_KEY=` no `.env`, limites grátis aproximados, que o áudio é enviado ao Groq)

- [ ] **Step 1:** Escrever a seção no README (curta, mesmo tom da seção do Gemini).
- [ ] **Step 2:** Teste real (precisa da chave do usuário no `.env`): `.venv/Scripts/python.exe -m corteviral ingest <arquivo-ou-url>` ou pela UI com um vídeo de ~5–10 min; esperado: progresso `"transcrevendo na nuvem (Groq)"`, `transcript.json` com `device: "groq"` e palavras com tempos crescentes; legendas do clipe batendo com a fala.
- [ ] **Step 3:** `git commit -m "docs: como ligar a transcrição grátis pelo Groq"`
