"""The model behind the agent, through one OpenAI-compatible client. Switch it with environment variables only:

  AGENT_PROVIDER=qwen       (default) the hosted Qwen3.8-Flash-Next: QWEN_BASE_URL, QWEN_API_KEY
  AGENT_PROVIDER=anthropic  Claude via Anthropic's OpenAI-compatible endpoint: ANTHROPIC_BASE_URL, ANTHROPIC_API_KEY
  AGENT_PROVIDER=openai     OpenAI: OPENAI_BASE_URL, OPENAI_API_KEY
  AGENT_PROVIDER=openrouter any OpenRouter model: LLM_PROXY_URL/openrouter/v1, OPENROUTER_API_KEY
  AGENT_MODEL=<model id>    overrides the provider's default model

In a run every base URL already points at the platform's metering proxy; your own keys go in team secrets. A team
secret is at least 16 characters long, so the provider and the model of a run cannot be set there: the entry point
names them (`Model(provider=..., model=...)`).
"""
import asyncio
import os
import time
from types import SimpleNamespace

from openai import APIError, APITimeoutError, AsyncOpenAI, RateLimitError

DEFAULT_MODELS = {
    "qwen": "qwen3.8-flash-next",
    "anthropic": "claude-sonnet-5-5",
    "openai": "gpt-6.1-sol",
    "openrouter": "anthropic/claude-sonnet-5.5",
}

DEFAULT_PROVIDER = "qwen"

# Longest lead call seen in a run was about 90 s; a call that hangs must not eat the run.
CALL_TIMEOUT_SECONDS = 240

# Efforts from the most to the least thinking. A model that refuses one gets the next lower one.
EFFORTS = ["xhigh", "high", "medium", "low"]
# A caller that limits the answer wants a short text. A reasoning model counts its thinking against the same limit,
# so the limit is raised by this much and the call thinks as little as the model allows.
REASONING_HEADROOM_TOKENS = 4000


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


class Stepped:
    """The model of one agent that works with two models. The first one answers every step; an answer of which
    `change_for` says why the other model should give it is thrown away and asked again from the other one, so
    nothing of it is acted on. With `stays` the other model then answers every later step as well; without it the
    first model goes on, and the other one is asked only for such answers. When the first model has not answered
    a step within `first_seconds`, the other one answers that step. Callers use it like a `Model`."""

    def __init__(self, first: "Model", then: "Model", change_for, name: str = "", log=print, stays: bool = True,
                 first_seconds: float | None = None):
        self.first, self.then, self.change_for, self.name, self.log = first, then, change_for, name, log
        self.stays, self.first_seconds = stays, first_seconds
        self.changed = first is then
        self.handed_over = 0  # answers the other model gave instead of the first one

    @property
    def active(self) -> "Model":
        return self.then if self.changed else self.first

    provider = property(lambda self: self.active.provider)
    model = property(lambda self: self.active.model)
    thinking = property(lambda self: self.active.thinking)
    reasoning_effort = property(lambda self: self.active.reasoning_effort)

    async def chat(self, messages: list[dict], tools: list[dict] | None = None, **kwargs):
        if self.changed:
            return await self.then.chat(messages, tools, **kwargs)
        try:
            message = await asyncio.wait_for(self.first.chat(messages, tools, **kwargs), timeout=self.first_seconds)
            reason = self.change_for(message)
        except TimeoutError:
            reason = f"no answer from {self.first.provider} in {self.first_seconds:.0f} s"
        if not reason:
            return message
        self.handed_over += 1
        if self.stays:
            self.changed = True
            self.log(f"[{self.name}] {reason}: {self.then.provider}/{self.then.model} from here on")
        elif self.handed_over == 1 or reason.startswith("no answer"):
            self.log(f"[{self.name}] {reason}: {self.then.provider}/{self.then.model} answers such steps")
        return await self.then.chat(messages, tools, **kwargs)

    def usage(self) -> str:
        return self.active.usage()


def _key_refused(status, error) -> bool:
    """The provider does not take this key (any more): unknown, not allowed, or out of credit. Waiting does not help."""
    return status in (401, 403) or (status == 429 and "insufficient_quota" in str(error))


def _development_headers() -> dict:
    """Outside a run the proxy wants the developer's own platform token in a header; in a run the token is part of
    the base URL and this variable is not set."""
    token = os.environ.get("ATIRA_TEAM_KEY")
    return {"x-atira-team-key": token} if token else {}


def _responses_request(args: dict) -> dict:
    """A chat-completions request as a Responses request: the conversation becomes a list of items (messages, tool
    calls and tool results each on their own), tools and the forced tool lose one level of nesting, and the effort
    and the answer limit get their names of that interface. Nothing is stored on the provider's side."""
    items: list[dict] = []
    for message in args["messages"]:
        role, content = message["role"], message.get("content")
        if role == "tool":
            items.append({"type": "function_call_output", "call_id": message["tool_call_id"],
                          "output": content if isinstance(content, str) else str(content)})
            continue
        if content:
            items.append({"role": role, "content": content})
        for call in message.get("tool_calls") or []:
            items.append({"type": "function_call", "call_id": call["id"], "name": call["function"]["name"],
                          "arguments": call["function"]["arguments"]})
    request = {"model": args["model"], "input": items, "store": False}
    if args.get("tools"):
        # not strict: the tools' schemas have optional fields
        request["tools"] = [{"type": "function", "strict": False, **tool["function"]} for tool in args["tools"]]
    choice = args.get("tool_choice")
    if isinstance(choice, dict):
        request["tool_choice"] = {"type": "function", "name": choice["function"]["name"]}
    elif choice:
        request["tool_choice"] = choice
    if args.get("reasoning_effort"):
        request["reasoning"] = {"effort": args["reasoning_effort"]}
    if args.get("max_completion_tokens"):
        request["max_output_tokens"] = args["max_completion_tokens"]
    return request


class Model:
    def __init__(self, provider: str | None = None, model: str | None = None, reasoning_effort: str | None = None,
                 thinking: bool | None = None, max_tokens: int | None = None, fallback: "Model | None" = None,
                 call_seconds: float | None = None):
        # how much the model thinks before it answers; None leaves the provider's default
        self.reasoning_effort = reasoning_effort
        # False switches Qwen's thinking off entirely: a routine step (a click, copying facts) then answers in a few
        # seconds instead of writing thousands of reasoning tokens first (about 40 tokens per second)
        self.thinking = thinking
        self.max_tokens = max_tokens  # upper bound per answer, reasoning included; None: the provider's
        # The model that answers from then on when this one's provider turns the key down or its credit is used up,
        # so that a run goes on with the hosted model instead of ending without a bid.
        self.fallback = fallback
        self.fell_back = False
        self.provider = provider or os.environ.get("AGENT_PROVIDER") or DEFAULT_PROVIDER
        self.model = model or os.environ.get("AGENT_MODEL") or DEFAULT_MODELS[self.provider]
        base_url, api_key = _client_config(self.provider)
        # A request this model normally answers in seconds can hang for minutes; with `call_seconds` it is given
        # up after that long and sent again, instead of holding its caller for the whole general limit.
        self.client = AsyncOpenAI(base_url=base_url, api_key=api_key, timeout=call_seconds or CALL_TIMEOUT_SECONDS,
                                  max_retries=0, default_headers=_development_headers())
        # OpenAI's reasoning models take only their default temperature and name the answer limit differently; what
        # else a model refuses is learned from its first refusal (see `_adjust`)
        self.reasons = self.provider == "openai"
        self.refused: set[str] = {"temperature"} if self.reasons else set()
        # These models think and call tools together only through the Responses interface. Should that interface
        # refuse a request, the model is asked through chat completions without thinking instead (see `chat`).
        self.responses = self.reasons
        self.effort: str | None = None  # an effort every call is sent with, whatever the caller asked for
        self.calls = 0
        self.prompt_tokens = 0
        self.completion_tokens = 0

    async def chat(self, messages: list[dict], tools: list[dict] | None = None, **kwargs):
        """One completion with retries; returns the assistant message."""
        if self.fell_back:
            return await self.fallback.chat(messages, tools, **kwargs)
        delay = 2.0
        for attempt in range(8):
            try:
                started = time.time()
                args = self._arguments(messages, tools, kwargs)
                if self.responses:
                    message = await self._respond(args)
                else:
                    reply = await self.client.chat.completions.create(**args)
                    self.calls += 1
                    if reply.usage:
                        self.prompt_tokens += reply.usage.prompt_tokens or 0
                        self.completion_tokens += reply.usage.completion_tokens or 0
                    if not reply.choices:
                        raise APIError("empty reply", request=None, body=None)
                    message = reply.choices[0].message
                message.elapsed = time.time() - started
                return message
            except (APIError, APITimeoutError, RateLimitError) as error:
                status = getattr(error, "status_code", None)
                if status == 400 and self._adjust(error, args):
                    print(f"[llm] {self.provider} refused a parameter ({str(error)[:160]}); adjusting", flush=True)
                    continue
                if status in (400, 404) and self.responses:
                    print(f"[llm] {self.provider} refused the Responses request ({str(error)[:300]}); "
                          "asking through chat completions without thinking", flush=True)
                    self.responses, self.effort = False, "none"
                    continue
                if self.fallback and _key_refused(status, error):
                    print(f"[llm] {self.provider} turned the key down ({status}: {str(error)[:200]}); "
                          f"{self.fallback.provider}/{self.fallback.model} answers from now on", flush=True)
                    self.fell_back = True
                    return await self.fallback.chat(messages, tools, **kwargs)
                if status in (400, 401, 403, 404) and attempt > 0:
                    raise
                print(f"[llm] {self.provider} call failed ({status or type(error).__name__}: {str(error)[:200]}); retrying", flush=True)
                await asyncio.sleep(delay)
                delay = min(delay * 2, 30)
        raise RuntimeError("model unavailable after retries")

    def _arguments(self, messages: list[dict], tools: list[dict] | None, kwargs: dict) -> dict:
        """The request for this provider: the callers pass the same parameters whatever the model is."""
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
        if self.reasons and "max_tokens" in args:
            args["max_completion_tokens"] = args.pop("max_tokens") + REASONING_HEADROOM_TOKENS
            args["reasoning_effort"] = EFFORTS[-1]
        effort = self.effort or args.get("reasoning_effort")
        while effort in self.refused:
            lower = EFFORTS[EFFORTS.index(effort) + 1:] if effort in EFFORTS else []
            effort = lower[0] if lower else None
        if effort:
            args["reasoning_effort"] = effort
        else:
            args.pop("reasoning_effort", None)
        if "temperature" in self.refused:
            args.pop("temperature", None)
        return args

    def _adjust(self, error, sent: dict) -> bool:
        """A 400 that names a parameter this model does not take: that parameter, or the effort that was sent, is
        left out from now on. True when the next request will differ from the refused one."""
        text = f"{getattr(error, 'param', None) or ''} {error}".lower()
        if "reasoning_effort" in text and "to 'none'" in text and sent.get("reasoning_effort") != "none":
            self.effort = "none"  # the interface says which effort it takes with these tools
            return True
        if "reasoning_effort" in text or "reasoning.effort" in text:
            refused = sent.get("reasoning_effort")
        elif "temperature" in text and "temperature" in sent:
            refused = "temperature"
        else:
            refused = None
        if refused:
            self.refused.add(refused)
        return bool(refused)

    async def _respond(self, args: dict) -> SimpleNamespace:
        """One call through the Responses interface, for callers that speak chat completions: the request is
        translated on the way out, the reply on the way back (its text and its tool calls)."""
        reply = await self.client.responses.create(**_responses_request(args))
        self.calls += 1
        usage = getattr(reply, "usage", None)
        if usage:
            self.prompt_tokens += getattr(usage, "input_tokens", 0) or 0
            self.completion_tokens += getattr(usage, "output_tokens", 0) or 0
        texts, calls = [], []
        for item in getattr(reply, "output", None) or []:
            kind = getattr(item, "type", None)
            if kind == "message":
                texts += [part.text for part in item.content or [] if getattr(part, "type", None) == "output_text"]
            elif kind == "function_call":
                calls.append(SimpleNamespace(id=item.call_id, type="function", function=SimpleNamespace(
                    name=item.name, arguments=item.arguments or "{}")))
        if not texts and not calls:
            raise APIError("empty reply", request=None, body=None)
        return SimpleNamespace(role="assistant", content="".join(texts) or None, tool_calls=calls or None)

    def usage(self) -> str:
        own = f"{self.calls} calls, {self.prompt_tokens} prompt + {self.completion_tokens} completion tokens"
        return own + (f" (then {self.fallback.provider}/{self.fallback.model})" if self.fell_back else "")
