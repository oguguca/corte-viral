# Fase 1 — Download + Transcrição: Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** CLI `python -m corteviral ingest <url|arquivo>` que cria um projeto, baixa/importa o vídeo, extrai áudio e gera `transcript.json` com timestamps por palavra, com cache por etapa.

**Architecture:** Pacote Python `corteviral/` com config (Pydantic + YAML), modelo de projeto em disco com hash de parâmetros por etapa, e etapas `pipeline/ingest.py` e `pipeline/transcribe.py` como funções puras chamadas pela CLI (e, na Fase 5, pela API).

**Tech Stack:** Python 3.11, FFmpeg (CLI), yt-dlp (API Python), faster-whisper (CTranslate2, CUDA int8), Pydantic v2, PyYAML, pytest.

**Spec:** `docs/superpowers/specs/2026-11-11-corte-viral-design.md` (seções 2, 3, 4, 10, 11, 13)

## Global Constraints

- Python 3.11; Windows 10/11; caminhos sempre via `pathlib.Path` (a pasta do usuário fica no Desktop).
- Áudio extraído: WAV PCM 16 kHz mono (`audio.wav`).
- Whisper padrão: modelo `medium`, `device: cuda`, `compute_type: int8`, `vad_filter: true`, `word_timestamps: true`.
- Fallback: erro de CUDA/VRAM → CPU com modelo `small`, `compute_type: int8`, com aviso no log.
- Projetos em `projects/<slug>-<AAAAMMDD>/` (ignorado pelo git); `project.json` guarda origem, duração, etapas e hash de parâmetros.
- Formato de `transcript.json`: `{language, duration, words:[{w,start,end,p}], segments:[{start,end,text}]}` — tempos em segundos, float com 3 casas.
- Idiomas: `language: auto` (padrão), aceita `pt` / `en`.
- Commits: Conventional Commits em PT-BR, **sem** linhas de atribuição; push para `origin/main` ao fim da fase.
- Nunca versionar vídeo, áudio, modelos, `.env`, `.venv`.

## Review Focus

1. **Nomes de arquivo/títulos com acentos, emojis, `|`, `:` ou `?`** (comuns em títulos de live) → slug ASCII seguro, sem quebrar no Windows. Teste em Task 2.
2. **Rodar `ingest` de novo com a mesma URL/arquivo** → reutiliza o projeto existente e pula download/transcrição já feitos. Teste em Task 2 e Task 5.
3. **DLL CUDA/cuDNN ausente ou falta de VRAM** (erro só aparece na primeira inferência, não ao carregar) → cai para CPU e conclui. Teste em Task 4.
4. **URL de live ainda em andamento** → erro claro "live em andamento, aguarde virar VOD", sem baixar indefinidamente. Teste em Task 3.
5. **Arquivo local grande (VOD de 3h, 5–10 GB)** → não copiar; usar hardlink quando no mesmo disco, senão referenciar o caminho original. Teste em Task 3.

---

### Task 1: Ambiente, esqueleto do pacote e configuração

**Files:**
- Create: `pyproject.toml`, `corteviral/__init__.py`, `corteviral/config.py`, `config.example.yaml`, `.env.example`, `tests/test_config.py`, `tests/conftest.py`

**Interfaces:**
- Produces:
  - `class WhisperSettings(BaseModel)`: `model: str = "medium"`, `device: Literal["cuda","cpu","auto"] = "cuda"`, `compute_type: str = "int8"`, `language: str = "auto"`, `beam_size: int = 5`, `vad_filter: bool = True`, `cpu_fallback_model: str = "small"`
  - `class PathsSettings(BaseModel)`: `projects_dir: Path = Path("projects")`, `models_dir: Path = Path("models")`
  - `class Settings(BaseModel)`: `paths: PathsSettings`, `whisper: WhisperSettings`
  - `load_settings(path: Path | None = None) -> Settings` — lê `config.yaml` (ou o caminho dado); arquivo ausente → padrões; chaves desconhecidas → `ValidationError`.

- [ ] **Step 1: Instalar ferramentas (usuário confirma)**

Run:
```
winget install -e --id Python.Python.3.11
winget install -e --id Gyan.FFmpeg
```
Reabrir o terminal. Expected: `py -3.11 --version` → `Python 3.11.x`; `ffmpeg -version` mostra a versão.

- [ ] **Step 2: Criar venv e `pyproject.toml`**

Dependências: `faster-whisper>=1.1`, `yt-dlp>=2025.1`, `pydantic>=2.7`, `pyyaml>=6`, `python-dotenv>=1`, `nvidia-cublas-cu12`, `nvidia-cudnn-cu12==9.*`; extra `dev`: `pytest>=8`. Config pytest: marcador `slow` (testes com modelo real), `addopts = "-m 'not slow'"`.

Run: `py -3.11 -m venv .venv; .venv\Scripts\pip install -e .[dev]`
Expected: instalação sem erros.

- [ ] **Step 3: Testes de configuração**

```python
def test_defaults_when_file_missing(tmp_path):
    s = load_settings(tmp_path / "nao_existe.yaml")
    assert s.whisper.model == "medium"
    assert s.whisper.compute_type == "int8"
    assert s.whisper.cpu_fallback_model == "small"

def test_yaml_overrides(tmp_path):
    p = tmp_path / "c.yaml"; p.write_text("whisper:\n  model: small\n  language: pt\n", encoding="utf-8")
    s = load_settings(p)
    assert s.whisper.model == "small" and s.whisper.language == "pt"
    assert s.whisper.device == "cuda"

def test_unknown_key_rejected(tmp_path):
    p = tmp_path / "c.yaml"; p.write_text("whisper:\n  modelo: small\n", encoding="utf-8")
    with pytest.raises(ValidationError):
        load_settings(p)
```

- [ ] **Step 4: Rodar e ver falhar** — `.venv\Scripts\pytest tests/test_config.py -v` → FAIL (import).

- [ ] **Step 5: Implementar `corteviral/config.py`** (`model_config = ConfigDict(extra="forbid")` em todos os modelos) e `config.example.yaml` espelhando os padrões com comentários em PT-BR; `.env.example` com `ANTHROPIC_API_KEY=`.

- [ ] **Step 6: Rodar e ver passar** — mesmo comando → 3 passed.

`tests/conftest.py` concentra as fixtures compartilhadas, adicionadas pelas tasks que as usam: `settings_tmp` (Settings com `projects_dir = tmp_path/"projects"`, aqui), `sample_video` e `probe_audio` (Task 3), `project_with_audio` e `FakeModel` (Task 4), `write_cfg` (Task 5).

- [ ] **Step 7: Commit** — `git commit -m "chore: cria esqueleto do pacote e configuração"`

---

### Task 2: Projeto em disco e cache por etapa

**Files:**
- Create: `corteviral/project.py`, `corteviral/utils.py`, `tests/test_project.py`

**Interfaces:**
- Produces:
  - `slugify(text: str, max_len: int = 40) -> str` — ASCII minúsculo, `-` como separador, sem caracteres inválidos no Windows; vazio → `"video"`.
  - `params_hash(params: dict) -> str` — SHA-256 de JSON com `sort_keys=True`, primeiros 16 hex.
  - `class Project`: atributos `dir: Path`, `data: dict`; `Project.create(projects_dir: Path, source: str, title: str) -> Project`; `Project.find_by_source(projects_dir: Path, source: str) -> Project | None`; `Project.load(dir: Path) -> Project`; `save() -> None`; `is_stage_done(stage: str, params: dict) -> bool`; `mark_stage(stage: str, params: dict) -> None`; `invalidate_from(stage: str) -> None` usando a ordem `STAGES = ["ingest","transcribe","chat","signals","scoring","boundaries","layout","captions","metadata","export"]`; `path(name: str) -> Path`.

- [ ] **Step 1: Testes**

```python
def test_slugify_handles_title_with_emoji_and_symbols():
    assert slugify("🔴 LIVE | GTA 6: Assalto ao BANCO?! ção") == "live-gta-6-assalto-ao-banco-cao"
    assert slugify("???") == "video"

def test_params_hash_is_order_independent():
    assert params_hash({"a": 1, "b": 2}) == params_hash({"b": 2, "a": 1})

def test_create_then_find_by_source_reuses(tmp_path):
    p = Project.create(tmp_path, "https://youtu.be/x", "Live GTA")
    assert p.dir.name.startswith("live-gta-")
    assert Project.find_by_source(tmp_path, "https://youtu.be/x").dir == p.dir

def test_stage_cache_and_invalidation(tmp_path):
    p = Project.create(tmp_path, "a.mp4", "a")
    p.mark_stage("ingest", {"x": 1}); p.mark_stage("transcribe", {"m": "medium"})
    assert p.is_stage_done("transcribe", {"m": "medium"})
    assert not p.is_stage_done("transcribe", {"m": "small"})
    p.invalidate_from("transcribe")
    assert p.is_stage_done("ingest", {"x": 1}) and not p.is_stage_done("transcribe", {"m": "medium"})

def test_project_json_roundtrip(tmp_path):
    p = Project.create(tmp_path, "a.mp4", "a"); p.mark_stage("ingest", {})
    assert Project.load(p.dir).is_stage_done("ingest", {})
```

- [ ] **Step 2: Rodar e ver falhar** — `pytest tests/test_project.py -v` → FAIL.
- [ ] **Step 3: Implementar** — `slugify` via `unicodedata.normalize("NFKD")` + regex `[^a-z0-9]+`; colisão de nome de pasta → sufixo `-2`, `-3`. `project.json` escrito em UTF-8 de forma atômica (arquivo temporário + `os.replace`).
- [ ] **Step 4: Rodar e ver passar** — 5 passed.
- [ ] **Step 5: Commit** — `git commit -m "feat: adiciona modelo de projeto com cache por etapa"`

---

### Task 3: Ingest — download, importação local e extração de áudio

**Files:**
- Create: `corteviral/ffmpeg.py`, `corteviral/pipeline/__init__.py`, `corteviral/pipeline/ingest.py`, `tests/test_ingest.py`, `tests/conftest.py` (fixture `sample_video`)

**Interfaces:**
- Consumes: `Project`, `params_hash` (Task 2); `Settings` (Task 1).
- Produces:
  - `ffmpeg.run(args: list[str]) -> None` (levanta `FFmpegError` com stderr) e `ffmpeg.probe_duration(path: Path) -> float` (via `ffprobe`).
  - `is_url(s: str) -> bool`
  - `class IngestError(Exception)`
  - `ingest(source: str, settings: Settings, on_progress: Callable[[float, str], None] | None = None) -> Project` — cria ou reaproveita projeto; garante `source.<ext>` (ou referência ao original em `project.data["video_path"]`) e `audio.wav`; grava `duration`; marca etapa `ingest`.

- [ ] **Step 1: Fixture** — `sample_video` gera com FFmpeg um MP4 de 8 s (`testsrc=size=1280x720:rate=30` + `sine=frequency=440`) em `tmp_path` com nome `"vídeo teste ção.mp4"` (acento e espaço de propósito).

- [ ] **Step 2: Testes**

```python
def test_is_url():
    assert is_url("https://www.twitch.tv/videos/123") and is_url("https://kick.com/x/videos/abc")
    assert not is_url(r"C:\Users\x\Desktop\live.mp4")

def test_ingest_local_file_extracts_16k_mono_wav(sample_video, settings_tmp):
    p = ingest(str(sample_video), settings_tmp)
    wav = p.path("audio.wav")
    assert wav.exists()
    info = probe_audio(wav)  # helper de teste via ffprobe
    assert info == {"sample_rate": 16000, "channels": 1}
    assert abs(p.data["duration"] - 8.0) < 0.2

def test_ingest_local_does_not_copy_large_file(sample_video, settings_tmp):
    p = ingest(str(sample_video), settings_tmp)
    vp = Path(p.data["video_path"])
    assert vp.samefile(sample_video) or os.stat(vp).st_nlink > 1

def test_ingest_twice_is_cached(sample_video, settings_tmp, monkeypatch):
    p1 = ingest(str(sample_video), settings_tmp)
    calls = []; monkeypatch.setattr("corteviral.ffmpeg.run", lambda a: calls.append(a))
    p2 = ingest(str(sample_video), settings_tmp)
    assert p1.dir == p2.dir and calls == []

def test_live_in_progress_rejected(monkeypatch, settings_tmp):
    monkeypatch.setattr("corteviral.pipeline.ingest._probe_url",
                        lambda url: {"title": "x", "is_live": True})
    with pytest.raises(IngestError, match="live em andamento"):
        ingest("https://www.twitch.tv/fulano", settings_tmp)

def test_missing_local_file(settings_tmp):
    with pytest.raises(IngestError, match="não encontrado"):
        ingest(r"C:\nao\existe.mp4", settings_tmp)
```

- [ ] **Step 3: Rodar e ver falhar.**
- [ ] **Step 4: Implementar.** URLs: `_probe_url` usa `yt_dlp.YoutubeDL({"quiet": True}).extract_info(url, download=False)`; download com `format: "bv*[height<=1080]+ba/b[height<=1080]"`, `merge_output_format: "mp4"`, `outtmpl` dentro do projeto como `source.%(ext)s`, `progress_hooks` → `on_progress`. Local: `os.link` para `source<ext>` no projeto; se falhar (outro disco), guarda o caminho original em `video_path`. Áudio: `ffmpeg -y -i <video> -vn -ac 1 -ar 16000 -c:a pcm_s16le audio.wav`. Parâmetros do hash da etapa: `{"source": source, "ar": 16000}`.
- [ ] **Step 5: Rodar e ver passar** — 6 passed.
- [ ] **Step 6: Commit** — `git commit -m "feat: adiciona ingest com download via yt-dlp e extração de áudio"`

---

### Task 4: Transcrição com timestamps por palavra e fallback para CPU

**Files:**
- Create: `corteviral/pipeline/transcribe.py`, `corteviral/models.py`, `tests/test_transcribe.py`

**Interfaces:**
- Consumes: `Project`, `Settings.whisper`.
- Produces:
  - `models.Word(w: str, start: float, end: float, p: float)`, `models.Segment(start: float, end: float, text: str)`, `models.Transcript(language: str, duration: float, words: list[Word], segments: list[Segment])` com `save(path)` / `load(path)`.
  - `transcribe(project: Project, settings: Settings, on_progress: Callable[[float, str], None] | None = None, model_factory=WhisperModel) -> Transcript` — grava `transcript.json`, marca etapa `transcribe` com hash de `settings.whisper`.

- [ ] **Step 1: Testes (modelo falso injetado via `model_factory`)**

```python
def test_transcript_has_word_timestamps(project_with_audio, settings_tmp):
    t = transcribe(project_with_audio, settings_tmp, model_factory=FakeModel.ok)
    assert t.language == "pt"
    assert [w.w for w in t.words] == ["Olha", "isso", "aqui!"]
    assert t.words[0].start == 0.5 and t.words[2].end == 1.9
    assert project_with_audio.path("transcript.json").exists()

def test_words_are_monotonic_and_rounded(project_with_audio, settings_tmp):
    t = transcribe(project_with_audio, settings_tmp, model_factory=FakeModel.ok)
    assert all(a.start <= b.start for a, b in zip(t.words, t.words[1:]))
    assert all(round(w.start, 3) == w.start for w in t.words)

def test_cuda_failure_falls_back_to_cpu_small(project_with_audio, settings_tmp, caplog):
    created = []
    t = transcribe(project_with_audio, settings_tmp,
                   model_factory=FakeModel.cuda_fails_on_first_inference(created))
    assert created == [("medium", "cuda"), ("small", "cpu")]
    assert "CPU" in caplog.text and len(t.words) == 3

def test_cached_second_run_does_not_load_model(project_with_audio, settings_tmp):
    transcribe(project_with_audio, settings_tmp, model_factory=FakeModel.ok)
    t = transcribe(project_with_audio, settings_tmp, model_factory=FakeModel.explode)
    assert len(t.words) == 3

@pytest.mark.slow
def test_real_model_on_sample(project_with_audio, settings_tmp):
    settings_tmp.whisper.model = "tiny"
    t = transcribe(project_with_audio, settings_tmp)
    assert t.duration > 0
```

`FakeModel.cuda_fails_on_first_inference` levanta `RuntimeError("CUDA failed with error out of memory")` ao consumir o gerador de segmentos (é assim que o faster-whisper falha de verdade), não no construtor.

- [ ] **Step 2: Rodar e ver falhar.**
- [ ] **Step 3: Implementar.** `language="auto"` → passar `None` ao faster-whisper. Consumir o gerador dentro de `try`; erro cujo texto contenha `cuda`, `cudnn`, `cublas` ou `out of memory` (sem diferenciar maiúsculas) → log de aviso + recriar com `(cpu_fallback_model, "cpu", "int8")` e reiniciar. Progresso = `segment.end / info.duration`.
- [ ] **Step 4: Rodar e ver passar** — 4 passed (+1 slow skipped).
- [ ] **Step 5: Commit** — `git commit -m "feat: adiciona transcrição com faster-whisper e fallback para CPU"`

---

### Task 5: CLI, teste ponta a ponta e README

**Files:**
- Create: `corteviral/__main__.py`, `corteviral/cli.py`, `tests/test_cli.py`
- Modify: `README.md` (seções Instalação e Uso)

**Interfaces:**
- Consumes: `load_settings`, `ingest`, `transcribe`.
- Produces: `main(argv: list[str] | None = None) -> int`; comandos `ingest <fonte> [--config PATH] [--model NOME] [--language auto|pt|en] [--force]` e `info <pasta_do_projeto>`. `--force` chama `invalidate_from("ingest")`. Progresso impresso em uma linha (`\r[transcribe] 42% ...`). Código de saída 1 com mensagem em PT-BR para `IngestError`.

- [ ] **Step 1: Testes**

```python
def test_cli_ingest_runs_both_stages(sample_video, tmp_path, monkeypatch, capsys):
    monkeypatch.setattr("corteviral.pipeline.transcribe.WhisperModel", FakeModel.ok)
    cfg = write_cfg(tmp_path)  # projects_dir dentro de tmp_path
    assert main(["ingest", str(sample_video), "--config", str(cfg)]) == 0
    out = capsys.readouterr().out
    assert "transcript.json" in out

def test_cli_error_exit_code(tmp_path, capsys):
    assert main(["ingest", r"C:\nao\existe.mp4", "--config", str(write_cfg(tmp_path))]) == 1
    assert "não encontrado" in capsys.readouterr().err
```

- [ ] **Step 2: Rodar e ver falhar; implementar `cli.py` com `argparse`; rodar e ver passar** — `pytest -v` → todos os testes não-slow passam.
- [ ] **Step 3: Verificação real (com o usuário)** — `python -m corteviral ingest <link de VOD do usuário>`; conferir `transcript.json` (idioma, palavras com tempo) e anotar o tempo gasto por hora de vídeo na 1050 Ti → registrar no README.
- [ ] **Step 4: README** — Instalação (winget Python/FFmpeg, venv, `pip install -e .[dev]`, copiar `config.example.yaml` → `config.yaml`) e Uso (`ingest`, `info`, `--force`, onde ficam os arquivos).
- [ ] **Step 5: Commit + push**

```
git commit -m "feat: adiciona CLI de ingest e transcrição"
git commit -m "docs: atualiza README com instalação e uso da Fase 1"
git push
```
