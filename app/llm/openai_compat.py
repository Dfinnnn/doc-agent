"""LLM adapter for any provider that accepts OpenAI-style requests.

Which provider is used depends only on .env (LLM_MODEL, LLM_API_KEY, LLM_BASE_URL).
"""

import json

from openai import OpenAI

from app import config
from app.llm.base import LLM


TIMEOUT_SECONDS = 120  # per try; a sync batch or summary can take a while
MAX_RETRIES = 2        # the library retries connection errors and busy servers


class OpenAICompatLLM(LLM):
    def __init__(self, model, api_key, base_url):
        self.model = model
        # Without a timeout the library waits up to 10 minutes per try
        self.client = OpenAI(api_key=api_key, base_url=base_url,
                             timeout=TIMEOUT_SECONDS, max_retries=MAX_RETRIES)

    @classmethod
    def from_config(cls):
        """Build the adapter from the LLM_ settings in .env."""
        return cls(config.LLM_MODEL, config.LLM_API_KEY, config.LLM_BASE_URL)

    def chat_json(self, system, user):
        response = self.client.chat.completions.create(
            model=self.model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            response_format={"type": "json_object"},  # ask for JSON only, no extra text
        )
        text = response.choices[0].message.content
        try:
            return json.loads(text)
        except json.JSONDecodeError:
            raise ValueError(f"The model did not return valid JSON: {text[:200]!r}")

    def chat_tools(self, messages, tools):
        # tools=None: the model must answer in text (used when the tool limit is reached)
        extra = {"tools": tools} if tools else {}
        response = self.client.chat.completions.create(
            model=self.model, messages=messages, **extra,
        )
        reply = response.choices[0].message
        tool_calls = []
        for call in reply.tool_calls or []:
            try:
                arguments = json.loads(call.function.arguments or "{}")
            except json.JSONDecodeError:
                arguments = {"_invalid": call.function.arguments}  # the loop reports it
            tool_calls.append({"id": call.id, "name": call.function.name, "arguments": arguments})
        usage = response.usage
        return {
            "content": reply.content,
            "tool_calls": tool_calls,
            "tokens": {"input": usage.prompt_tokens, "output": usage.completion_tokens}
                      if usage else None,
            # Keep every field the provider sent (some need theirs echoed back)
            "message": reply.model_dump(exclude_none=True),
        }


if __name__ == "__main__":
    # Quick connection check: python -m app.llm.openai_compat
    llm = OpenAICompatLLM.from_config()
    reply = llm.chat_json(
        system='Reply only with JSON like {"ok": true, "model": "<your model name>"}.',
        user="Connection check.",
    )
    print(f"{config.LLM_PROVIDER} / {config.LLM_MODEL} replied: {reply}")
