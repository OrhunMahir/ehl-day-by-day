"""The model behind the agent, through one OpenAI-compatible client. Switch it with environment variables only:

  AGENT_PROVIDER=qwen       (default) the hosted Qwen3.8-Flash-Next: QWEN_BASE_URL, QWEN_API_KEY
  AGENT_PROVIDER=anthropic  Claude via Anthropic's OpenAI-compatible endpoint: ANTHROPIC_BASE_URL, ANTHROPIC_API_KEY
  AGENT_PROVIDER=openai     OpenAI (e.g. a Codex/GPT model): OPENAI_BASE_URL, OPENAI_API_KEY
  AGENT_PROVIDER=openrouter any OpenRouter model: LLM_PROXY_URL/openrouter/v1, OPENROUTER_API_KEY
  AGENT_MODEL=<model id>    overrides the provider's default model

In a run every base URL already points at the platform's metering proxy; your own keys go in team secrets.
"""
import asyncio
import os
import time

from openai import APIError, APITimeoutError, AsyncOpenAI, RateLimitError

DEFAULT_MODELS = {
    "qwen": "qwen3.8-flash-next",
    "anthropic": "claude-sonnet-5-5",
    "openai": "gpt-5.5",
    "openrouter": "anthropic/claude-sonnet-5.5",
}

# Longest lead call seen in a run was about 90 s; a call that hangs must not eat the run.
CALL_TIMEOUT_SECONDS = 240


def _client_config(provider: str) -> tuple[str, str]:
    """Base URL and key of a provider. Every base URL comes from the run's environment, where it points at the
    platform's metering proxy; there is no address to fall back to."""
    env = os.environ
    if provider == "qwen":
        return env["QWEN_BASE_URL"], env["QWEN_API_KEY"]
    if provider == "anthropic":
        return env["ANTHROPIC_BASE_URL"].rstrip("/") + "/v1", env["ANTHROPIC_API_KEY"]
    if provider == "openai":
        return env["OPENAI_BASE_URL"], env["OPENAI_API_KEY"]
    if provider == "openrouter":
        return env["LLM_PROXY_URL"].rstrip("/") + "/openrouter/v1", env["OPENROUTER_API_KEY"]
    raise SystemExit(f"Unknown AGENT_PROVIDER {provider!r}; use one of {', '.join(DEFAULT_MODELS)}")


class Model:
    def __init__(self, provider: str | None = None, model: str | None = None, reasoning_effort: str | None = None,
                 thinking: bool | None = None, max_tokens: int | None = None):
        # how much the model thinks before it answers; None leaves the provider's default
        self.reasoning_effort = reasoning_effort
        # False switches Qwen's thinking off entirely: a routine step (a click, copying facts) then answers in a few
        # seconds instead of writing thousands of reasoning tokens first (about 40 tokens per second)
        self.thinking = thinking
        self.max_tokens = max_tokens  # upper bound per answer, reasoning included; None: the provider's
        self.provider = provider or os.environ.get("AGENT_PROVIDER", "qwen")
        self.model = model or os.environ.get("AGENT_MODEL") or DEFAULT_MODELS[self.provider]
        base_url, api_key = _client_config(self.provider)
        self.client = AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=CALL_TIMEOUT_SECONDS, max_retries=0)
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0

    async def chat(self, messages: list[dict], tools: list[dict] | None = None, **kwargs):
        """One completion with retries; returns the assistant message."""
        delay = 2.0
        for attempt in range(8):
            try:
                started = time.time()
                args = dict(model=self.model, messages=messages, **kwargs)
                if self.max_tokens:
                    args.setdefault("max_tokens", self.max_tokens)
                if self.thinking is False and self.provider == "qwen":
                    extra = dict(args.get("extra_body") or {})
                    extra["chat_template_kwargs"] = {"enable_thinking": False, **(extra.get("chat_template_kwargs") or {})}
                    args["extra_body"] = extra
                elif self.reasoning_effort:
                    args.setdefault("reasoning_effort", self.reasoning_effort)
                if tools:
                    args["tools"] = tools
                reply = await self.client.chat.completions.create(**args)
                self.calls += 1
                if reply.usage:
                    self.prompt_tokens += reply.usage.prompt_tokens or 0
                    self.completion_tokens += reply.usage.completion_tokens or 0
                if not reply.choices:
                    raise APIError("empty reply", request=None, body=None)
                reply.choices[0].message.elapsed = time.time() - started
                return reply.choices[0].message
            except (APIError, APITimeoutError, RateLimitError) as error:
                status = getattr(error, "status_code", None)
                if status in (400, 401, 403, 404) and attempt > 0:
                    raise
                print(f"[llm] {self.provider} call failed ({status or type(error).__name__}: {str(error)[:200]}); retrying", flush=True)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)
        raise RuntimeError("model unavailable after retries")

    def usage(self) -> str:
        return f"{self.calls} calls, {self.prompt_tokens} prompt + {self.completion_tokens} completion tokens"
