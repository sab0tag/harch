"""The only place that talks to the Anthropic API (docs/07_ai.md).

* The key is read from the environment here and nowhere else; it never reaches settings,
  templates, logs or AIRequestLog.
* Structured answers come from a forced tool call with a JSON schema, not from parsing text.
* Every call — successful, failed or refused by the daily limit — writes an AIRequestLog row.
* Tests never reach the network: the transport is swapped with `use_transport()`.

Nothing here saves domain objects. Callers turn the returned dict into a draft for a human.
"""
import os
import time
from contextlib import contextmanager
from dataclasses import dataclass

from django.conf import settings
from django.utils import timezone

from .models import AIRequestLog

KEY_ENV = "ANTHROPIC_API_KEY"


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


def used_today() -> int:
    start = timezone.localtime().replace(hour=0, minute=0, second=0, microsecond=0)
    return AIRequestLog.objects.filter(created_at__gte=start).exclude(
        status=AIRequestLog.Status.REJECTED_BY_LIMIT
    ).count()


# ------------------------------------------------------------------ transport
def _anthropic_transport(*, system, content, tool, max_tokens):
    import anthropic

    client = anthropic.Anthropic(api_key=_key(), timeout=settings.AI_TIMEOUT_SECONDS, max_retries=1)
    try:
        msg = client.messages.create(
            model=settings.ANTHROPIC_MODEL,
            max_tokens=max_tokens,
            system=system,
            messages=[{"role": "user", "content": content}],
            tools=[tool],
            tool_choice={"type": "tool", "name": tool["name"]},
        )
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
    block = next((b for b in msg.content if getattr(b, "type", "") == "tool_use"), None)
    if block is None:
        raise AIError("Модель не повернула структуровану відповідь.")
    return Reply(dict(block.input), msg.usage.input_tokens, msg.usage.output_tokens)


_transport = _anthropic_transport


@contextmanager
def use_transport(fn):
    """Swap the network call (tests, evals). fn(system=, content=, tool=, max_tokens=) -> Reply."""
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
    log = AIRequestLog(user=user, feature=feature, model=settings.ANTHROPIC_MODEL, request_meta=request_meta or {})
    if used_today() >= settings.AI_DAILY_REQUEST_LIMIT:
        log.status = AIRequestLog.Status.REJECTED_BY_LIMIT
        log.error = f"Денний ліміт {settings.AI_DAILY_REQUEST_LIMIT} запитів вичерпано."
        log.save()
        raise AILimitExceeded(log.error + " Спробуйте завтра або зверніться до адміністратора.")
    tool = {"name": tool_name, "description": "Return the extracted data.", "input_schema": tool_schema}
    started = time.monotonic()
    try:
        reply = _transport(system=system_prompt, content=content_blocks, tool=tool, max_tokens=max_tokens)
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
    """Smallest possible real call, to confirm the key and model work. Costs a few tokens."""
    return extract(
        user=user, feature="healthcheck", tool_name="ping", tool_schema=PING_SCHEMA, max_tokens=50,
        system_prompt="Health check. Call the tool with ok=true.",
        content_blocks=[{"type": "text", "text": "ping"}],
    )


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
        "used_today": used_today(),
        "daily_limit": settings.AI_DAILY_REQUEST_LIMIT,
        "month_calls": month.count(),
        "month_input_tokens": tokens["i"] or 0,
        "month_output_tokens": tokens["o"] or 0,
    }
