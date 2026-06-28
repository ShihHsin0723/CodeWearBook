from functools import lru_cache

from transformers import AutoTokenizer, AutoModelForCausalLM, AutoConfig, pipeline
import torch
import requests
import time
from config import (
    LLM_SOURCE,
    LlmSource,
    CACHE_DIR,
    MODEL_PATH,
    ANTROPIC_API_MODEL,
    GEMINI_API_MODEL,
    OPENAI_API_MODEL,
    TOGETHER_API_MODEL,
)
from get_llm_api_keys import get_api_keys

DEFAULT_LLM_TIMEOUT_SECONDS = 90
DEFAULT_LLM_MAX_RETRIES = 3
DEFAULT_LLM_MAX_TOKENS = 2048
LLM_API_FALLBACK_ORDER = (
    LlmSource.ANTROPIC_API,
    LlmSource.OPENAI_API,
    LlmSource.GEMINI_API,
    LlmSource.TOGETHER_API,
)
LLM_PROVIDER_NAMES = {
    LlmSource.ANTROPIC_API: "Anthropic",
    LlmSource.OPENAI_API: "OpenAI",
    LlmSource.GEMINI_API: "Gemini",
    LlmSource.TOGETHER_API: "Together",
}
LLM_PROVIDER_MODELS = {
    LlmSource.ANTROPIC_API: ANTROPIC_API_MODEL,
    LlmSource.OPENAI_API: OPENAI_API_MODEL,
    LlmSource.GEMINI_API: GEMINI_API_MODEL,
    LlmSource.TOGETHER_API: TOGETHER_API_MODEL,
}
LLM_PROVIDER_KEY_NAMES = {
    LlmSource.ANTROPIC_API: "anthropic_api_key",
    LlmSource.OPENAI_API: "openai_api_key",
    LlmSource.GEMINI_API: "gemini_api_key",
    LlmSource.TOGETHER_API: "together_api_key",
}
_ACTIVE_API_PROVIDER = None
_API_HEALTH_CHECKED = False


class LLMProviderUnavailableError(RuntimeError):
    """Raised when a configured provider cannot serve a request."""


@lru_cache(maxsize=1)
def _load_api_keys():
    return get_api_keys()


def _get_api_key(key_name: str):
    return _load_api_keys().get(key_name)


def _is_limit_or_quota_error(response_json):
    error = response_json.get("error", response_json)
    if isinstance(error, dict):
        error_text = " ".join(str(value) for value in error.values())
    else:
        error_text = str(error)
    error_text = error_text.lower()
    return any(
        marker in error_text
        for marker in (
            "rate_limit",
            "rate limit",
            "quota",
            "usage limit",
            "usage limits",
            "insufficient_quota",
            "too many requests",
            "resource_exhausted",
        )
    )


def _raise_for_provider_error(provider_name, response, response_json):
    if response.ok and "error" not in response_json:
        return

    if _is_limit_or_quota_error(response_json) or response.status_code == 429:
        raise LLMProviderUnavailableError(
            f"{provider_name} API limit reached: {response_json}"
        )

    raise LLMProviderUnavailableError(
        f"{provider_name} API request failed with status "
        f"{response.status_code}: {response_json}"
    )


def _provider_call(provider, prompt, timeout):
    if provider == LlmSource.ANTROPIC_API:
        return call_claude_llm(prompt, timeout=timeout)
    if provider == LlmSource.OPENAI_API:
        return call_open_ai_llm(prompt, timeout=timeout)
    if provider == LlmSource.GEMINI_API:
        return call_gemini_llm(prompt, timeout=timeout)
    if provider == LlmSource.TOGETHER_API:
        return call_together_llm(prompt, timeout=timeout)
    raise ValueError(f"Unsupported LLM API provider: {provider}")


def _ordered_api_providers():
    if LLM_SOURCE in LLM_API_FALLBACK_ORDER:
        return LLM_API_FALLBACK_ORDER
    return (LLM_SOURCE,)


def _mark_provider_unavailable(provider, exc):
    provider_name = LLM_PROVIDER_NAMES.get(provider, str(provider))
    print(f"{provider_name} LLM API unavailable; trying next provider. {exc}")


def _select_working_api_provider(timeout=15):
    global _ACTIVE_API_PROVIDER, _API_HEALTH_CHECKED

    if _API_HEALTH_CHECKED and _ACTIVE_API_PROVIDER is not None:
        return _ACTIVE_API_PROVIDER

    failures = []
    health_prompt = "Reply with OK."
    for provider in _ordered_api_providers():
        provider_name = LLM_PROVIDER_NAMES.get(provider, str(provider))
        key_name = LLM_PROVIDER_KEY_NAMES.get(provider)
        if key_name and not _get_api_key(key_name):
            failures.append(f"{provider_name}: missing API key")
            print(f"{provider_name} LLM API unavailable; missing API key.")
            continue

        try:
            _provider_call(provider, health_prompt, timeout=timeout)
        except Exception as exc:
            failures.append(f"{provider_name}: {exc}")
            _mark_provider_unavailable(provider, exc)
            continue

        _ACTIVE_API_PROVIDER = provider
        _API_HEALTH_CHECKED = True
        print(
            f"Using {provider_name} LLM API "
            f"({LLM_PROVIDER_MODELS.get(provider, 'configured model')})."
        )
        return provider

    _API_HEALTH_CHECKED = True
    raise RuntimeError("No configured LLM API provider is available:\n" + "\n".join(failures))


def verify_llm_api_available(timeout=15):
    """Check the API fallback chain once at session startup."""
    if LLM_SOURCE == LlmSource.LOCAL:
        return LLM_SOURCE
    return _select_working_api_provider(timeout=timeout)


def call_llm(prompt=None, timeout: float = DEFAULT_LLM_TIMEOUT_SECONDS):
    global _ACTIVE_API_PROVIDER, _API_HEALTH_CHECKED

    if LLM_SOURCE == LlmSource.LOCAL:
        return call_local_llm()

    failures = []
    preferred_provider = _select_working_api_provider()
    providers = (preferred_provider,) + tuple(
        provider for provider in _ordered_api_providers() if provider != preferred_provider
    )
    for provider in providers:
        try:
            return _provider_call(provider, prompt, timeout)
        except Exception as exc:
            failures.append(f"{LLM_PROVIDER_NAMES.get(provider, provider)}: {exc}")
            _mark_provider_unavailable(provider, exc)
            _ACTIVE_API_PROVIDER = None
            _API_HEALTH_CHECKED = False

    raise RuntimeError("No configured LLM API provider is available:\n" + "\n".join(failures))


def call_local_llm():
    device_map = "auto" if torch.cuda.is_available() else "cpu"
    torch_dtype = torch.float16 if torch.cuda.is_available() else torch.bfloat16

    config = AutoConfig.from_pretrained(MODEL_PATH, cache_dir=CACHE_DIR)
    model = AutoModelForCausalLM.from_pretrained(
        MODEL_PATH,
        cache_dir=CACHE_DIR,
        torch_dtype=torch_dtype,
        device_map=device_map,
        offload_folder="offload"
    )
    tokenizer = AutoTokenizer.from_pretrained(MODEL_PATH, cache_dir=CACHE_DIR)

    pipeline_llm = pipeline("text-generation", model=model,
                            tokenizer=tokenizer, pad_token_id=tokenizer.eos_token_id)
    return pipeline_llm


def _post_with_retries(url, headers, payload, timeout, max_retries=DEFAULT_LLM_MAX_RETRIES):
    """Retry transient HTTP failures such as read timeouts."""
    last_error = None

    for attempt in range(1, max_retries + 1):
        try:
            return requests.post(url, headers=headers, json=payload, timeout=timeout)
        except (requests.exceptions.ReadTimeout, requests.exceptions.ConnectionError) as exc:
            last_error = exc
            if attempt == max_retries:
                break
            time.sleep(min(2 ** (attempt - 1), 4))

    raise last_error


def call_claude_llm(prompt, timeout: float = DEFAULT_LLM_TIMEOUT_SECONDS):
    api_key = _get_api_key('anthropic_api_key')
    if not api_key:
        raise ValueError("ANTHROPIC_API_KEY is missing")

    url = "https://api.anthropic.com/v1/messages"
    headers = {
        "x-api-key": api_key,
        "anthropic-version": "2023-06-01",
        "Content-Type": "application/json"
    }

    payload = {
        "model": ANTROPIC_API_MODEL,
        "max_tokens": DEFAULT_LLM_MAX_TOKENS,
        "temperature": 0.5,
        "messages": [
            {"role": "user", "content": prompt}
        ]
    }

    response = _post_with_retries(
        url,
        headers,
        payload,
        timeout=timeout,
    )

    try:
        response_json = response.json()
    except Exception as e:
        raise RuntimeError(
            f"Failed to parse JSON from Anthropic API: {e}\nRaw response: {response.text}")

    _raise_for_provider_error("Anthropic", response, response_json)

    try:
        return response_json["content"][0]["text"]
    except (KeyError, IndexError):
        raise RuntimeError(
            f"Invalid response from Claude API:\n{response_json}")


def call_open_ai_llm(prompt, timeout: float = DEFAULT_LLM_TIMEOUT_SECONDS):
    api_key = _get_api_key('openai_api_key')
    if not api_key:
        raise ValueError("OPENAI_API_KEY is missing")

    url = "https://api.openai.com/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": OPENAI_API_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "max_completion_tokens": DEFAULT_LLM_MAX_TOKENS
    }

    response = _post_with_retries(
        url,
        headers,
        payload,
        timeout=timeout,
    )

    try:
        response_json = response.json()
    except Exception as e:
        raise RuntimeError(
            f"Failed to parse JSON from OpenAI API: {e}\nRaw response: {response.text}")

    _raise_for_provider_error("OpenAI", response, response_json)

    if "choices" not in response_json:
        raise RuntimeError(
            f"Invalid response from OpenAI API:\n{response_json}")

    return response_json["choices"][0]["message"]["content"]


def call_gemini_llm(prompt, timeout: float = DEFAULT_LLM_TIMEOUT_SECONDS):
    api_key = _get_api_key('gemini_api_key')
    if not api_key:
        raise ValueError("GEMINI_API_KEY is missing")

    url = (
        "https://generativelanguage.googleapis.com/v1beta/models/"
        f"{GEMINI_API_MODEL}:generateContent?key={api_key}"
    )
    headers = {"Content-Type": "application/json"}
    payload = {
        "contents": [
            {
                "role": "user",
                "parts": [{"text": prompt}],
            }
        ],
        "generationConfig": {
            "maxOutputTokens": DEFAULT_LLM_MAX_TOKENS,
            "temperature": 0.5,
        },
    }

    response = _post_with_retries(
        url,
        headers,
        payload,
        timeout=timeout,
    )

    try:
        response_json = response.json()
    except Exception as e:
        raise RuntimeError(
            f"Failed to parse JSON from Gemini API: {e}\nRaw response: {response.text}")

    _raise_for_provider_error("Gemini", response, response_json)

    try:
        return "".join(
            part.get("text", "")
            for part in response_json["candidates"][0]["content"]["parts"]
        )
    except (KeyError, IndexError):
        raise RuntimeError(
            f"Invalid response from Gemini API:\n{response_json}")


def call_together_llm(prompt, timeout: float = DEFAULT_LLM_TIMEOUT_SECONDS):
    api_key = _get_api_key('together_api_key')
    if not api_key:
        raise ValueError("TOGETHER_API_KEY is missing")

    url = "https://api.together.xyz/v1/chat/completions"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": TOGETHER_API_MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0.2,
        "max_tokens": DEFAULT_LLM_MAX_TOKENS
    }

    response = _post_with_retries(
        url,
        headers,
        payload,
        timeout=timeout,
    )

    try:
        response_json = response.json()
    except Exception as e:
        raise RuntimeError(
            f"Failed to parse JSON from Together API: {e}\nRaw response: {response.text}")

    _raise_for_provider_error("Together", response, response_json)

    if "choices" not in response_json:
        raise RuntimeError(
            f"Invalid response from Together API:\n{response_json}")

    return response_json["choices"][0]["message"]["content"]
