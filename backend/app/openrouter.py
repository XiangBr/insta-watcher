"""OpenRouter client for the competitor analysis: the decision model (JEV, by TypeSafe) and a
chat model for the written report. The key goes in the Authorization header only."""

import json
from dataclasses import dataclass
from typing import Any

import httpx

OPENROUTER_HOST = "https://openrouter.ai"
DECISION_TIMEOUT = httpx.Timeout(60.0, connect=10.0)
REPORT_TIMEOUT = httpx.Timeout(300.0, connect=10.0)
# Reasoning models spend part of this before writing; the report itself is ~2-3k tokens.
MAX_REPORT_TOKENS = 12000


class OpenRouterError(Exception):
    """A failed call. The message is safe to store and show."""


@dataclass(frozen=True)
class Decisions:
    answers: dict[str, dict[str, Any]]
    cost_usd: float | None


@dataclass(frozen=True)
class Completion:
    text: str
    cost_usd: float | None
    # finish_reason "length": the answer hit MAX_REPORT_TOKENS and was cut off.
    truncated: bool = False


def _cost(body: dict[str, Any]) -> float | None:
    usage = body.get("usage")
    cost = usage.get("cost") if isinstance(usage, dict) else None
    return float(cost) if isinstance(cost, int | float) else None


class OpenRouterClient:
    def __init__(self, http: httpx.AsyncClient, api_key: str) -> None:
        self._http = http
        self._headers = {
            "Authorization": f"Bearer {api_key}",
            # Optional attribution headers OpenRouter shows in its dashboard.
            "X-Title": "insta-watcher",
        }

    async def _post(self, path: str, payload: dict[str, Any], limit: httpx.Timeout) -> Any:
        try:
            response = await self._http.post(
                f"{OPENROUTER_HOST}{path}", headers=self._headers, json=payload, timeout=limit
            )
        except httpx.HTTPError as exc:
            raise OpenRouterError(f"OpenRouter unreachable ({type(exc).__name__})") from None
        try:
            body = response.json()
        except ValueError:
            body = None
        if response.status_code >= 400:
            message = ""
            if isinstance(body, dict) and isinstance(body.get("error"), dict):
                message = str(body["error"].get("message") or "")[:200]
            raise OpenRouterError(f"OpenRouter HTTP {response.status_code}: {message}".rstrip(": "))
        if not isinstance(body, dict):
            raise OpenRouterError("OpenRouter answered without a JSON object")
        return body

    async def decide(
        self, model: str, state: Any, questions: dict[str, dict[str, Any]]
    ) -> Decisions:
        """One Decisions API call. Questions are answered independently of each other."""
        body = await self._post(
            "/api/alpha/decisions",
            {"model": model, "state": state, "questions": questions},
            DECISION_TIMEOUT,
        )
        answers = body.get("answers")
        if not isinstance(answers, dict):
            raise OpenRouterError("decision model answered without answers")
        return Decisions(
            answers={k: v for k, v in answers.items() if isinstance(v, dict)},
            cost_usd=_cost(body),
        )

    async def complete_json(
        self,
        model: str,
        messages: list[dict[str, str]],
        schema: dict[str, Any] | None = None,
    ) -> Completion:
        """A chat completion asked to answer with a JSON object.

        With a schema, structured output is requested. OpenRouter drops the parameter for
        models that do not support it, so the prompt still has to describe the format.
        """
        response_format: dict[str, Any] = (
            {"type": "json_schema", "json_schema": {"name": "report", "schema": schema}}
            if schema is not None
            else {"type": "json_object"}
        )
        body = await self._post(
            "/api/v1/chat/completions",
            {
                "model": model,
                "messages": messages,
                "response_format": response_format,
                "max_tokens": MAX_REPORT_TOKENS,
                "usage": {"include": True},
            },
            REPORT_TIMEOUT,
        )
        choices = body.get("choices")
        choice = choices[0] if isinstance(choices, list) and choices else None
        message = choice.get("message") if isinstance(choice, dict) else None
        text = message.get("content") if isinstance(message, dict) else None
        finish = choice.get("finish_reason") if isinstance(choice, dict) else None
        if not isinstance(text, str) or not text.strip():
            raise OpenRouterError("model answered without text")
        return Completion(
            text=text,
            cost_usd=_cost(body),
            truncated=finish == "length",
        )


def parse_json_object(text: str) -> dict[str, Any]:
    """The model's JSON, tolerating a ```json fence, prose around it, and raw line breaks
    inside strings (strict=False), which models often write in long texts."""
    start, end = text.find("{"), text.rfind("}")
    if start < 0 or end <= start:
        raise OpenRouterError("model answer has no JSON object")
    try:
        value = json.loads(text[start : end + 1], strict=False)
    except json.JSONDecodeError as exc:
        raise OpenRouterError(
            f"model answer is not valid JSON ({exc.msg}, character {exc.pos})"
        ) from None
    if not isinstance(value, dict):
        raise OpenRouterError("model answer is not a JSON object")
    return value
