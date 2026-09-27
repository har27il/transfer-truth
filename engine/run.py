#!/usr/bin/env python3
"""
Make the ingestion engine executable: load the transfer-analyst system prompt and
run ONE raw post through an LLM, returning the strict JSON object it defines.

Provider defaults to NVIDIA NIM (OpenAI-compatible, free tier; set NVIDIA_API_KEY),
matching outcome/source.py. The completion call is injectable as `complete` so the
grader and tests run with no network and no key.

Usage:
    NVIDIA_API_KEY=... python engine/run.py "Isak to Liverpool, here we go! (Romano)"
"""
import json
import os
import re
import sys
from pathlib import Path

from langsmith import traceable

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
PROMPT_FILE = ROOT / "engine" / "transfer-analyst-system-prompt.md"
AGENT_NAME = os.environ.get("TM_AGENT_NAME", "Verity")

NIM_BASE = os.environ.get("NIM_BASE_URL", "https://integrate.api.nvidia.com/v1")
# Model default lives in nim_models.py (shared, allowlist-guarded) — see the
# June 25 post-mortem there before changing anything about model selection.
from nim_models import ENGINE_MODEL as NIM_MODEL

_DASHES = re.compile(r"^-{20,}\s*$", re.M)
_THINK = re.compile(r"<think>.*?(?:</think>|\Z)", re.S)


def load_system_prompt(path=PROMPT_FILE, agent_name=AGENT_NAME):
    """Extract the prompt body between the two long dashed delimiter lines, plus the
    few-shot examples, and substitute the {{AGENT_NAME}} placeholder."""
    text = path.read_text(encoding="utf-8")
    marks = list(_DASHES.finditer(text))
    if len(marks) >= 2:
        body = text[marks[0].end():marks[1].start()].strip()
    else:  # fall back to the whole file rather than silently shipping an empty prompt
        body = text
    fewshot = ""
    if "## FEW-SHOT EXAMPLES" in text:
        fewshot = "\n\n" + text.split("## FEW-SHOT EXAMPLES", 1)[1].split("## HOW THIS FEEDS", 1)[0].strip()
    return (body + fewshot).replace("{{AGENT_NAME}}", agent_name)


def parse_engine_json(raw):
    """Pull the JSON object out of a model answer. Returns None on failure (the
    engine contract is JSON-only; a non-JSON answer is a hard failure, not a guess).

    Reasoning models wrap the answer in <think>...</think> prose whose stray
    braces poison a naive first-{/last-} slice (the June 25 outage) — so think
    blocks are stripped first, and if the slice still fails, each remaining "{"
    is tried as the start of a balanced object."""
    raw = _THINK.sub("", raw or "")
    try:
        i, j = raw.index("{"), raw.rindex("}")
        return json.loads(raw[i:j + 1])
    except (ValueError, json.JSONDecodeError):
        pass
    decoder = json.JSONDecoder()
    i = raw.find("{")
    while i != -1:
        try:
            obj, _end = decoder.raw_decode(raw, i)
            if isinstance(obj, dict):
                return obj
        except json.JSONDecodeError:
            pass
        i = raw.find("{", i + 1)
    return None


@traceable(name="nim_extraction", run_type="llm")
def _nim_complete(system, user, model=None):
    from openai import OpenAI
    key = os.environ.get("NVIDIA_API_KEY")
    if not key:
        raise RuntimeError(
            "NVIDIA_API_KEY not set. Get a free key at https://build.nvidia.com, or "
            "pass complete=... in tests.")
    # max_retries: the SDK retries 429/5xx/connection errors with exponential backoff
    # (respecting Retry-After). This is what makes the ingest pipeline's CONCURRENCY
    # safe -- parallel calls that hit the free-tier rate limit back off and retry
    # instead of being dropped as extraction errors. timeout caps a hung call so one
    # stuck request can't pin a worker forever.
    client = OpenAI(base_url=NIM_BASE, api_key=key,
                    max_retries=int(os.environ.get("NIM_MAX_RETRIES", "5")),
                    timeout=float(os.environ.get("NIM_TIMEOUT", "30")))
    # max_tokens must leave room for a reasoning model's <think> preamble BEFORE
    # the JSON: at 600 the thinking ate the whole budget and the answer was
    # truncated into a parse failure (nemotron eval scored 33% purely on
    # truncation). Instruct models stop early anyway, so headroom costs nothing.
    resp = client.chat.completions.create(
        model=model or NIM_MODEL,
        messages=[{"role": "system", "content": system},
                  {"role": "user", "content": user}],
        temperature=0,
        max_tokens=int(os.environ.get("NIM_MAX_TOKENS", DEFAULT_MAX_TOKENS)),
        **reasoning_budget_kwargs(),
    )
    return finished_text(resp, "extraction")


# Hard ceiling on the reasoning trace (hosted-API field `reasoning_budget`, default 16384
# -- RESEARCH.md §2). Kept BELOW max_tokens so a runaway trace can't eat the budget the
# JSON answer needs (one ran to 4096 in the 2026-09-27 baseline). Empty = not sent.
DEFAULT_REASONING_BUDGET = ""


def reasoning_budget_kwargs():
    budget = os.environ.get("NIM_REASONING_BUDGET", DEFAULT_REASONING_BUDGET).strip()
    return {"extra_body": {"reasoning_budget": int(budget)}} if budget else {}


# 2026-09-27 measurement, 27 golden cases on nemotron-3-super: 0 truncated, median 676
# completion tokens, max 1710 -- 84% of the old 2048 cap, on headline-only inputs (real
# posts add a summary). The hosted endpoint's own reasoning budget defaults to 16384
# (RESEARCH.md), so the cap, not the model, decides when a long trace gets cut.
DEFAULT_MAX_TOKENS = "4096"


def finished_text(resp, where):
    """The answer text. A trace cut off at max_tokens (finish_reason == "length") used to
    look exactly like the model breaking the JSON contract -- say which it was, so the
    log can tell truncation apart from a bad answer."""
    choice = resp.choices[0]
    if getattr(choice, "finish_reason", None) == "length":
        used = getattr(getattr(resp, "usage", None), "completion_tokens", "?")
        print(f"  ! {where}: answer truncated at max_tokens ({used} completion tokens); "
              f"raise NIM_MAX_TOKENS", flush=True)
    return choice.message.content or ""


def analyze(post, complete=_nim_complete, system=None):
    """Return the parsed engine JSON for one post (or None if the model broke contract).

    One same-call retry on a contract break: the reasoning default emits
    unparseable output intermittently (~1 in 10 on the 27-case eval), and a
    second identical call usually parses. Persistent breaks still return None
    and flow into the pipeline's parse_err/retry machinery."""
    system = system if system is not None else load_system_prompt()
    result = parse_engine_json(complete(system, post))
    if result is None:
        result = parse_engine_json(complete(system, post))
    return result


if __name__ == "__main__":
    if len(sys.argv) < 2:
        raise SystemExit('Usage: python engine/run.py "<raw post text>"')
    print(json.dumps(analyze(sys.argv[1]), indent=2, ensure_ascii=False))
