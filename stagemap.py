#!/usr/bin/env python3
"""
Shared stage vocabulary — the SINGLE SOURCE OF TRUTH for claim stages.

A "stage" is how far a transfer claim commits: a vague link is weak, a "here we
go" is an all-in bet. STAGE_P maps each stage to the probability the journalist
is implicitly asserting the deal completes. The Brier scorer (scoring/score.py)
and the ML featurizer (ml/deal_predictor.py) both import this so the mapping can
never drift out of sync between them. The extraction prompt
(engine/transfer-analyst-system-prompt.md) mirrors these stage names by hand —
keep that comment in step if you edit this dict.

Import from anywhere in the repo with:
    import sys; from pathlib import Path
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from stagemap import STAGE_P
"""

# stage -> implied P(deal completes) the journalist is asserting at this stage
STAGE_P = {
    "rumour_link": 0.15,   # "linked with" — barely more than noise
    "interest": 0.15,      # "keen on" / "monitoring"
    "talks": 0.35,         # "in talks" / "contact made"
    "advanced": 0.60,      # "talks advanced" / "closing in"
    "agreement": 0.80,     # "agreement reached" / "personal terms agreed"
    "medical": 0.92,       # "medical booked / underway"
    "here_we_go": 0.99,    # Romano's all-in signal
    "official": 0.99,      # club has announced
    "denied": 0.02,        # source reports the deal is OFF / player staying
}
