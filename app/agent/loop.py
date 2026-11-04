"""The agent loop, in the terminal: the LLM picks tools, our code runs them.

Run with: python -m app.agent.loop
"""

import json
from datetime import date

from app import config
from app.llm.openai_compat import OpenAICompatLLM
from app.tools.registry import run_tool, tool_schemas

MAX_ROUNDS = 8  # tool rounds per message, so the loop can never run forever

SYSTEM_PROMPT = """You are a document assistant for an intern's two work records:
a company work log (Google Sheet) and a university logbook (.docx).

Today is {today}. Dates are written dd/mm (day first). If the year is not given,
use the most recent date that is not in the future. Pass dates to tools as dd/mm/yyyy.

Rules:
- Answer only from tool results. If the records do not say, say so; never guess.
- Write tools only propose changes; nothing is written until the user approves.
  Never say that something was saved or written.
- If a request needs something your tools cannot do, say so and why.
- Keep answers short and plain.
"""


def short(text, limit=200):
    return text if len(text) <= limit else text[:limit] + "..."


def ask(llm, messages, tools):
    """Run one user message through the loop; returns the final answer."""
    for _ in range(MAX_ROUNDS):
        reply = llm.chat_tools(messages, tools)
        messages.append(reply["message"])
        if not reply["tool_calls"]:
            return reply["content"] or "(no answer)"
        for call in reply["tool_calls"]:
            print(f"  [tool] {call['name']}({json.dumps(call['arguments'], ensure_ascii=False)})")
            result = run_tool(call["name"], call["arguments"])
            print(f"  [result] {short(result)}")
            messages.append({"role": "tool", "tool_call_id": call["id"], "content": result})
    return f"(stopped after {MAX_ROUNDS} tool rounds without an answer)"


def main():
    llm = OpenAICompatLLM.from_config()
    tools = tool_schemas()
    messages = [{"role": "system",
                 "content": SYSTEM_PROMPT.format(today=f"{date.today():%A %d/%m/%Y}")}]
    print(f"Agent ready ({config.LLM_PROVIDER} / {config.LLM_MODEL}). Type 'exit' to quit.")
    while True:
        text = input("\nyou> ").strip()
        if text.lower() in ("exit", "quit"):
            break
        if not text:
            continue
        messages.append({"role": "user", "content": text})
        try:
            print(f"\nagent> {ask(llm, messages, tools)}")
        except Exception as error:  # report plainly, keep the chat going
            print(f"\nagent> Error: {type(error).__name__}: {error}")


if __name__ == "__main__":
    main()
