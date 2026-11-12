"""Provedores de IA plugáveis para avaliar trechos: Gemini (grátis), Anthropic, ou nenhum.

As chaves vêm do .env / variáveis de ambiente e nunca vão para URL, log ou config.yaml.
"""
from __future__ import annotations

import base64
import json
import logging
import os
import time
import urllib.error
import urllib.request
from pathlib import Path
from typing import Protocol

from dotenv import dotenv_values

from corteviral.config import Settings
from corteviral.usage import UsageTracker

log = logging.getLogger(__name__)

DEFAULT_MODELS = {"gemini": "gemini-3.5-flash-lite", "anthropic": "claude-opus-5-5"}
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
RETRY_DELAYS = (2, 5)  # s entre tentativas em erro temporário (5xx / timeout)
_TRANSIENT = {500, 502, 503, 504}

INSTRUCTIONS = """Você é editor de cortes virais de lives de games (principalmente GTA), em português do Brasil.
Para cada trecho de transcrição, avalie o potencial de virar um clipe curto viral (TikTok, Shorts, Reels).
Critérios: humor, tensão/ação, reação forte, frase de efeito, gancho nos primeiros 3 segundos, e se faz
sentido para quem não viu a live. Os "sinais" (volume, reações faladas, chat) são pistas, não verdade.
Perfil "{profile}": {profile_hint}
Seja exigente: 80+ só para trechos realmente fortes; trechos parados ou sem contexto ficam abaixo de 40.
Para cada item devolva: id (o mesmo recebido), score (inteiro 0-100), reason (até 140 caracteres,
em português, dizendo por que funciona ou não), hook (até 8 palavras, texto de gancho para a tela),
standalone (true se faz sentido sozinho). Avalie todos os itens."""

PROFILE_HINTS = {
    "viral": "clipe de 15 a 60 s; precisa prender rápido.",
    "monetizacao": "clipe de 61 a 120 s; precisa de arco completo (preparação, clímax e reação).",
}

ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "id": {"type": "string"}, "score": {"type": "integer"}, "reason": {"type": "string"},
        "hook": {"type": "string"}, "standalone": {"type": "boolean"},
    },
    "required": ["id", "score", "reason", "hook", "standalone"],
}


def _retry_after(e: urllib.error.HTTPError, default: int = 20, cap: int = 60) -> int:
    try:
        return max(1, min(cap, int(float((e.headers or {}).get("Retry-After", default)))))
    except (TypeError, ValueError):
        return default


CRITERIA = ("gancho", "emocao", "visual", "final", "contexto")
REVIEW_SCHEMA = {
    "type": "object",
    "properties": {
        "descricao": {"type": "string"}, "motivo": {"type": "string"}, "risco": {"type": "string"},
        **{c: {"type": "integer"} for c in CRITERIA},
        "inicio": {"type": "number"}, "fim": {"type": "number"},
    },
    "required": ["descricao", "motivo", "risco", *CRITERIA, "inicio", "fim"],
}
REVIEW_INSTRUCTIONS = """Você é editor de cortes virais de lives de games (principalmente GTA), em português do Brasil.
ASSISTA e OUÇA o vídeo anexo (um trecho de live: o momento escolhido começa em {pre:.0f} s e termina
{post:.0f} s antes do fim do vídeo) e
avalie o potencial de virar um clipe curto viral (TikTok, Shorts, Reels). Perfil "{profile}": {profile_hint}

Calibração (use a escala inteira e diferencie os trechos entre si):
- 90–100: viral quase certo; raro (cerca de 1 em cada 20 trechos de uma live).
- 70–89: bom, vale postar.  - 50–69: mediano, só se faltar coisa melhor.  - abaixo de 50: fraco.
Numa live típica a maioria dos trechos fica entre 40 e 75. Não dê a mesma nota para tudo.

Dê notas de 0 a 100:
- gancho: os 3 primeiros segundos do corte sugerido prendem quem está rolando o feed?
- emocao: humor, tensão, susto, raiva, euforia; reação forte dos streamers (gritos, risadas).
- visual: o que acontece na tela é forte (ação, explosão, perseguição, cena marcante)?
- final: o corte tem desfecho (punchline, reação, conclusão) ou termina no meio?
- contexto: quem não viu a live entende sozinho?

Sugira inicio e fim do corte em SEGUNDOS CONTADOS DESDE O INÍCIO DESTE VÍDEO: comece direto no gancho
(sem enrolação) e termine logo depois da reação/desfecho.
descricao: o que acontece (tela + áudio), 1–2 frases. motivo: por que funciona ou não, 1–2 frases.
risco: o que pode atrapalhar (contexto, direitos autorais de música, parado) ou "nenhum".
Escreva em português. Streamer: {streamer}.

Transcrição do trecho (pode ter erros):
{transcript}
"""


def _range_hint(ctx: dict) -> str:
    rng = ctx.get("range")
    if not rng:
        return PROFILE_HINTS.get(ctx.get("profile", "viral"), "")
    lo, hi = rng
    arc = "; precisa de arco completo (preparação, clímax e reação)" if lo > 60 else "; precisa prender rápido"
    return f"clipe de {lo:.0f} a {hi:.0f} s{arc}. O corte sugerido (inicio/fim) deve respeitar essa duração."


def _review_prompt(ctx: dict) -> str:
    w0, w1 = ctx.get("window", [0, 0])
    text = REVIEW_INSTRUCTIONS.format(
        pre=ctx.get("pre", ctx.get("margin", 15)), post=ctx.get("post", ctx.get("margin", 15)),
        profile=ctx.get("profile", "viral"),
        profile_hint=_range_hint(ctx),
        streamer=ctx.get("streamer") or "desconhecido", transcript=(ctx.get("transcript") or "")[:4000])
    examples = ctx.get("examples") or []
    if examples:
        lines = [f"- [{'BOM' if e.get('label') == 'good' else 'RUIM'}] {e.get('description') or e.get('text', '')[:200]}"
                 f" — {e.get('reason', '')}" for e in examples]
        text += ("\nGosto do usuário (exemplos que ele já avaliou; use para calibrar as notas):\n"
                 + "\n".join(lines) + "\n")
    return text + f"\nDuração deste vídeo: {w1 - w0:.0f} s."


def _clamp_review(d):
    """Modelos pequenos às vezes devolvem 101 ou 77.6: ajusta para inteiro 0–100 em vez de descartar."""
    if isinstance(d, dict):
        for c in CRITERIA:
            v = d.get(c)
            if isinstance(v, (int, float)) and not isinstance(v, bool):
                d[c] = int(round(min(100.0, max(0.0, float(v)))))
    return d


def _valid_review(d) -> bool:
    if not isinstance(d, dict):
        return False
    for c in CRITERIA:
        v = d.get(c)
        if not isinstance(v, int) or isinstance(v, bool) or not 0 <= v <= 100:
            return False
    if not all(isinstance(d.get(k), (int, float)) and not isinstance(d.get(k), bool) for k in ("inicio", "fim")):
        return False
    return all(isinstance(d.get(k), str) for k in ("descricao", "motivo", "risco"))


_POST_ITEM = {"type": "object", "properties": {
    "title": {"type": "string"}, "description": {"type": "string"},
    "hashtags": {"type": "array", "items": {"type": "string"}},
    "tags": {"type": "array", "items": {"type": "string"}}},
    "required": ["title", "description", "hashtags"]}
POSTS_SCHEMA = {"type": "array", "items": {"type": "object", "properties": {
    "id": {"type": "string"}, "hook": {"type": "string"},
    **{p: _POST_ITEM for p in ("tiktok", "shorts", "reels", "kwai")}},
    "required": ["id", "hook", "tiktok", "shorts", "reels", "kwai"]}}
POSTS_INSTRUCTIONS = """Você é social media de cortes de lives de games (GTA) no Brasil.
Para cada clipe, escreva os textos de postagem em {lang}:
- hook: frase de gancho que aparece escrita no vídeo nos 3 primeiros segundos (até 8 palavras, sem hashtag,
  provoca curiosidade, sem entregar o final; ex.: "Ele não esperava por isso").
- Para cada rede (tiktok, shorts, reels, kwai): title, description e hashtags.
  - title: o melhor título possível para ganhar views (até 70 caracteres). Use o que funciona em cortes virais:
    curiosidade aberta ("…e ninguém esperava isso"), reação forte, número ou detalhe concreto do momento,
    palavra-chave do jogo no começo (GTA 6). Nada de clickbait mentiroso, nada de CAIXA ALTA no título todo,
    no máximo 1 emoji.
  - description: 1–2 frases + uma pergunta que faça a pessoa comentar.
  - hashtags: tiktok/kwai/shorts 6–8 (amplas como #gta6 #gaming + de nicho do momento + nome do streamer
    sem espaços); reels no máximo 5 (o Instagram limita a 5).
- Só em shorts, também tags: 10–15 palavras-chave de busca do YouTube, sem #, do mais buscado para o mais
  específico (ex.: "gta 6", "gta vi", "trailer gta 6", nome do streamer, assunto do momento).
- Adapte o tom: tiktok/kwai mais informal e com gírias; shorts direto e com palavra-chave no início do título
  (bom para busca); reels mais visual e com emoji.
- Em shorts, pense em busca + retenção do YouTube:
  - title: palavra-chave mais buscada nas primeiras palavras ("GTA 6: …"), curiosidade no final, até 70 caracteres.
  - description: 2–3 frases naturais que repetem as palavras-chave do título (GTA 6, trailer, nome do
    streamer, assunto do momento), depois uma pergunta para comentar e um convite curto para se inscrever.
  - hashtags: as 3 primeiras são as mais fortes (o YouTube mostra essas acima do título), ex.: #gta6 #gta #shorts.
- Hashtags do jogo devem ser do jogo certo: se for GTA 6 (trailer, gameplay ou react de GTA VI), use #gta6
  e nunca #gtav; se não der para saber a versão, use só #gta.
- Não invente fatos que não estão na descrição/transcrição. Não use o nome de pessoas reais além do streamer.
Clipes:
"""


def _posts_prompt(items: list[dict], language: str) -> str:
    lang = "português do Brasil" if (language or "pt").startswith("pt") else f"no idioma '{language}'"
    return POSTS_INSTRUCTIONS.format(lang=lang) + json.dumps(items, ensure_ascii=False)


class LLMError(Exception):
    pass


class LLMRateLimited(LLMError):
    """Cota/limite de requisições atingido: não adianta tentar de novo agora."""


class LLMClient(Protocol):
    def score(self, items: list[dict], profile: str, language: str) -> list[dict]: ...


def _prompt(items: list[dict], profile: str, language: str) -> str:
    head = INSTRUCTIONS.format(profile=profile, profile_hint=PROFILE_HINTS.get(profile, ""))
    if language and not language.startswith("pt"):
        head += f"\nA transcrição está em '{language}'; escreva reason e hook em português mesmo assim."
    return head + "\n\nTrechos:\n" + json.dumps(items, ensure_ascii=False)


class GeminiClient:
    def __init__(self, api_key: str, model: str, timeout: int, tracker: UsageTracker | None = None):
        self._key = api_key
        self.model = model
        self.timeout = timeout
        self.tracker = tracker

    def _record(self, inp: int, out: int, ok: bool, rate_limited: bool = False) -> None:
        if self.tracker:
            self.tracker.record("gemini", self.model, inp, out, ok=ok, rate_limited=rate_limited)

    def score(self, items: list[dict], profile: str, language: str) -> list[dict]:
        body = {
            "contents": [{"parts": [{"text": _prompt(items, profile, language)}]}],
            "generationConfig": {
                "responseMimeType": "application/json",
                "responseSchema": {"type": "array", "items": ITEM_SCHEMA},
                "temperature": 0.2,
            },
        }
        data = self._post(body)
        return data if isinstance(data, list) else []

    def review_video(self, video_bytes: bytes, context: dict) -> dict:
        """A IA assiste o mini-vídeo do trecho e devolve critérios, corte sugerido e textos."""
        body = {
            "contents": [{"parts": [
                {"inline_data": {"mime_type": "video/mp4", "data": base64.b64encode(video_bytes).decode("ascii")}},
                {"text": _review_prompt(context)},
            ]}],
            "generationConfig": {
                "responseMimeType": "application/json", "responseSchema": REVIEW_SCHEMA,
                "mediaResolution": "MEDIA_RESOLUTION_LOW", "temperature": 0.2,
            },
        }
        data = _clamp_review(self._post(body))
        if not _valid_review(data):
            raise LLMError("revisão em vídeo veio incompleta")
        return data

    def write_posts(self, items: list[dict], language: str) -> list[dict]:
        """Gancho + título/descrição/hashtags para TikTok, Shorts, Reels e Kwai (um item por clipe)."""
        body = {
            "contents": [{"parts": [{"text": _posts_prompt(items, language)}]}],
            "generationConfig": {"responseMimeType": "application/json", "responseSchema": POSTS_SCHEMA,
                                 "temperature": 0.7},
        }
        data = self._post(body)
        return data if isinstance(data, list) else []

    def _post(self, body: dict):
        req = urllib.request.Request(
            GEMINI_URL.format(model=self.model), data=json.dumps(body).encode("utf-8"),
            headers={"x-goog-api-key": self._key, "Content-Type": "application/json"}, method="POST",
        )
        waited_429 = False
        for attempt in range(len(RETRY_DELAYS) + 2):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as r:
                    payload = json.load(r)
                break
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    if not waited_429:  # o plano grátis tem limite por minuto: espera e tenta de novo
                        waited_429 = True
                        self._record(0, 0, ok=False)
                        time.sleep(_retry_after(e))
                        continue
                    self._record(0, 0, ok=False, rate_limited=True)
                    raise LLMRateLimited("limite do Gemini atingido (HTTP 429)") from e
                self._record(0, 0, ok=False)
                if e.code in _TRANSIENT and attempt < len(RETRY_DELAYS):
                    time.sleep(RETRY_DELAYS[attempt])
                    continue
                raise LLMError(f"Gemini HTTP {e.code}") from e
            except ValueError as e:  # corpo que não é JSON (página de erro de proxy etc.)
                self._record(0, 0, ok=False)
                raise LLMError("resposta do Gemini não é JSON") from e
            except (OSError, TimeoutError) as e:
                self._record(0, 0, ok=False)
                if attempt < len(RETRY_DELAYS):
                    time.sleep(RETRY_DELAYS[attempt])
                    continue
                raise LLMError(f"Gemini sem resposta: {e}") from e
        meta = payload.get("usageMetadata", {})
        out_tokens = meta.get("candidatesTokenCount", 0) + meta.get("thoughtsTokenCount", 0)
        self._record(meta.get("promptTokenCount", 0), out_tokens, ok=True)
        try:
            text = payload["candidates"][0]["content"]["parts"][0]["text"]
            return json.loads(text)
        except (KeyError, IndexError, TypeError, json.JSONDecodeError) as e:
            raise LLMError("resposta do Gemini fora do formato esperado") from e


class AnthropicClient:
    """Usa o SDK oficial; fallback de recusa no servidor ligado ("default")."""

    def __init__(self, api_key: str, model: str, timeout: int, tracker: UsageTracker | None = None):
        import anthropic

        self._anthropic = anthropic
        self.client = anthropic.Anthropic(api_key=api_key, timeout=timeout, max_retries=2)
        self.model = model
        self.tracker = tracker

    def score(self, items: list[dict], profile: str, language: str) -> list[dict]:
        a = self._anthropic
        schema = {"type": "object", "additionalProperties": False, "required": ["moments"],
                  "properties": {"moments": {"type": "array",
                                             "items": {**ITEM_SCHEMA, "additionalProperties": False}}}}
        try:
            resp = self.client.beta.messages.create(
                model=self.model, max_tokens=16000,
                betas=["server-side-fallback-2026-07-01"], fallbacks="default",
                output_config={"effort": "low", "format": {"type": "json_schema", "schema": schema}},
                messages=[{"role": "user", "content": _prompt(items, profile, language)}],
            )
        except a.RateLimitError as e:
            if self.tracker:
                self.tracker.record("anthropic", self.model, 0, 0, ok=False, rate_limited=True)
            raise LLMRateLimited("limite da Anthropic atingido") from e
        except a.APIStatusError as e:
            if self.tracker:
                self.tracker.record("anthropic", self.model, 0, 0, ok=False)
            raise LLMError(f"Anthropic HTTP {e.status_code}: {e.message[:120]}") from e
        except a.APIConnectionError as e:
            raise LLMError("Anthropic sem conexão") from e
        if self.tracker:
            self.tracker.record("anthropic", self.model, resp.usage.input_tokens, resp.usage.output_tokens, ok=True)
        if resp.stop_reason == "refusal":
            raise LLMError("a IA recusou avaliar estes trechos")
        text = next((b.text for b in resp.content if b.type == "text"), "")
        try:
            data = json.loads(text)
            return data.get("moments", []) if isinstance(data, dict) else []
        except json.JSONDecodeError as e:
            raise LLMError("resposta da Anthropic fora do formato esperado") from e


def _key(name: str, env_file: Path) -> str:
    return (os.environ.get(name) or dotenv_values(env_file).get(name) or "").strip()


def make_client(settings: Settings, env_file: Path = Path(".env"), model: str | None = None) -> LLMClient | None:
    """Cliente do provedor configurado; None se for 'none' ou se faltar a chave (com aviso)."""
    cfg = settings.llm
    if cfg.provider == "none":
        return None
    tracker = UsageTracker(Path(settings.paths.data_dir) / "usage.json")
    model = model or cfg.model or DEFAULT_MODELS[cfg.provider]
    if cfg.provider == "gemini":
        key = _key("GEMINI_API_KEY", env_file)
        if not key:
            log.warning("GEMINI_API_KEY ausente no .env — análise só com sinais locais.")
            return None
        return GeminiClient(key, model, cfg.timeout, tracker)
    key = _key("ANTHROPIC_API_KEY", env_file)
    if not key:
        log.warning("ANTHROPIC_API_KEY ausente no .env — análise só com sinais locais.")
        return None
    return AnthropicClient(key, model, cfg.timeout, tracker)
