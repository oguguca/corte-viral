"""Provedores de IA plugáveis para avaliar trechos: Gemini (grátis), Anthropic, ou nenhum.

As chaves vêm do .env / variáveis de ambiente e nunca vão para URL, log ou config.yaml.
"""
from __future__ import annotations

import json
import logging
import os
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
        req = urllib.request.Request(
            GEMINI_URL.format(model=self.model), data=json.dumps(body).encode("utf-8"),
            headers={"x-goog-api-key": self._key, "Content-Type": "application/json"}, method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                payload = json.load(r)
        except urllib.error.HTTPError as e:
            limited = e.code == 429
            self._record(0, 0, ok=False, rate_limited=limited)
            if limited:
                raise LLMRateLimited("limite do Gemini atingido (HTTP 429)") from e
            raise LLMError(f"Gemini HTTP {e.code}") from e
        except (OSError, TimeoutError) as e:
            self._record(0, 0, ok=False)
            raise LLMError(f"Gemini sem resposta: {e}") from e
        meta = payload.get("usageMetadata", {})
        out_tokens = meta.get("candidatesTokenCount", 0) + meta.get("thoughtsTokenCount", 0)
        self._record(meta.get("promptTokenCount", 0), out_tokens, ok=True)
        try:
            text = payload["candidates"][0]["content"]["parts"][0]["text"]
            data = json.loads(text)
        except (KeyError, IndexError, json.JSONDecodeError) as e:
            raise LLMError("resposta do Gemini fora do formato esperado") from e
        return data if isinstance(data, list) else []


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
            return json.loads(text).get("moments", [])
        except json.JSONDecodeError as e:
            raise LLMError("resposta da Anthropic fora do formato esperado") from e


def _key(name: str, env_file: Path) -> str:
    return (os.environ.get(name) or dotenv_values(env_file).get(name) or "").strip()


def make_client(settings: Settings, env_file: Path = Path(".env")) -> LLMClient | None:
    """Cliente do provedor configurado; None se for 'none' ou se faltar a chave (com aviso)."""
    cfg = settings.llm
    if cfg.provider == "none":
        return None
    tracker = UsageTracker(Path(settings.paths.data_dir) / "usage.json")
    model = cfg.model or DEFAULT_MODELS[cfg.provider]
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
