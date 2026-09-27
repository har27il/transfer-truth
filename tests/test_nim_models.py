"""Model-selection guard + parser-contract tests (June 25 post-mortem).

The incident: the engine's default model was hand-edited to a reasoning model
and pushed straight to main; its <think> output broke the JSON parser, parse
failures were silently counted as "non-transfer", and the site ran a week on
stale data. These tests make that failure class structurally loud:

  1. The engine default must be on the EVALUATED_MODELS allowlist (a green
     golden-eval run is the entry ticket). The cron runs pytest before touching
     data, so an unevaluated default stops the pipeline the same day.
  2. Engine and resolver must both read the ONE shared module (no silent drift).
  3. parse_engine_json must survive reasoning-model transcripts.
"""
import importlib
import json

import nim_models
from engine.run import parse_engine_json


# Models that have PASSED the live golden eval (>=80% pass / >=90% field
# accuracy — dispatch .github/workflows/golden-eval.yml for the evidence).
# Add a model here ONLY with a green eval run to point at.
EVALUATED_MODELS = {
    "meta/llama-3.3-70b-instruct",          # evaluated 2026-06-22: 80% pass / 97% field
                                            # (endpoint degraded to timeouts 2026-07-05)
    "nvidia/nemotron-3-super-120b-a12b",    # evaluated 2026-07-05 run 28741893878:
                                            # 87% pass / 98% field @ NIM_MAX_TOKENS=2048
}


def test_default_engine_model_is_evaluated():
    assert nim_models.DEFAULT_NIM_MODEL in EVALUATED_MODELS, (
        f"Engine default '{nim_models.DEFAULT_NIM_MODEL}' has no golden-eval evidence. "
        f"Dispatch golden-eval.yml (Actions tab) with this model; add it to "
        f"EVALUATED_MODELS only when the run is green. This gate exists because an "
        f"unevaluated swap silently broke extraction for a week (June 25).")


def test_engine_and_resolver_read_the_shared_module():
    """Drift guard: on June 25 the two files each declared their own default and
    desynced. Both must resolve their model from nim_models."""
    import engine.run as engine_run
    import outcome.source as outcome_source
    assert engine_run.NIM_MODEL == nim_models.ENGINE_MODEL
    assert outcome_source.NIM_MODEL == nim_models.RESOLVER_MODEL


def test_env_overrides_are_scoped_per_pipeline(monkeypatch):
    """NIM_MODEL (used by golden-eval A/B) must flip ONLY the engine; the
    resolver has its own knob. One override must never flip both pipelines."""
    try:
        monkeypatch.setenv("NIM_MODEL", "candidate/engine-x")
        monkeypatch.delenv("NIM_RESOLVER_MODEL", raising=False)
        m = importlib.reload(nim_models)
        assert m.ENGINE_MODEL == "candidate/engine-x"
        assert m.RESOLVER_MODEL == m.DEFAULT_NIM_MODEL
    finally:
        monkeypatch.undo()
        importlib.reload(nim_models)


# ---- parser contract: reasoning-model output must not poison the JSON ------

GOOD = {"is_transfer_claim": True, "player": "Sandro Tonali", "stage": "here_we_go"}


def test_parse_plain_json_still_works():
    assert parse_engine_json(json.dumps(GOOD)) == GOOD


def test_parse_survives_think_block_with_braces():
    """REGRESSION (June 25): a reasoning transcript whose <think> block contains
    braces broke the first-{/last-} slice and the whole answer parsed to None."""
    raw = ("<think>The mapping {stage: here_we_go} implies p=0.99. Let me check "
           "the schema {a: b} again...</think>\n" + json.dumps(GOOD))
    assert parse_engine_json(raw) == GOOD


def test_parse_survives_unclosed_think_block():
    raw = "<think>reasoning that never closes " + json.dumps(GOOD)
    # An unclosed think block swallows to end-of-string — a hard failure, never
    # a half-parsed guess.
    assert parse_engine_json(raw) is None


def test_parse_survives_brace_in_prose_preamble():
    raw = "Here is the {requested} output:\n" + json.dumps(GOOD)
    assert parse_engine_json(raw) == GOOD


def test_parse_returns_none_when_no_json():
    assert parse_engine_json("I cannot analyze this post.") is None
    assert parse_engine_json("") is None
    assert parse_engine_json(None) is None


def test_analyze_retries_once_on_contract_break():
    """Intermittent reasoning-model JSON breaks (~1/10 calls) must not cost a
    claim: analyze retries the identical call once, and only once."""
    from engine.run import analyze
    calls = []

    def flaky_then_good(system, user):
        calls.append(1)
        return "no json, sorry" if len(calls) == 1 else '{"is_transfer_claim": false}'

    assert analyze("post", complete=flaky_then_good, system="s") == {"is_transfer_claim": False}
    assert len(calls) == 2

    def always_broken(system, user):
        calls.append(1)
        return "still no json"

    calls.clear()
    assert analyze("post", complete=always_broken, system="s") is None
    assert len(calls) == 2                      # exactly one retry, no loop


# --- truncation is its own failure, not a "contract break" (RESEARCH.md, 2026-09-27) ---

def _resp(text, finish, tokens=2048):
    from types import SimpleNamespace as NS
    return NS(choices=[NS(finish_reason=finish, message=NS(content=text))],
              usage=NS(completion_tokens=tokens))


def test_a_truncated_answer_is_logged_as_truncation(capsys):
    from engine.run import finished_text
    assert finished_text(_resp("<think>long...", "length"), "extraction") == "<think>long..."
    out = capsys.readouterr().out
    assert "truncated at max_tokens" in out and "2048" in out


def test_a_normal_answer_is_silent(capsys):
    from engine.run import finished_text
    assert finished_text(_resp('{"ok": true}', "stop", 600), "extraction") == '{"ok": true}'
    assert capsys.readouterr().out == ""


def test_default_cap_leaves_headroom_over_the_measured_longest_trace():
    """Measured 2026-09-27: the longest golden-case completion was 1710 tokens (84% of
    the old 2048 cap). The default must keep real headroom above that."""
    from engine.run import DEFAULT_MAX_TOKENS
    assert int(DEFAULT_MAX_TOKENS) >= 2 * 1710


def test_reasoning_budget_is_sent_only_when_configured(monkeypatch):
    from engine import run
    monkeypatch.setattr(run, "DEFAULT_REASONING_BUDGET", "")
    monkeypatch.delenv("NIM_REASONING_BUDGET", raising=False)
    assert run.reasoning_budget_kwargs() == {}
    monkeypatch.setenv("NIM_REASONING_BUDGET", "3072")
    assert run.reasoning_budget_kwargs() == {"extra_body": {"reasoning_budget": 3072}}


def test_a_configured_reasoning_budget_stays_below_the_output_cap():
    """The budget must leave room for the JSON answer inside max_tokens."""
    from engine import run
    if run.DEFAULT_REASONING_BUDGET:
        assert int(run.DEFAULT_REASONING_BUDGET) + 512 <= int(run.DEFAULT_MAX_TOKENS)
