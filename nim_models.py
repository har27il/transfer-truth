"""Single home for which NIM model each pipeline runs — nothing else defines one.

Two constants on purpose (June 25 post-mortem): the extraction ENGINE and the
outcome RESOLVER are different tasks with different safety properties. The
engine is graded by the golden eval (tests/golden/) and a bad model there
silently starves the whole site; the resolver fails safe by design (an
unparseable answer resolves to "unclear" and the positive-evidence rule drops
it). Keeping both defaults in ONE file makes drift impossible to miss — on
June 25 the engine default was edited to a reasoning model while the resolver
kept the old one, and the split-brain ran undetected for a week.

Env overrides:
  NIM_MODEL           engine only — golden-eval.yml uses this to A/B candidates.
  NIM_RESOLVER_MODEL  resolver only.

Changing DEFAULT_NIM_MODEL requires a green golden-eval run and an entry in
EVALUATED_MODELS (tests/test_nim_models.py) — the cron's test gate refuses to
run an unevaluated engine default. That test is the guard; do not "fix" it by
adding a model you haven't evaluated.
"""
import os

# The known-good default: passed the golden eval (>=80% pass / >=90% field
# accuracy). Reasoning models (nemotron-super, deepseek-r1, ...) are NOT safe
# here even with <think>-stripping in the parser — evaluate first.
DEFAULT_NIM_MODEL = "meta/llama-3.3-70b-instruct"

ENGINE_MODEL = os.environ.get("NIM_MODEL", DEFAULT_NIM_MODEL)
RESOLVER_MODEL = os.environ.get("NIM_RESOLVER_MODEL", DEFAULT_NIM_MODEL)
