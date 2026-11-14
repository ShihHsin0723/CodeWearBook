"""Per-query wall time and provider-reported LLM usage for evaluations."""

from contextvars import ContextVar
from time import perf_counter


_active_calls = ContextVar("active_llm_calls", default=None)
_call_usage = ContextVar("llm_call_usage", default=None)
_call_attempts = ContextVar("llm_call_attempts", default=0)


class QueryMetrics:
    def __init__(self):
        self.calls = []
        self.elapsed_seconds = None

    def __enter__(self):
        self._token = _active_calls.set(self.calls)
        self._start = perf_counter()
        return self

    def __exit__(self, _type, _value, _traceback):
        self.elapsed_seconds = perf_counter() - self._start
        _active_calls.reset(self._token)

    def as_dict(self):
        input_tokens = sum(call["input_tokens"] or 0 for call in self.calls)
        output_tokens = sum(call["output_tokens"] or 0 for call in self.calls)
        cache_creation_tokens = sum(call["cache_creation_input_tokens"] or 0 for call in self.calls)
        cache_read_tokens = sum(call["cache_read_input_tokens"] or 0 for call in self.calls)
        total_input_tokens = input_tokens + cache_creation_tokens + cache_read_tokens
        return {
            "query_elapsed_seconds": self.elapsed_seconds,
            "llm_elapsed_seconds": sum(call["elapsed_seconds"] for call in self.calls),
            "llm_call_count": len(self.calls),
            "llm_input_tokens": input_tokens,
            "llm_output_tokens": output_tokens,
            "llm_total_input_tokens": total_input_tokens,
            "llm_total_tokens": total_input_tokens + output_tokens,
            "llm_usage_complete": all(
                call["input_tokens"] is not None and call["output_tokens"] is not None
                for call in self.calls
            ),
            "llm_calls": self.calls,
        }


def begin_call():
    return _call_usage.set(None), _call_attempts.set(0)


def end_call(tokens):
    usage = _call_usage.get()
    attempts = _call_attempts.get()
    _call_usage.reset(tokens[0])
    _call_attempts.reset(tokens[1])
    return usage, attempts


def set_call_usage(model, input_tokens, output_tokens, **extra):
    _call_usage.set({"model": model, "input_tokens": input_tokens,
                     "output_tokens": output_tokens, **extra})


def set_call_attempts(attempts):
    _call_attempts.set(attempts)


def record_call(provider, model, elapsed_seconds, usage, attempts, error):
    calls = _active_calls.get()
    if calls is not None:
        calls.append({
            "provider": provider,
            "model": (usage or {}).get("model") or model,
            "elapsed_seconds": elapsed_seconds,
            "attempts": attempts,
            "input_tokens": (usage or {}).get("input_tokens"),
            "output_tokens": (usage or {}).get("output_tokens"),
            "cache_creation_input_tokens": (usage or {}).get("cache_creation_input_tokens", 0),
            "cache_read_input_tokens": (usage or {}).get("cache_read_input_tokens", 0),
            "cache_creation_1h_input_tokens": (usage or {}).get("cache_creation_1h_input_tokens", 0),
            "cached_input_tokens": (usage or {}).get("cached_input_tokens", 0),
            "error": error,
        })
