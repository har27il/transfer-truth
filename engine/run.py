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
        max_tokens=int(os.environ.get("NIM_MAX_TOKENS", "2048")),
    )
    return resp.choices[0].message.content or ""


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
