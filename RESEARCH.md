# Transfer Truth: research notes on extraction, evaluation and scoring

*Compiled 2026-09-27 on branch `fix/retrospective-rumours`. Every external claim cites the primary source that owns it: the spec, the paper, the vendor API reference or the source docs. Repo numbers come from `ground-truth/deals.csv`, `ground-truth/journalist_claims.csv` and `scoring/leaderboard.json` as committed at that point, using throwaway stdlib scripts. Nothing in the repo was changed.*

## Headline

1. **The scored ledger has a verification bias, and the bias drives the leaderboard.** A claim's chance of reaching a scored (`verified=YES`, resolved) deal depends on its stage: 14–17% for `denied`, `interest`, `rumour_link` and `talks`, against 34–50% for `agreement`, `here_we_go`, `official` and `medical`. Among the claims that did get scored, 10/10 `rumour_link` claims and 26/28 `interest` claims point at completed deals, even though the site implies p=0.15 for both. On the Murphy decomposition, the population Brier score is 0.175, made up of reliability 0.137, resolution 0.002 and uncertainty 0.040. That makes it worse than always forecasting the base rate (Brier skill score −3.3). Verification also depends on the *outcome*: humans have promoted 125 of 254 completed deals (49%) but only 13 of 73 collapsed deals (18%). Treat these numbers as a symptom of the bias, not as a target to calibrate against. The fix is to verify collapses; reweighting by stage cannot correct it (§4, §5).
2. **The extractor never sees the publication date.** `ingest/pipeline.py:122-124` sends only title and summary. TimeML/TempEval anchors every event to a Document Creation Time (DCT). This is the underlying cause of the Mbappé bug and of TODOS 0d (§1).
3. **Reasoning-token budget vs `max_tokens`.** The hosted Nemotron endpoint defaults `reasoning_budget` to 16384, but the repo caps `max_tokens` at 2048 and never checks `finish_reason`. That combination is the likely cause of the "~1 in 10 contract break", though it is not confirmed. The repo's own comment shows that reasoning tokens count against `max_tokens`, and a `finish_reason` check would confirm the rest (§2).
4. **The 80% golden floor on n=27 cannot tell models apart.** A model whose true pass rate is 75% clears it 30% of the time, and a model at a true 85% fails it 21% of the time (§3).
5. **Reliability weighting in the meter is close to a no-op.** Leaderboard scores run from 78.8 to 87.3, so `ingest/meter.py:140` weights differ by at most about 11%. The 14-day recency decay dominates (§5).

---

## 1. Temporal grounding: event time vs publication time

**What the primary sources say**

- TimeML treats the document's own date as a first-class temporal anchor. `TIMEX3` has a `functionInDocument` attribute with values `CREATION_TIME` and `PUBLICATION_TIME`, among others. Relative expressions ("two weeks ago", "last week") are marked `temporalFunction="true"` and resolved through `anchorTimeID` against that anchor ([TimeML 1.2.1 spec](https://timeml.github.io/site/publications/timeMLdocs/timeml_1.2.1.html)). The spec also records event `tense`/`aspect` (PAST, PERFECTIVE, …) as explicit attributes, separate from the date.
- TempEval-1 made **event↔DCT** relations a task in their own right. Task B "addresses only the temporal relations holding between the Document Creation Time (DCT) and event expressions". It was the *easiest* of the three: strict F 0.66–0.80, against 0.34–0.62 for event↔timex (Task A) and 0.42–0.55 for adjacent-sentence events (Task C) ([Verhagen et al., SemEval-2007 Task 15](https://aclanthology.org/S07-1014.pdf), Tables 2–4).
- In TempEval-3, "event and document creation time" is one of the four linked pair types, and the organisers added the event–DCT relations that were missing from AQUAINT. End-to-end temporal-relation extraction from raw text stayed hard: the best participant scored 30.98 F1 and the TIPSem reference 42.39 (Table 7). The organisers also note that timex *normalisation* "is currently (and perhaps inherently) done best by rule-engineered systems" ([UzZaman et al., SemEval-2013 Task 1](https://aclanthology.org/S13-2001.pdf)).
- FactBank annotates event *factuality* (certain / probable / possible, with polarity), which is a separate axis from time. "Was close to joining" is a counterfactual past event, not a possible future one ([Saurí & Pustejovsky 2009, LRE 43](https://link.springer.com/article/10.1007/s10579-009-9089-9)).
- LLM evaluations report the same weaknesses:
  - ChatGPT's zero-shot temporal relation extraction "has a large gap" to supervised methods, depends heavily on the prompt, and "cannot keep consistency during temporal inference" ([Yuan, Xie & Ananiadou, BioNLP 2023](https://aclanthology.org/2023.bionlp-1.7/)).
  - ChatGPT is weaker on temporal order than on causal relations ([Chan et al., Findings EACL 2024](https://arxiv.org/abs/2304.14827)).
  - On TRAM, "the best-performing model lags significantly behind human performance" ([Wang & Zhao, Findings ACL 2024](https://arxiv.org/abs/2310.00835)).
  - The answer to a question often depends on *when* it is asked, and models trained on older data degrade on present-day questions ([SituatedQA, Zhang & Choi, EMNLP 2021](https://arxiv.org/abs/2109.06157)).

**Recommended practice.** Give the model the DCT explicitly. Have it output the event's relation to the DCT (before / overlapping / after), or a normalised time for the event, separately from the claim content. Use deterministic rules to normalise explicit dates and as a backstop. Treat factuality/modality ("was close", "could") as its own field.

**Implication for this repo**

- The Mbappé bug is a textbook Task-B error. The model classified an event without being told the DCT. The claim then inherited the RSS publish date as its event date (`ingest/pipeline.py:69`) and a hard-coded window (`DEFAULT_WINDOW`, `ingest/pipeline.py:24`; TODOS 0d).
- `ingest/retro.py` is a rule-based backstop, which is consistent with TempEval-3's finding on normalisation. The `historical` flag in the prompt, however, is a binary stand-in for a missing event↔DCT relation.
- Change 1: prefix the user message in `_extract` (`ingest/pipeline.py:122-124`) with `Published: YYYY-MM-DD`.
- Change 2: add an output field to `engine/transfer-analyst-system-prompt.md`, e.g. `event_time_relation: "current"|"past"|"future"` plus `event_time_text` (the raw timex).
- Change 3: derive the deal window from `claim_date` (TODOS 0d), and from the event time once it exists.
- **Hidden dependency:** `tests/golden/cases.jsonl` has no date field. Each case needs a DCT before the grader can check the new field. Otherwise the golden gate would pass while testing nothing new.
- Effort **M**. Risk low–medium: it changes the prompt, so it goes through `golden-eval.yml`.

## 2. Structured output with NVIDIA NIM

**What the primary sources say**

- **Self-hosted NIM for LLMs** documents guided decoding through the `nvext` extension, sent from the OpenAI client as `extra_body={"nvext": {...}}`. Parameter names: `guided_json`, `guided_regex`, `guided_choice`, `guided_grammar`, `guided_decoding_backend`. Backends are xgrammar (default, "the fastest option"), outlines and lm-format-enforcer. NVIDIA recommends `guided_json` over `response_format={"type":"json_object"}`, because the latter "permits the model to produce any valid JSON, including empty objects" ([NIM LLM 1.12.0, Structured Generation](https://docs.nvidia.com/nim/large-language-models/1.12.0/structured-generation.html)). The `latest` version of that page rendered empty when fetched, so the versioned page is cited.
- **The hosted endpoint** (`integrate.api.nvidia.com`) for `nvidia/nemotron-3-super-120b-a12b` documents none of `response_format`, `json_schema`, `guided_json` or `nvext`. It does document `tools`/`tool_choice` and these reasoning controls ([API reference](https://docs.api.nvidia.com/nim/reference/nvidia-nemotron-3-super-120b-a12b-infer)):
  - `reasoning_effort`: `none` | `low` | `high`, default `high`.
  - `reasoning_budget`: −1 to 32768, **default 16384**.
  - `chat_template_kwargs`: `enable_thinking`, `low_effort`.
  - `seed`: default 42, "best effort" determinism.
  - A `reasoning_content` response field.
  - `temperature` is typed with `exclusiveMinimum: 0` and default 1.
- The **model card** recommends "`temperature=1.0` and `top_p=0.95`" across all tasks, and names `reasoning_budget` as the hard ceiling on the reasoning trace ([HF model card](https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-BF16); [build.nvidia.com card](https://build.nvidia.com/nvidia/nemotron-3-super-120b-a12b/modelcard)).
- **vLLM**, the engine behind NIM's vLLM profiles, removed the `guided_json`/`guided_*` request fields in v0.12.0 in favour of `structured_outputs`. It exposes `--structured-outputs-config.enable_in_reasoning`, which implies that by default the constraint applies to the answer, not to the reasoning trace ([vLLM structured outputs](https://docs.vllm.ai/en/latest/features/structured_outputs.html)). The reasoning docs confirm reasoning and `content` are returned separately ([vLLM reasoning outputs](https://docs.vllm.ai/en/latest/features/reasoning_outputs.html)).
- **OpenAI**, the API that NIM mirrors: `response_format: {type: "json_schema", strict: true}` "ensures the model will always generate responses that adhere to your supplied JSON Schema". JSON mode guarantees only syntax. Strict mode requires every field to be `required`, `additionalProperties: false`, and nullables expressed as a union with `null`. Refusals come back in a separate `refusal` field ([OpenAI Structured Outputs](https://developers.openai.com/api/docs/guides/structured-outputs)).

**Implication for this repo**

- **Documented controls, likely cause (hypothesis):** `engine/run.py:95-104` sends no reasoning controls and `max_tokens=2048`, while the endpoint default is a 16384-token reasoning budget.
  - The repo's own comment (`engine/run.py:93-96`) records that at `max_tokens=600` "the thinking ate the whole budget", so reasoning tokens count against `max_tokens`.
  - A `<think>` longer than about 2048 tokens would therefore truncate into "unparseable output". That would explain the ~1-in-10 break the retry in `analyze()` papers over. It is a hypothesis until `finish_reason` is logged.
  - Set `reasoning_budget` well under `max_tokens`, or `reasoning_effort="low"`, or `enable_thinking=False`.
  - Check `resp.choices[0].finish_reason == "length"` and raise a distinct error. That confirms or refutes the hypothesis, and truncation stops being counted as a contract break.
  - Apply the same to `outcome/source.py:~282`.
  - Effort **S**. Must pass the golden gate (`nim_models.py` post-mortem).
- **Undocumented, needs a test:** whether the hosted endpoint accepts `extra_body={"nvext":{"guided_json": schema}}` or `response_format` json_schema. The docs neither promise nor rule it out. Test it with a `golden-eval.yml` dispatch, not by assumption. If it works, keep `parse_engine_json` as the fallback.
  - The output contract (`engine/transfer-analyst-system-prompt.md`, OUTPUT CONTRACT) already fits the OpenAI strict subset: every field present, with nulls.
- **Temperature:** the repo sends `temperature=0`, the documented schema excludes 0, and the calls succeed. How the server handles 0 (rejects, clamps, or switches to greedy) is **undocumented**, so treat it as unknown. Do not change it silently. If it changes, evaluate it with repeats (§3).

## 3. Evaluating a 27-case golden set

**What the primary sources say**

- **Temperature 0 is not deterministic.**
  - Five LLMs "configured to be deterministic" showed "accuracy variations up to 15% across naturally occurring runs", and "none of the LLMs consistently delivers repeatable accuracy" ([Atil et al., "Non-Determinism of 'Deterministic' LLM Settings", arXiv 2408.04667v5](https://arxiv.org/abs/2408.04667)).
  - The main cause is kernels that are not batch-invariant: server load changes the batch size. 1,000 temperature-0 completions produced 80 unique outputs, and batch-invariant kernels made all 1,000 identical ([He / Thinking Machines, 2025-09-10](https://thinkingmachines.ai/blog/defeating-nondeterminism-in-llm-inference/)).
  - The hosted API describes `seed` only as "best effort".
- **Error bars** ([Miller, "Adding Error Bars to Evals", arXiv 2411.00640](https://arxiv.org/abs/2411.00640)):
  - Recommendations: CLT standard errors; clustered SEs; "resampling answers" (K samples per question, `Var(s_i)=σ_i²/K`, up to a 2/3 cut in estimator variance); **paired per-question differences** when comparing two models; power analysis.
  - §3.3 "Don't touch the thermostat!" advises against lowering temperature to reduce variance, because it can shift variance into the question-level means "or else reduce conditional variance by injecting bias".
- **Small-n intervals.** The Wald interval behaves erratically. The authors recommend "the Wilson interval or the equal-tailed Jeffreys prior interval for small *n*" ([Brown, Cai & DasGupta, Statistical Science 2001](https://projecteuclid.org/journals/statistical-science/volume-16/issue-2/Interval-Estimation-for-a-Binomial-Proportion/10.1214/ss/1009213286.full)). The abstract gives no numerical cutoff for "small".

**Numbers for this repo.** These are binomial calculations that treat the 27 cases as a sample of posts, i.e. "how good is the model on posts like these".

| passes / 27 | rate | Wilson 95% CI |
|---|---|---|
| 22 | 81.5% | 63.3–91.8% |
| 23 | 85.2% | 67.5–94.1% |
| 24 | 88.9% | 71.9–96.1% |

The floor needs ≥22/27. The probability of clearing it for a given true pass rate:

| true pass rate | 0.70 | 0.75 | 0.80 | 0.85 | 0.90 |
|---|---|---|---|---|---|
| P(clear floor) | 0.14 | 0.30 | 0.54 | 0.79 | 0.95 |

Run-to-run noise ("would this run repeat?") is a *separate* source of uncertainty that comes on top of this. The repo already records it: the `analyze()` docstring says ~1-in-10 unparseable output on the 27-case eval (`engine/run.py:104-111`).

**Implication for this repo**

- One run against a fixed 80% floor (`tests/test_golden.py:100-108`) is a noisy smoke test, not a statistically meaningful quality bar.
- The "87% pass" that justified the Nemotron default (`nim_models.py`) is not any k/27. It matches 13/15, so it was probably measured on the older 15-case set. 13/15 has a Wilson 95% CI of roughly 62–96%.
- Run each case K times (K=3–5 is affordable at 27 cases) and report per-case pass fractions, the mean, and a Wilson CI.
- For model A/B runs through `golden-eval.yml`, compare **paired per-case differences** (McNemar-style) rather than two pass rates side by side.
- `tests/golden/regressions.jsonl` already exists (2 cases). Run each one K times and require K/K, since these bugs already shipped once.
- Housekeeping: the comments in `.github/workflows/golden-eval.yml` are stale. They say "15-case golden set" and "default meta/llama-3.3-70b".
- Effort **S–M**. Cost: K× free-tier NIM calls.

## 4. Probability calibration and proper scoring

**What the primary sources say**

- The Brier score is the mean squared error of probability forecasts ([Brier 1950, MWR 78(1)](https://journals.ametsoc.org/view/journals/mwre/78/1/1520-0493_1950_078_0001_vofeit_2_0_co_2.xml)). Murphy splits it into **uncertainty** (the score of the sample base rate), **reliability** and **resolution**: BS = REL − RES + UNC ([Murphy 1973, J. Appl. Meteor. 12(4)](https://journals.ametsoc.org/view/journals/apme/12/4/1520-0450_1973_012_0595_anvpot_2_0_co_2.xml)). scikit-learn's docs make the practical point that a lower Brier score "does not necessarily mean a better calibrated model", so compute the terms ([sklearn calibration guide](https://scikit-learn.org/stable/modules/calibration.html)).
- A scoring rule is *proper* if the forecaster maximises expected score by reporting their true belief. Proper rules "encourage the forecaster to make careful assessments and to be honest" ([Gneiting & Raftery 2007, JASA 102(477)](https://sites.stat.washington.edu/raftery/Research/PDF/Gneiting2007jasa.pdf)).
- Reliability diagrams built by binning are unstable. CORP (isotonic/PAV-based) diagrams fix this ([Dimitriadis, Gneiting & Jordan, PNAS 2021](https://www.pnas.org/doi/10.1073/pnas.2016191118)).
- Recalibration methods and sample size:
  - "When the calibration set is small (less than about 200-1000 cases), Platt Scaling outperforms Isotonic Regression" ([Niculescu-Mizil & Caruana, ICML 2005](https://www.cs.cornell.edu/~alexn/papers/calibration.icml05.crc.rev3.pdf)).
  - sklearn: isotonic "is more prone to overfitting, especially on small datasets", and does as well or better than sigmoid only with "greater than ~ 1000 samples".

**Numbers for this repo.** 285 scored claims, grouped by the stage-implied p, which gives natural bins.

| implied p | stages | n | observed completion rate |
|---|---|---|---|
| 0.02 | denied | 8 | 0.75 |
| 0.15 | rumour_link, interest | 38 | 0.95 |
| 0.35 | talks | 14 | 0.93 |
| 0.60 | advanced | 34 | 0.97 |
| 0.80 | agreement | 61 | 0.93 |
| 0.92 | medical | 9 | 0.89 |
| 0.99 | here_we_go, official | 121 | 0.99 |

- Murphy decomposition: BS 0.175 = REL 0.137 − RES 0.002 + UNC 0.040. Brier skill score against climatology is −3.3.
- Base rates: **95.8% at claim level** and **90.6% at deal level** (125 completed / 138 verified-resolved deals).
- Saying "agreement" about every deal would score better than the population, and so would a constant 0.906.

**Implication for this repo**

- **Do not recalibrate `stagemap.py` from this ledger.** Per-stage frequencies or isotonic fitted here would push `rumour_link` toward 1.0 and `denied` toward 0.75. That would bake the verification bias of §5 into the core table. The table above is a symptom.
- Once collapses are verified at a representative rate (§5, rec. 1), use a beta-smoothed per-stage estimate centred on the hand-set value: `p̂_s = (completions_s + m·p0_s)/(n_s + m)`. It behaves well at n=8–60 per bin. Skip isotonic (n≪1000). If the smoothed rates violate stage order, apply PAV on top.
- **Propriety is a property of the mapping, not of the journalist.** Journalists never state a probability. The site imposes one via `STAGE_P`, so a journalist's Brier score measures how well *the site's* mapping fits *their* stage words.
- **The earliness bonus is not proper (our analysis, not a quoted result).** `scoring/score.py:71-92` pays only when p≥0.5 *and* the deal completed, grows with p, and is added to the score at line 120. Stating a higher stage can therefore only earn bonus, never lose it. An add-on like that pulls the combined score's optimum away from honest reporting. Either drop it from the ranked score (show it separately), or replace it with something symmetric, e.g. Brier averaged over time.
- Publish REL/RES/UNC and a Brier skill score next to the leaderboard (`scoring/leaderboard.json`, `site/build_leaderboard.py`). This touches scoring math, so plan mode first.

## 5. Source reliability with small samples

**What the primary sources say**

- **Credibility formula.** The "credibility formula" `(1−b)·E[μ] + b·X̄` with `b = n/(n+k)` is the best linear approximation to the Bayesian posterior mean, and it needs no distributional assumptions. The constant is not free: **k = E[σ²(θ)] / Var[μ(θ)]**, the expected within-risk variance over the variance of the risk means ([Bühlmann 1967, ASTIN Bulletin 4(3), pp. 199–207](https://www.casact.org/sites/default/files/database/astin_vol4no3_199.pdf)).
- **Empirical-Bayes shrinkage** of many small-sample rates toward a pooled mean has less than half the MSE of raw means in Efron & Morris's examples, including baseball batting averages ([Efron & Morris 1975, JASA 70](https://www.tandfonline.com/doi/abs/10.1080/01621459.1975.10479864)).
- **Verification bias.** Accuracy estimated only from verified cases "can be seriously biased" when verification depends on the index result. The fix is inverse-probability weighting by verification rate within strata ([Begg & Greenes 1983, Biometrics 39(1)](https://pubmed.ncbi.nlm.nih.gov/6871349/)).
- **Rumour resolution timing.** Rumours "ultimately proven true tend to be resolved faster": median about 2 hours for true against over 14 hours for false ([Zubiaga et al. 2016, PLoS ONE (PHEME)](https://journals.plos.org/plosone/article?id=10.1371%2Fjournal.pone.0150989)). The transfer analogue: completions get announced, collapses fade out.
- **Source reliability without ground truth** can be estimated jointly with the latent truth by EM ([Dawid & Skene 1979, JRSS-C 28(1)](https://academic.oup.com/jrsssc/article/28/1/20/6953573)).

**Numbers for this repo**

- `scoring/score.py:28,118` is exactly Bühlmann's form with a hand-set K=20.
- A nonparametric method-of-moments estimate on the 285 scored claims gives EPV ≈ 0.074, VHM ≈ 0.0075, so **k ≈ 10**. This is illustrative only: 21 sources, 15 of them with n=1, all on the biased ledger.
- Verification propensity, P(claim reaches a scored deal | stage), from all 944 claims:

| stage | propensity |
|---|---|
| denied | 0.14 |
| interest | 0.17 |
| rumour_link | 0.17 |
| talks | 0.17 |
| agreement | 0.35 |
| here_we_go | 0.34 |
| advanced | 0.42 |
| official | 0.44 |
| medical | 0.50 |

- **Verification also depends on the outcome, not only on the stage.** Human promotion rate: completed deals 125 YES of 254 (49%); collapsed deals 13 YES of 73 (18%). 60 `collapsed, verified=auto` rows are waiting for review.
- **Attribution:** 251/285 scored claims belong to three RSS outlet names, and 100 claims (17 scored) are BBC *gossip-column* relays of other papers' stories, attributed to "BBC Sport".
- **Meter weights:** the shrunk scores lie between 78.8 and 87.3.

**Implication for this repo**

1. **Fix the data before the maths.** Reviewing the 60 auto-collapsed rows (`outcome/promote.py`) is the single highest-value action, and it is a data task, not code.
2. **Report propensity per stage as a diagnostic, not a correction weight** (a new read-only script, or a section in `scoring/score.py` output). It shows where the ledger is thin.
   - Begg & Greenes-style weighting by 1/P(scored | stage) is valid only if verification is independent of the outcome *given* the stage.
   - The 49% vs 18% promotion rates show that assumption fails here. Stage weights cannot change any per-stage completion rate (`rumour_link` stays at 10/10). They would only re-mix stages inside each journalist's average, and would up-weight early-stage claims that are almost all recorded as "completed".
   - The actual correction is getting collapses verified: the 60-row review, plus a rule that resolves a deal as collapsed once its window closes (TODOS 0a).
   - The current ledger cannot separate two explanations for `rumour_link` at 100% completion: (a) the ledger is biased, or (b) the stage table really is too low. The per-stage rates after the review are the test that tells them apart.
3. **Choose K from data, not taste.** Log the Bühlmann estimate (`k̂ = EPV/VHM`) on every run, and move K only after step 1, since the current estimate inherits the bias.
4. **Separate relays from originators.** A gossip-column item is the BBC *reporting that another paper reported*. Scoring it as BBC's call grades the aggregator for another outlet's reporting. Extract the originating outlet (the prompt already has `source_name`; add an `originating_source` field) or exclude relays from journalist scoring.
5. **The meter is effectively recency-weighted.** At ±5% around the mean, reliability barely moves `deal_probability` (`ingest/meter.py:140`). Either accept that and say so, or use a steeper transform (e.g. weight by skill vs climatology) once the ledger is less biased.
6. **Do not use Dawid–Skene/EM on live outcomes.** It would let unverified (latent) outcomes move public scores, which breaks the positive-evidence guardrail in CLAUDE.md. Use it only as an offline diagnostic.

## 6. Rumour verification literature: what transfers

**What the primary sources say**

- **RumourEval 2017** ([Derczynski et al., SemEval-2017 Task 8](https://aclanthology.org/S17-2006/)):
  - Subtask A is **stance** towards a rumour: support / deny / query / comment (SDQC).
  - Subtask B is **veracity**: true / false / unverified with a confidence. It was scored by macro-accuracy combined with an RMSE of confidence on correct items.
  - In the closed veracity variant the **baseline (0.571) beat every system** (best 0.536) ([PDF, Tables 3–4](https://arxiv.org/pdf/1704.05972)).
- **RumourEval 2019** extended the data to Reddit. The organisers conclude that "automated support for rumour verification remains in its infancy" ([Gorrell et al., SemEval-2019 Task 7](https://aclanthology.org/S19-2147/)).
- **PHEME** annotates rumour threads with how people support or deny rumours before and after resolution. It finds "the prevalent tendency for users is to support every unverified rumour", and that even reputable news organisations post claims that "often prove to be unverified" ([Zubiaga et al. 2016](https://journals.plos.org/plosone/article?id=10.1371%2Fjournal.pone.0150989)).
- **Emergent** is the closest analogue to this site. It holds 300 rumoured claims and 2,595 *news articles*, each labelled by journalists for stance and tracked to a veracity of true / false / unverified ([Ferreira & Vlachos, NAACL 2016](https://aclanthology.org/N16-1138/)).
- **FEVER** verifies claims against evidence with three labels, Supported / Refuted / **NotEnoughInfo**, and requires the evidence sentences to be cited ([Thorne et al., NAACL 2018](https://aclanthology.org/N18-1074/)).

**What transfers**

- **Stage is stance plus degree.** The repo's `stage` is SDQC-like stance with graded commitment (`denied` = deny, the others = support at a given strength). The literature does not grade the strength of support. Transfer Truth's mapping is its own design and should be defended and calibrated as such (§4).
- **The site's design matches the literature.** It grades *sources* against *resolved* outcomes instead of predicting veracity from text, which RumourEval shows is hard. The resolver's "unclear", together with the positive-evidence rule, is FEVER's NotEnoughInfo, and it keeps the precision-over-recall posture of `outcome/apply.py`.
- **Resolution timing explains the ledger.** The PHEME/Emergent pattern (true resolves fast, false resolves late or never) is why the ledger is completion-heavy. It argues for an explicit collapse rule tied to window close (TODOS 0a) as well as human review.
- **Relays break source attribution.** Emergent labels *each article's* stance toward a claim. The site should likewise separate the article (the BBC gossip column) from the claim's originator (§5.4).

**What's missing.** None of these datasets is about sports transfers, grades named journalists over time, or grades graded commitment against outcomes. The search found no peer-reviewed dataset on football-transfer rumours. The claim "there isn't one" is therefore **unverified**, not established.

---

## Prioritised recommendations

| # | Recommendation | Evidence | Files touched | Effort | Risk |
|---|---|---|---|---|---|
| 1 | Get collapses verified: human-review the 60 `collapsed, auto` deals and add a window-close collapse rule (TODOS 0a). Report per-stage propensity and per-outcome promotion rates as a **diagnostic**. Do not use stage IPW: verification depends on the outcome (49% vs 18%). | [Begg & Greenes 1983](https://pubmed.ncbi.nlm.nih.gov/6871349/); [PHEME](https://journals.plos.org/plosone/article?id=10.1371%2Fjournal.pone.0150989) | `ground-truth/deals.csv` (data via `outcome/promote.py`); `outcome/apply.py` (0a); new report script or `scoring/score.py` output | S (review, report) / M (0a) | Low for review and report; medium for 0a (resolution rule, plan mode) |
| 2 | Cap reasoning explicitly (`reasoning_budget` < `max_tokens`, or `reasoning_effort="low"`) and treat `finish_reason=="length"` as its own error. | [Hosted API ref](https://docs.api.nvidia.com/nim/reference/nvidia-nemotron-3-super-120b-a12b-infer); [model card](https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-BF16) | `engine/run.py:95-104`, `outcome/source.py` | S | Low; must pass `golden-eval.yml` |
| 3 | Pass the publication date (DCT) to the extractor. Add an event-time relation field. Date the golden cases. Derive the window from `claim_date` (TODOS 0d). | [TimeML](https://timeml.github.io/site/publications/timeMLdocs/timeml_1.2.1.html); [TempEval-1](https://aclanthology.org/S07-1014.pdf); [TempEval-3](https://aclanthology.org/S13-2001.pdf) | `ingest/pipeline.py:69,122-124,173`, `engine/transfer-analyst-system-prompt.md`, `tests/golden/cases.jsonl`, `engine/golden.py`, `ingest/cluster.py`/`site/windows.py` | M | Medium; prompt change and re-keying of new claims |
| 4 | Golden eval with K repeats, a Wilson CI, paired per-case A/B, and K/K on regressions. | [Miller 2024](https://arxiv.org/abs/2411.00640); [Atil et al.](https://arxiv.org/abs/2408.04667); [Brown et al. 2001](https://projecteuclid.org/journals/statistical-science/volume-16/issue-2/Interval-Estimation-for-a-Binomial-Proportion/10.1214/ss/1009213286.full) | `engine/golden.py`, `tests/test_golden.py:100-108`, `.github/workflows/golden-eval.yml` | S–M | Low; K× API calls |
| 5 | Take the earliness bonus out of the ranked score (it is not proper). Publish the Murphy decomposition and Brier skill score. | [Gneiting & Raftery 2007](https://sites.stat.washington.edu/raftery/Research/PDF/Gneiting2007jasa.pdf); [Murphy 1973](https://journals.ametsoc.org/view/journals/apme/12/4/1520-0450_1973_012_0595_anvpot_2_0_co_2.xml) | `scoring/score.py:71-120`, `site/build_leaderboard.py` | S–M | Medium; visible leaderboard change, plan mode |
| 6 | Attribute relayed (gossip-column) claims to the originating outlet, or exclude them from journalist scores. | [Emergent](https://aclanthology.org/N16-1138/); [RumourEval](https://aclanthology.org/S17-2006/) | prompt (`originating_source`), `ingest/pipeline.py`, `ingest/bridge.py`, `ground-truth/journalist_claims.csv` | M | Medium; changes who gets credit |
| 7 | Log a data-driven Bühlmann k. Change K=20 only after rec. 1. | [Bühlmann 1967](https://www.casact.org/sites/default/files/database/astin_vol4no3_199.pdf); [Efron & Morris 1975](https://www.tandfonline.com/doi/abs/10.1080/01621459.1975.10479864) | `scoring/score.py:28,118` | S | Low if log-only |
| 8 | Test hosted `nvext.guided_json` / `response_format` json_schema through a golden-eval dispatch. Adopt it only if it is accepted and does not regress. | [NIM 1.12 structured generation](https://docs.nvidia.com/nim/large-language-models/1.12.0/structured-generation.html); [OpenAI SO](https://developers.openai.com/api/docs/guides/structured-outputs); [vLLM](https://docs.vllm.ai/en/latest/features/structured_outputs.html) | `engine/run.py` (keep `parse_engine_json` fallback) | S | Low; behind the gate |
| 9 | Recalibrate `STAGE_P` with a beta-smoothed per-stage estimate centred on the current values, **only after** recs 1 and 5. Never isotonic at this n. | [Niculescu-Mizil & Caruana 2005](https://www.cs.cornell.edu/~alexn/papers/calibration.icml05.crc.rev3.pdf); [sklearn](https://scikit-learn.org/stable/modules/calibration.html); [CORP](https://www.pnas.org/doi/10.1073/pnas.2016191118) | `stagemap.py`, prompt table, `ml/deal_predictor.py` | M | High; moves every score and meter |

## Verified in-session (2026-09-27): measurements behind the table

These were run against the live ledger and the live NIM endpoint after the research above,
to confirm or refute its claims. Scripts were throwaway; the numbers are reproducible from
the ledger at commit `31c00f8` and the model `nvidia/nemotron-3-super-120b-a12b`.

**Stage calibration on verified outcomes (why rec. 9 must wait for rec. 1).** Over 138
verified deals (125 completed, 13 collapsed) and 285 scored claims, every stage completes
at 75–100%. That includes `interest` (table 0.15, observed 0.93, n=28) and `denied` (table
0.02, observed 0.75, n=8). The overall Brier score is 0.175 with the table and 0.038 with
per-stage observed rates. That is not evidence that the table is wrong; it is the
verification bias of rec. 1 made visible.
- For the 2026 window: 100 completions are verified and 0 collapses are.
- 60 auto collapses and 129 auto completions await `outcome/promote.py`.

**Stale evidence in the collapse queue (TODOS 0a, now the top data risk).** 31 of the 60
auto collapses cite only pre-2026 years in their evidence. Of those:
- 15 are "joined elsewhere" verdicts. These are the most suspect: e.g. deal 418, Harvey
  Elliott → Valencia, is collapsed on a 2025 move.
- 16 are "window closed, stayed". A stay can be legitimately undated, so these are weaker
  signals.

Review these by hand before any bulk promotion.

**Truncation hypothesis (rec. 2): not confirmed, but the headroom was thin.** 27 golden
cases, same call as `engine/run.py`:
- 0 of 27 had `finish_reason == "length"`; 0 of 27 were unparsed.
- Completion tokens: median 676, max 1710, which is 84% of the old 2048 cap, on
  headline-only inputs.

Action taken: default cap raised to 4096, and truncation is now logged as its own event
(`engine.run.finished_text`, used by both call sites).

**Constrained decoding on the hosted endpoint (rec. 8, resolves a "could not verify").**
- `extra_body={"nvext": {"guided_json": ...}}` is REJECTED: 400, "unknown field
  `guided_json`".
- `response_format={"type": "json_schema", ...}` is ACCEPTED and returns pure,
  schema-conformant JSON.

Full A/B with the complete output contract as the schema, one run each:

| Mode | Pass | Field accuracy | Parse failures | Regressions |
|---|---|---|---|---|
| Free-form | 89% (24/27) | 98% | 0 | 2/2 |
| Constrained | 85% (23/27) | 98% | 0 | 2/2 |

Constrained latency: median 6.7s, max 64.3s.

The one-case gap is inside the n=27 noise shown in §3. **Not adopted.** Decide with the
repeated, paired A/B of rec. 4.

**First repeated baseline (`python engine/golden.py 3`, current prompt and model).**
- 27 cases × 3 runs: **pass 91%, 95% CI 75–97%** (Wilson, over cases). 24 cases pass
  every run.
- Flaky: `woltemade-talks` (1/3, "Stuttgart" vs "VfB Stuttgart") and
  `postecoglou-manager` (1/3, manager read as a player).
- Stable miss: `olise-meeting-talks` fails 0/3 on stage nuance. That's a real
  prompt target, not noise.
- Regressions: both cases 3/3.

So the single runs earlier today (89% free-form, 85% constrained) both sit inside this
interval. They cannot rank the two modes; use `golden.paired()` on repeated runs.

**Truncation does happen, rarely.** In those ~87 calls, one extraction ran to the full
4096-token cap: a runaway reasoning trace, now logged by `finished_text`. At the old 2048
cap it, and any trace between 2048 and 4096, would have failed as a "contract break". This
supports rec. 2's second half: set an explicit `reasoning_budget` below `max_tokens`. It
changes model behaviour, so it must go through the golden gate.

**`reasoning_budget` tested: accepted, but NOT adopted.** The hosted endpoint honours
`extra_body={"reasoning_budget": N}`: at N=256 a call used 424 completion tokens against
675 unbudgeted, and still finished with valid JSON. Repeated test at N=3072 (27 × 3,
`golden.paired` against the baseline above):

| Setting | Pass | 95% CI |
|---|---|---|
| Baseline (no budget) | 91% | 75–97% |
| Budget 3072 | 86% | 69–95% |

- Paired comparison: worse on 3 cases (kane, postecoglou, woltemade), better on 0,
  p = 0.25. Not significant, but every disagreement went the wrong way.
- The one runaway still ran to the full 4096-token cap under the budget, so it didn't
  stop the failure it was meant to stop.

Decision: keep the knob (`NIM_REASONING_BUDGET`, off by default) for future A/Bs. Rely
on the 4096 cap, the truncation log, and `analyze()`'s retry for the ~1-in-90 runaway.

**Resolver evidence-window guard shipped (TODOS 0a, commit `5c34bc2`).**
- Re-resolved live, 3 of 4 sampled stale "joined elsewhere" collapses had in fact
  completed in 2026.
- The new check re-opens 17 of the 60 queued collapses for re-resolution (10 per run).
- The ground-truth gate caught two bad fallback designs before they shipped:
  - contract-end years ("until 2032") being read as the move date;
  - a verified completion (deal 114) whose Wikipedia quote is stale.

**Other claims re-checked.**
- The golden-floor probabilities reproduce exactly: a true 75% clears the 80% floor 30% of
  the time, and a true 85% fails it 21% of the time.
- The gossip-relay count reproduces: 100 "BBC Sport" claims have "gossip" in the headline,
  and 17 of them are scored.
- The earliness bonus is at `scoring/score.py:71-92`, as described.

## Could not verify / thin evidence

- **Hosted NIM and constrained decoding:** whether the hosted endpoint honours `nvext`/`response_format` is not documented either way. It needs a live test with the key, which this research did not use.
- **Temperature 0 on the hosted endpoint:** the documented schema excludes 0, but the repo's calls succeed. What the server actually does with 0 is unknown.
- **Checked against raw text.** An earlier tool summary of TempEval-3 contained invented figures, so the following were re-checked against the PDF, HTML or JSON text itself:
  - TempEval-1 and TempEval-3 scores, RumourEval 2017 scores, the PHEME 2h/14h figures, Miller's recommendations, the 2/3 cap and "thermostat" advice.
  - Niculescu-Mizil & Caruana's 200–1000 threshold, Gneiting & Raftery's definition, and Bühlmann's `k`.
  - The hosted API schema (`reasoning_budget` "Defaults to 16384"; temperature `"maximum":1,"exclusiveMinimum":0`).
  - vLLM's v0.12.0 removal warning and the `enable_in_reasoning` flag, and Thinking Machines' "80 unique completions".
- **Abstract or summary level only, not checked against full text:**
  - Yuan et al., Chan et al., TRAM and SituatedQA findings.
  - Efron & Morris's "less than half the MSE".
  - Begg & Greenes' method description (inverse weighting within strata).
  - Dawid & Skene.
  - RumourEval 2019's quoted sentence and the FEVER dataset details.
  - The OpenAI structured-outputs wording and the NIM 1.12.0 `nvext` page, which was read through a summariser; the parameter names agree across two independent fetches.
- **Brown, Cai & DasGupta:** the abstract gives no numeric threshold for "small n". The full-text PDF available was a scanned JSTOR copy that could not be text-searched.
- **The k≈10 estimate** comes from a biased, heavily unbalanced sample (15 of 21 sources have n=1). It shows that K can be estimated. It is not a recommended value.
- **No primary-source dataset on football-transfer rumours was found.**

## Sources

- TimeML 1.2.1 spec: https://timeml.github.io/site/publications/timeMLdocs/timeml_1.2.1.html
- Verhagen et al. 2007, TempEval (SemEval-2007 Task 15): https://aclanthology.org/S07-1014.pdf
- UzZaman et al. 2013, TempEval-3: https://aclanthology.org/S13-2001.pdf
- Saurí & Pustejovsky 2009, FactBank: https://link.springer.com/article/10.1007/s10579-009-9089-9
- Yuan, Xie & Ananiadou 2023: https://aclanthology.org/2023.bionlp-1.7/
- Chan et al. 2024: https://arxiv.org/abs/2304.14827
- Wang & Zhao 2024, TRAM: https://arxiv.org/abs/2310.00835
- Zhang & Choi 2021, SituatedQA: https://arxiv.org/abs/2109.06157
- NVIDIA NIM for LLMs 1.12.0, Structured Generation: https://docs.nvidia.com/nim/large-language-models/1.12.0/structured-generation.html
- NVIDIA hosted API reference, nemotron-3-super-120b-a12b: https://docs.api.nvidia.com/nim/reference/nvidia-nemotron-3-super-120b-a12b-infer
- Nemotron-3-Super model card (HF): https://huggingface.co/nvidia/NVIDIA-Nemotron-3-Super-120B-A12B-BF16
- Nemotron-3-Super model card (NVIDIA): https://build.nvidia.com/nvidia/nemotron-3-super-120b-a12b/modelcard
- vLLM structured outputs: https://docs.vllm.ai/en/latest/features/structured_outputs.html
- vLLM reasoning outputs: https://docs.vllm.ai/en/latest/features/reasoning_outputs.html
- OpenAI Structured Outputs: https://developers.openai.com/api/docs/guides/structured-outputs
- Atil et al., Non-Determinism of "Deterministic" LLM Settings: https://arxiv.org/abs/2408.04667
- He / Thinking Machines 2025, Defeating Nondeterminism in LLM Inference: https://thinkingmachines.ai/blog/defeating-nondeterminism-in-llm-inference/
- Miller 2024, Adding Error Bars to Evals: https://arxiv.org/abs/2411.00640
- Brown, Cai & DasGupta 2001: https://projecteuclid.org/journals/statistical-science/volume-16/issue-2/Interval-Estimation-for-a-Binomial-Proportion/10.1214/ss/1009213286.full
- Brier 1950: https://journals.ametsoc.org/view/journals/mwre/78/1/1520-0493_1950_078_0001_vofeit_2_0_co_2.xml
- Murphy 1973: https://journals.ametsoc.org/view/journals/apme/12/4/1520-0450_1973_012_0595_anvpot_2_0_co_2.xml
- Gneiting & Raftery 2007: https://sites.stat.washington.edu/raftery/Research/PDF/Gneiting2007jasa.pdf (DOI 10.1198/016214506000001437)
- Dimitriadis, Gneiting & Jordan 2021, CORP: https://www.pnas.org/doi/10.1073/pnas.2016191118
- Niculescu-Mizil & Caruana 2005: https://www.cs.cornell.edu/~alexn/papers/calibration.icml05.crc.rev3.pdf
- scikit-learn probability calibration: https://scikit-learn.org/stable/modules/calibration.html
- Bühlmann 1967, Experience rating and credibility: https://www.casact.org/sites/default/files/database/astin_vol4no3_199.pdf
- Efron & Morris 1975: https://www.tandfonline.com/doi/abs/10.1080/01621459.1975.10479864
- Begg & Greenes 1983: https://pubmed.ncbi.nlm.nih.gov/6871349/
- Dawid & Skene 1979: https://academic.oup.com/jrsssc/article/28/1/20/6953573
- Derczynski et al. 2017, RumourEval: https://aclanthology.org/S17-2006/ (PDF https://arxiv.org/pdf/1704.05972)
- Gorrell et al. 2019, RumourEval 2019: https://aclanthology.org/S19-2147/
- Zubiaga et al. 2016, PHEME threads: https://journals.plos.org/plosone/article?id=10.1371%2Fjournal.pone.0150989
- Ferreira & Vlachos 2016, Emergent: https://aclanthology.org/N16-1138/
- Thorne et al. 2018, FEVER: https://aclanthology.org/N18-1074/
