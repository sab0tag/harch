"""The only place that talks to the Anthropic API (docs/07_ai.md).

* The key is read from the environment here and nowhere else; it never reaches settings,
  templates, logs or AIRequestLog.
* Structured answers come from structured output (a JSON schema the API enforces), not free text.
* Every call — successful, failed or refused by the daily limit — writes an AIRequestLog row.
* Tests never reach the network: the transport is swapped with `use_transport()`.

Nothing here saves domain objects. Callers turn the returned dict into a draft for a human.
"""
import json
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass

from django.conf import settings
from django.utils import timezone

from .models import AIRequestLog

KEY_ENV = "ANTHROPIC_API_KEY"

# Text-only tasks whose output a person always reviews and whose numbers the system recomputes
# go to the cheaper model (ANTHROPIC_MODEL_LIGHT). Card photos (precision matters) and product
# nutrition estimates stay on the main model until an eval shows the light one is good enough.
LIGHT_FEATURES = frozenset({"reference_duplicates", "menu_review", "dish_draft", "dish_technology", "healthcheck_light"})


class AIUnavailable(Exception):
    """AI is switched off or no key is configured — the UI simply hides AI buttons."""


class AILimitExceeded(Exception):
    pass


class AIError(Exception):
    """The call failed; the message is safe to show to a user (no key, no internals)."""


@dataclass
class Reply:
    data: dict
    input_tokens: int | None = None
    output_tokens: int | None = None


def _key() -> str:
    return os.environ.get(KEY_ENV, "").strip()


def key_configured() -> bool:
    return bool(_key())


def is_available() -> bool:
    return bool(settings.AI_ENABLED) and key_configured()


def model_for(feature: str) -> str:
    light = settings.ANTHROPIC_MODEL_LIGHT
    return light if light and feature in LIGHT_FEATURES else settings.ANTHROPIC_MODEL


def used_today() -> int:
    start = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    return AIRequestLog.objects.filter(created_at__gte=start).exclude(
        status=AIRequestLog.Status.REJECTED_BY_LIMIT
    ).count()


# ------------------------------------------------------------------ request shape
# Token economy (docs/07_ai.md, «Токени»):
# * Structured output (output_config.format) instead of a forced tool call: no tool definition in
#   the prompt, and the only form Claude Opus 5.5 accepts (it rejects forced tool_choice with a 400).
# * Thinking can't be switched off on Opus 5.5 — effort is the lever. Our tasks are short
#   extractions a person reviews, so «low» unless a feature needs more (AI_EFFORT_BY_FEATURE).
# * Haiku 4.5 takes no effort parameter and doesn't think unless asked — we never ask.
# * No prompt caching: prefixes are short and calls are one-off, a cache write would cost more.
EFFORT_BY_FEATURE = {"card_import": "medium", "product_estimate": "medium"}   # numbers read off photos / tables
THINKING_HEADROOM = 3000        # low-effort thinking shares max_tokens with the answer
UNSUPPORTED_SCHEMA_KEYS = {"minItems", "maxItems", "minimum", "maximum", "exclusiveMinimum",
                           "exclusiveMaximum", "multipleOf", "minLength", "maxLength", "pattern"}


def takes_effort(model: str) -> bool:
    """Effort errors on Haiku 4.5 and Sonnet 4.5; every current Opus/Sonnet/Fable model accepts it."""
    return not (model.startswith("claude-haiku") or model.startswith("claude-sonnet-4-5"))


def strict_schema(schema):
    """Our tool-style schemas → what structured outputs accept: additionalProperties false on every
    object, no numeric/length/array constraints (code validates those), unions as anyOf."""
    if isinstance(schema, list):
        return [strict_schema(s) for s in schema]
    if not isinstance(schema, dict):
        return schema
    out = {k: strict_schema(v) for k, v in schema.items() if k not in UNSUPPORTED_SCHEMA_KEYS}
    if isinstance(out.get("type"), list):
        types = out.pop("type")
        out = {"anyOf": [{**out, "type": t} if t != "null" else {"type": "null"} for t in types]}
        return out
    if out.get("type") == "object":
        out["additionalProperties"] = False
    return out


def build_request(*, model, feature, system, content, schema, max_tokens) -> dict:
    output_config = {"format": {"type": "json_schema", "schema": strict_schema(schema)}}
    if takes_effort(model):
        output_config["effort"] = EFFORT_BY_FEATURE.get(feature, settings.AI_EFFORT)
        max_tokens += THINKING_HEADROOM
    return {"model": model, "max_tokens": max_tokens, "system": system,
            "messages": [{"role": "user", "content": content}], "output_config": output_config}


def parse_response(msg) -> "Reply":
    if msg.stop_reason == "refusal":
        raise AIError("Модель відмовилась відповідати на цей запит.")
    if msg.stop_reason == "max_tokens":
        raise AIError("Відповідь ШІ обрізано (завеликий запит) — спробуйте меншу пачку.")
    text = next((b.text for b in msg.content if getattr(b, "type", "") == "text"), None)
    try:
        data = json.loads(text) if text else None
    except ValueError:
        data = None
    if not isinstance(data, dict):
        raise AIError("Модель не повернула структуровану відповідь.")
    return Reply(data, msg.usage.input_tokens, msg.usage.output_tokens)


# ------------------------------------------------------------------ transport
def _anthropic_transport(*, model, system, content, tool, max_tokens, feature=""):
    import anthropic

    client = anthropic.Anthropic(api_key=_key(), timeout=settings.AI_TIMEOUT_SECONDS, max_retries=1)
    try:
        msg = client.messages.create(**build_request(
            model=model, feature=feature, system=system, content=content,
            schema=tool["input_schema"], max_tokens=max_tokens,
        ))
    except anthropic.AuthenticationError as exc:
        raise AIError("Ключ Anthropic не прийнято — перевірте ANTHROPIC_API_KEY у .env на сервері.") from exc
    except anthropic.RateLimitError as exc:
        raise AIError("Сервіс ШІ перевантажений або вичерпано ліміт акаунта. Спробуйте пізніше.") from exc
    except anthropic.APITimeoutError as exc:
        raise AIError("Сервіс ШІ не відповів вчасно. Спробуйте ще раз.") from exc
    except anthropic.APIConnectionError as exc:
        raise AIError("Немає з'єднання з сервісом ШІ.") from exc
    except anthropic.APIStatusError as exc:
        raise AIError(f"Сервіс ШІ повернув помилку {exc.status_code}.") from exc
    return parse_response(msg)


_transport = _anthropic_transport


@contextmanager
def use_transport(fn):
    """Swap the network call (tests, evals). fn(model=, system=, content=, tool=, max_tokens=) -> Reply."""
    global _transport
    previous, _transport = _transport, fn
    try:
        yield
    finally:
        _transport = previous


# ------------------------------------------------------------------ public API
def extract(*, user, feature: str, system_prompt: str, content_blocks: list, tool_schema: dict,
            tool_name: str = "record", request_meta: dict | None = None, max_tokens: int = 4096) -> dict:
    """Run one structured extraction and return the tool input as a dict.

    Raises AIUnavailable, AILimitExceeded or AIError; every outcome except AIUnavailable is logged.
    """
    if not is_available():
        raise AIUnavailable("ШІ вимкнено або ключ не налаштовано.")
    model = model_for(feature)
    log = AIRequestLog(user=user, feature=feature, model=model, request_meta=request_meta or {})
    if used_today() >= settings.AI_DAILY_REQUEST_LIMIT:
        log.status = AIRequestLog.Status.REJECTED_BY_LIMIT
        log.error = f"Денний ліміт {settings.AI_DAILY_REQUEST_LIMIT} запитів вичерпано."
        log.save()
        raise AILimitExceeded(log.error + " Спробуйте завтра або зверніться до адміністратора.")
    tool = {"name": tool_name, "description": "Return the extracted data.", "input_schema": tool_schema}
    started = time.monotonic()
    try:
        reply = _transport(model=model, system=system_prompt, content=content_blocks, tool=tool,
                           max_tokens=max_tokens, feature=feature)
    except AIError as exc:
        _finish(log, started, status=AIRequestLog.Status.ERROR, error=str(exc))
        raise
    except Exception as exc:  # never leak internals (or anything key-like) to the user
        _finish(log, started, status=AIRequestLog.Status.ERROR, error=type(exc).__name__)
        raise AIError("Не вдалося отримати відповідь ШІ.") from exc
    _finish(log, started, status=AIRequestLog.Status.OK, response_json=reply.data,
            input_tokens=reply.input_tokens, output_tokens=reply.output_tokens)
    return reply.data


def _finish(log, started, **fields):
    for k, v in fields.items():
        setattr(log, k, v)
    log.duration_ms = int((time.monotonic() - started) * 1000)
    log.save()


PING_SCHEMA = {
    "type": "object",
    "properties": {"ok": {"type": "boolean"}},
    "required": ["ok"],
}


def check_connection(user=None) -> dict:
    """Smallest possible real call per configured model, to confirm the key and models work.

    Costs a few tokens each. Raises AIError naming the model that failed.
    """
    results = {}
    for feature, model in (("healthcheck", settings.ANTHROPIC_MODEL), ("healthcheck_light", settings.ANTHROPIC_MODEL_LIGHT)):
        if not model or model in results:
            continue
        try:
            results[model] = extract(
                user=user, feature=feature, tool_name="ping", tool_schema=PING_SCHEMA, max_tokens=50,
                system_prompt="Health check. Answer with ok=true.",
                content_blocks=[{"type": "text", "text": "ping"}],
            )
        except AIError as exc:
            raise AIError(f"{model}: {exc}") from exc
    return results


def status() -> dict:
    """What the admin status page shows. Deliberately contains no part of the key."""
    now = timezone.localtime()
    month = AIRequestLog.objects.filter(created_at__gte=now.replace(day=1, hour=0, minute=0, second=0, microsecond=0))
    from django.db.models import Sum

    tokens = month.aggregate(i=Sum("input_tokens"), o=Sum("output_tokens"))
    return {
        "enabled": bool(settings.AI_ENABLED),
        "key_configured": key_configured(),
        "available": is_available(),
        "model": settings.ANTHROPIC_MODEL,
        "model_light": settings.ANTHROPIC_MODEL_LIGHT or settings.ANTHROPIC_MODEL,
        "light_features": sorted(LIGHT_FEATURES - {"healthcheck_light"}),
        "effort": settings.AI_EFFORT if takes_effort(settings.ANTHROPIC_MODEL) else "—",
        "used_today": used_today(),
        "daily_limit": settings.AI_DAILY_REQUEST_LIMIT,
        "month_calls": month.count(),
        "month_input_tokens": tokens["i"] or 0,
        "month_output_tokens": tokens["o"] or 0,
    }
