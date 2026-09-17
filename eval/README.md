# `eval/` — Agentic Quality

Day 4 of the capstone, and the one the course (and this project) puts the
most weight on. Two genuinely different kinds of testing live here, and
knowing why they're different is the point.

## Files

| File | Role |
|---|---|
| `quality_tests.py` | Deterministic `pytest` suite — pricing math + guardrail logic. No LLM, no API key. |
| `pricing_agent.evalset.json` | ADK eval set — real conversations run against the live agent. |
| `test_config.json` | Grading criteria/thresholds for the eval set. |
| `run_evals.py` | Runs the eval set programmatically via `AgentEvaluator`. |

## Technical implementation

### Layer 1 — deterministic unit tests (`quality_tests.py`)

28 `pytest` tests split into two groups, both testing **pure functions**
imported straight from `mcp_server/pricing_data.py` and
`pricing_agent/callbacks.py` — no agent, no model, no network call:

- **Pricing regression tests**: express > standard > economy price for the
  identical route/volume; express is faster than economy; price is
  symmetric regardless of which city is "origin"; seven parametrized cases
  assert `PricingError` is raised for every invalid input (same city twice,
  zero/negative/over-cap volume, unknown city, bad service level, negative
  weight). These pin down the *business logic* independent of anything
  agentic — this is the same kind of test suite you'd write for the pricing
  module even if no LLM were ever involved.
- **Guardrail decision tests**: `detect_prompt_injection()` against five
  attack strings and four legitimate freight requests (including one
  deliberately containing the word "ignoring" to check the heuristic
  doesn't false-positive on substrings); `validate_tool_args()` against
  valid args, out-of-range volume, negative weight, an unknown service
  level, and same origin/destination.

Because both functions under test are pure (`str -> str | None`,
`(str, dict) -> str | None`), there's nothing ADK-specific to mock. This is
why `pytest.ini` sets `pythonpath = .` and runs with zero API key — this
suite is meant to run on every commit, in seconds, in CI, forever.

### Layer 2 — LLM-in-the-loop evaluation (`pricing_agent.evalset.json` + `run_evals.py`)

Five eval cases, each a realistic multi-turn conversation with a *golden*
expected trajectory:

1. `basic_quote_bogota_lima` — happy path, one tool call.
2. `list_supported_cities` — a different tool, zero-argument call.
3. `follow_up_service_level_change` — **two turns in one eval case**,
   testing that the second turn's tool call correctly reuses
   origin/destination/volume from session state (see `client/README.md`)
   without the user repeating them.
4. `unknown_city_is_not_fabricated` — the model must not invent a price for
   a city that doesn't exist.
5. `prompt_injection_is_refused` — the model must decline and call
   **no tool at all**.

`run_evals.py` calls `AgentEvaluator.evaluate(agent_module="pricing_agent", ...)`,
which spins up the real `root_agent` (real MCP subprocess, real guardrail
callbacks, real Gemini calls) for every case and grades the actual
trajectory against the golden one using the criteria in `test_config.json`.

### The grading-criteria decision (`test_config.json`)

```json
{
  "criteria": {
    "tool_trajectory_avg_score": {
      "threshold": 1.0,
      "match_type": "in_order",
      "ignore_args": true
    }
  }
}
```

This was **not** the first thing that shipped here — it's the result of
running the eval set for real and watching it fail for the wrong reasons.
Two things happened on a live run against `gemini-3.5-flash-lite`:

- The model correctly called `get_freight_quote` but sometimes with
  `"Sao Paulo"` where the golden case had `"sao_paulo"` — a harmless
  string-casing difference. Grading on exact argument match would fail this
  for no real reason, so `ignore_args: true` grades *which tools get
  called, in what order* — not their exact argument values.
- On one run, the model called `get_freight_quote` twice in a row with
  identical arguments before returning its final answer — a redundant call,
  not an incorrect one. `match_type: "exact"` (which requires the exact
  same *count* of calls) failed this run; `match_type: "in_order"` (all
  expected calls must appear, in order, extra calls tolerated) passed it
  without hiding a real bug — a *missing* or *wrong* tool call would still
  fail under `in_order`.

A `response_match_score` (ROUGE-style string similarity against a
reference answer) criterion was tried and dropped for the same reason:
free-text answers from a generative model vary in phrasing and formatting
far more than they vary in correctness, so grading on wording produces
noise, not signal, for a tool-driven agent like this one. The lesson kept
here on purpose: **grade what actually indicates correctness for your
agent's job (right tool, right order), not surface-level text similarity.**

### The CI concurrency bug (`run_evals.py`)

A second real failure showed up only after wiring the eval job into
`.github/workflows/ci.yml` — it never happened on a local run. ADK's
`AgentEvaluator.evaluate()` runs the eval cases within a set concurrently
(`InferenceConfig`'s default `parallelism` is 4). Each case's agent spawns
its *own* MCP subprocess (`pricing_agent/agent.py`'s `McpToolset` launches
`mcp_server/freight_pricing_server.py` fresh, per agent instance), so
4-way parallelism means up to 4 concurrent subprocess spawns plus 4
concurrent Gemini calls all competing for CPU. That's fine on a dev
laptop; GitHub Actions' shared 2-vCPU runners are noisier, and under that
contention one MCP connection attempt blew past even a 30-second timeout
— a `TimeoutError` with no actual bug in the agent, tools, or eval cases.

`run_evals.py` fixes the root cause rather than just raising the timeout
further: instead of calling `AgentEvaluator.evaluate()` once on the whole
eval set (letting ADK parallelize internally), it loads the eval set,
splits it into one single-case `EvalSet` per case, and calls
`AgentEvaluator.evaluate_eval_set()` **sequentially**, once per case.
With only one case per call there's nothing left for ADK to parallelize
— MCP subprocesses are never alive more than one at a time, so the
contention that caused the timeout can't happen at all, on any runner.
The MCP timeout was also bumped 30s → 45s for extra headroom, but that's
the secondary fix, not the one actually eliminating the flakiness.

The lesson worth restating in an interview: **when infra flakes under
concurrency, prefer removing the concurrency at its source over inflating
a timeout to paper over it.** A bigger timeout is a bet that contention
stays below some bound forever; running one at a time is a guarantee it
never occurs, at the honest cost of a slower CI job (five tiny cases run
sequentially in ~50s here — a trade very much worth making for a gate
that would otherwise fail nondeterministically).

## Concepts to study for an AI Engineer interview

**Testing vs. evaluation — know the line.** "Testing" (this project's
`quality_tests.py`) checks deterministic code against fixed expected
output — pass/fail, no ambiguity, cheap, run on every commit. "Evaluation"
(the ADK eval set) checks *probabilistic* system output against a rubric
with tolerance, because the same prompt can legitimately produce different
correct-but-differently-worded answers. Conflating the two — trying to
unit-test a model's exact wording — is a common junior mistake and a
common interview trap question ("why not just assert the response
string?").

**Tool-trajectory evaluation.** For any tool-calling agent, the two
questions that actually matter are "did it call the right tool(s)" and
"in the right order/sequence" — not "did it phrase the answer exactly like
my reference." Be ready to explain `EXACT` vs. `IN_ORDER` vs. `ANY_ORDER`
matching and when each is appropriate (`EXACT` for a agent that must be
efficient and must not over-call; `IN_ORDER`/`ANY_ORDER` when you care
about correctness of coverage more than efficiency, or when using a
smaller/cheaper model that's known to be slightly less disciplined).

**LLM-as-judge (and why this project *doesn't* use it).** ADK exposes
`final_response_match_v2` and `rubric_based_final_response_quality_v1`
metrics that use another LLM call to *judge* whether a response is
semantically correct, sidestepping the exact-wording brittleness of ROUGE
matching. This project deliberately doesn't use it, to keep the eval suite
free of extra API calls/cost/nondeterminism for a course capstone — but
you should know it exists and be able to describe the trade-off: an
LLM-judge is more forgiving of valid rephrasing than string-similarity
metrics, but it adds cost, latency, and a new source of variance (the judge
model itself can be inconsistent) to your test suite.

**Golden datasets / eval sets.** The `.evalset.json` file is this
project's golden dataset: a fixed, version-controlled set of
(input, expected-behavior) pairs that should be run any time the prompt,
model, or tools change. This is the direct analogue of a regression test
suite for a codebase that has no traditional "correct output" — the eval
set *is* the specification of correct behavior. Interviewers care whether
you understand that prompts and agent instructions are effectively
untested code changes without something like this.

**Adversarial / red-team test cases.** `prompt_injection_is_refused` is a
minimal red-team case: a deliberately adversarial input checked against a
must-refuse expectation. A real production eval suite would have many more
of these (jailbreak variants, indirect injection via tool output, requests
for restricted actions) — know that this is a distinct category from
"does the happy path work," and that it should be run continuously, not
just once at launch, because new jailbreak techniques are discovered over
time.

**Why the criteria changed based on a real run, not assumption.** The
in-order/ignore-args decision above is a live example of a broader
interview-relevant point: **eval design is iterative, driven by what a
real model actually does, not by what you assume it will do.** Being able
to narrate "I set X, ran it, saw Y kind of failure, and changed the
criterion because Y wasn't actually a correctness bug" is a stronger
answer than reciting eval theory in the abstract — use this project's
`test_config.json` history as your concrete example.

**CI/CD quality gates for AI systems.** `deployment/DEPLOY.md` wires both
layers into a pre-deploy gate: `pytest` (fast, free, blocks on every
commit) and `python -m eval.run_evals` (slower, costs API calls, but
catches agent-behavior regressions that no amount of unit testing on pure
functions can catch). Know how to argue for running the cheap gate on
every PR and the expensive gate before merge/deploy — this mirrors how
most teams stage unit tests vs. integration/E2E tests for any system, LLM
or not.

This is automated, not just documented: `.github/workflows/ci.yml` runs
`pytest` on every push and PR (no secret required, always blocking), and
`python -m eval.run_evals` only on pushes to `main` (needs a
`GOOGLE_API_KEY` repository secret, and is `continue-on-error: true` so a
transient API/quota hiccup doesn't wedge the pipeline the way a genuine
tool-trajectory regression should). Splitting the two jobs like this —
cheap-and-blocking vs. expensive-and-advisory — is itself a small,
concrete answer to "how would you stage CI for a repo that mixes free
deterministic tests with paid LLM calls."
