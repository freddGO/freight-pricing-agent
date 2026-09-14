# `client/` — Sessions & Memory (Context Engineering)

Day 3 of the capstone. This folder owns the pieces the agent itself never
touches directly: `SessionService` and `MemoryService`. That separation
is the whole lesson.

## Files

| File | Role |
|---|---|
| `cli_client.py` | Owns both services, drives the ADK `Runner`, and (in `--demo` mode) proves session state and cross-session memory both work. |
| `persistence.py` | `$0` durable alternative to the in-RAM services: `DatabaseSessionService` (SQLite) + a hand-rolled `SqliteMemoryService` that mirrors ADK's own keyword-overlap algorithm exactly. |

## Technical implementation

### Two different problems, two different services

It's tempting to think of "context" as one thing. ADK — and most serious
agent architectures — split it into two services with very different
lifetimes and purposes:

| | `SessionService` | `MemoryService` |
|---|---|---|
| Scope | One conversation | Across all of a user's conversations |
| Contents | The exact turn-by-turn event log + working `state` dict | Distilled/searchable facts extracted from past sessions |
| Lifetime | Until the conversation ends (or is explicitly resumed) | Long-term, persists after the session is gone |
| Access pattern | Automatically included in every model call for that session | Only retrieved when the agent explicitly calls a memory tool |
| Backend used here | `InMemorySessionService` (dev only) | `InMemoryMemoryService` (dev only) |
| Production backend | `DatabaseSessionService` / `VertexAiSessionService` | `VertexAiMemoryBankService` / a RAG-backed store |

### Session state in action

```python
session = await session_service.create_session(app_name=..., user_id=user_id)
await _send(runner, user_id, session.id, "I need to ship 8 m3 from Bogota to Santiago.")
await _send(runner, user_id, session.id, "Actually make that express instead.")
```

Both calls reuse `session.id`. The second message never repeats the route
or volume — the model sees the *entire prior turn* (both the user's
message and its own previous tool call/response) because `Runner.run_async`
loads the full session history before calling the model. This is what
"short-term context" means concretely: it's not magic, it's the framework
re-sending the conversation transcript every turn (up to whatever context
window/summarization strategy is configured).

### Long-term memory in action

```python
committed_session = await session_service.get_session(app_name=..., user_id=user_id, session_id=session_1.id)
await memory_service.add_session_to_memory(committed_session)

session_2 = await session_service.create_session(app_name=..., user_id=user_id)  # brand new, empty state
await _send(runner, user_id, session_2.id, "What route did I quote before?")
```

`session_2` starts with **zero** session state — the model has no direct
access to `session_1`'s events. The only way it can answer correctly is by
calling the `load_memory` tool (wired into `root_agent.tools` in
`pricing_agent/agent.py`), which calls `MemoryService.search_memory()`
under the hood to retrieve relevant facts from *any* of that user's past
sessions that were previously committed with `add_session_to_memory()`.
This is the client code proving, not asserting, that memory is genuinely
decoupled from the active session.

### Deep dive: what actually happens inside `load_memory`

The two code blocks above show *that* memory recall works. This section
is *how*, traced through ADK's real source
(`google/adk/tools/load_memory_tool.py` and
`google/adk/memory/in_memory_memory_service.py`) rather than the framework's
marketing description of itself — the actual mechanics have real, useful
gaps to know about.

**The call chain for "what did I quote before?" in a brand-new session:**

```
LlmAgent sees `load_memory` in root_agent.tools
        │
        ▼
Gemini decides to call load_memory(query="...")   ← model-generated query string
        │
        ▼
LoadMemoryTool.load_memory(query, tool_context)
        │
        ▼
tool_context.search_memory(query)
        │  (fills in app_name/user_id from the *current* invocation)
        ▼
InMemoryMemoryService.search_memory(app_name=..., user_id=..., query=...)
        │
        ▼
returns SearchMemoryResponse(memories=[...])  → back to the model as the tool result
        │
        ▼
Gemini reads the retrieved memories and writes the final answer
```

**1. The tool secretly rewrites its own instructions every turn.**
`LoadMemoryTool` overrides `process_llm_request`, which runs on *every*
model call for as long as the tool is attached:

```python
async def process_llm_request(self, *, tool_context, llm_request):
    await super().process_llm_request(...)
    llm_request.append_instructions(["""
You have memory. You can use it to answer questions. If any questions need
you to look up the memory, you should call load_memory function with a query.
"""])
```

This is why `pricing_agent/prompts.py` only needs one sentence about
`load_memory` — the tool injects its own usage instructions into the
prompt the moment it's included in `tools=[...]`. Worth internalizing as a
general pattern: some ADK tools ship their own "usage manual" that gets
silently merged into context, so an agent's *effective* system prompt is
larger than the string you wrote by hand.

**2. What gets written to memory is raw events, not a summary.**

```python
async def add_session_to_memory(self, session: Session) -> None:
    ...
    self._session_events[user_key][session.id] = [
        event for event in session.events if event.content and event.content.parts
    ]
```

No LLM call, no summarization, no compression — `add_session_to_memory()`
(called explicitly by `cli_client.py`) just files away every event in the
session that has content. **ADK's default memory is a raw transcript
store, not a memory-extraction pipeline.** `VertexAiMemoryBankService`,
the production swap-in named above, *does* run an LLM extraction step at
write time — that's the actual feature difference between prototyping and
production memory here, not just "persistent vs. in-memory."

**3. Retrieval is keyword overlap, not embeddings.**
`InMemoryMemoryService`'s own docstring says it outright: *"Uses keyword
matching instead of semantic search. A search returns at most ten
memories, the ones sharing the most words with the query."* Stripped down:

```python
words_in_query = _extract_words_lower(query)          # set of lowercase tokens
for event in all_stored_events:
    words_in_event = _extract_words_lower(event_text)
    matched = sum(1 for w in words_in_query if w in words_in_event)
    if matched:
        scored.append((matched, MemoryEntry(...)))
scored.sort(key=lambda x: -x[0])
return scored[:10]
```

Plain word-overlap counting, capped at 10 results, ties broken by
insertion order — no cosine similarity, no vector index, no notion of
"these words are synonyms." (There's even a small fallback for accented
text: `Bogotá` doesn't always tokenize the way you'd expect, so non-ASCII
query words get a substring check against the lowercased raw text instead
of a strict token match.)

**Concrete consequence, observed in this project's own demo run:** when
session #2 asked "what route and volume did I quote before," retrieval
worked *only* because the model's own final-answer text from session #1 —
`"...8 m³ from Bogotá to Santiago..."` — is plain natural-language text
containing those literal words. The *tool-call* events
(`get_freight_quote(origin=..., volume_m3=8, ...)`) were also stored, but
they carry `FunctionCall`/`FunctionResponse` parts with no `.text`, so
`search_memory`'s `if not words_in_event: continue` guard silently skips
them. **The model's own conversational recap of a tool call is what's
searchable — the structured tool arguments themselves are not.** If the
model had answered turn 1 tersely without restating the route, session
#2's recall would likely have come back empty. This is a real, non-obvious
failure mode worth naming in an interview: memory quality here is
downstream of how chatty the model's own responses were, not of what the
tool actually computed.

**4. The commit is manual by design, not an oversight.** Nothing in ADK
auto-commits a session to memory. `cli_client.py` calls
`add_session_to_memory()` at one chosen checkpoint (end of session 1).
Committing every turn would be wasteful and would pollute long-term memory
with mid-conversation churn; committing too rarely risks losing content if
a session ends uncleanly. Production systems typically pick one of:
session-end commit, an explicit "remember this" trigger, or a periodic
background job.

**5. "For prototyping only" is a literal, load-bearing warning, not
boilerplate.** From the class docstring: *"This class is thread-safe,
however, it should be used for testing and development only."* Two
concrete gaps beyond "it's in-memory and dies on restart": no relevance
ranking beyond literal word overlap (a paraphrased query can miss a
memory that expressed the same idea in different words), and no cap on
the underlying store at all — `_session_events` grows forever per user;
the 10-result cap applies only to *search output*, never to what's
retained. Both are exactly what a vector-DB-backed service fixes: LLM-
driven extraction at write time, embedding similarity at read time — at
the cost of extra latency and $ per write and per read.

### Making it durable: `client/persistence.py` ($0, SQLite)

Everything above uses `InMemorySessionService`/`InMemoryMemoryService` —
i.e. it all lives in one Python process's RAM and disappears the moment
that process exits (see the "where does the data live" deep dive above).
`persistence.py` is the durable counterpart, at zero infrastructure cost:

```python
def build_services(persistent: bool) -> tuple[BaseSessionService, BaseMemoryService]:
    if not persistent:
        return InMemorySessionService(), InMemoryMemoryService()

    session_service = DatabaseSessionService(
        db_url=f"sqlite+aiosqlite:///{DATA_DIR / 'sessions.db'}"
    )
    memory_service = SqliteMemoryService(DATA_DIR / "memory.db")
    return session_service, memory_service
```

Two different persistence strategies, one per service, because ADK ships
a production-grade solution for one of them but not the other:

- **Sessions** → ADK's own `DatabaseSessionService`, pointed at a local
  SQLite file via SQLAlchemy's async driver
  (`sqlite+aiosqlite:///.data/sessions.db`). This is a real, ADK-maintained
  implementation — nothing custom here, just a different `db_url` than
  Postgres/Spanner would use. Requires `pip install "google-adk[db]"` (pulls
  in SQLAlchemy; `aiosqlite` is already a base ADK dependency).
- **Memory** → ADK does *not* ship a SQL-backed `BaseMemoryService` out of
  the box — only `InMemoryMemoryService` (RAM) and two Vertex AI-hosted
  options (billed, GCP-project-required — see the "concepts" section
  below). So `SqliteMemoryService` in this file is a from-scratch
  `BaseMemoryService` subclass, deliberately implementing the *exact same*
  tokenizer and "count shared words, keep the top 10" scoring as ADK's
  `InMemoryMemoryService` (see the deep dive above) — just against a
  SQLite table instead of a Python `dict`. That parity is intentional: the
  goal is to change *where the bytes live*, not *how good retrieval is*,
  so the two backends are a fair, like-for-like comparison.

Usage — either mode works with both CLI modes:

```bash
python -m client.cli_client --persist --demo   # scripted demo, SQLite-backed
python -m client.cli_client --persist          # interactive; commits to memory on quit
python -m client.cli_client --persist          # run again — a NEW process — same recall
```

**A live run of exactly this exposed a real, worth-knowing limitation —
not a bug in `persistence.py`, but the literal-word-overlap algorithm it
faithfully mirrors.** Run 1 asked for a quote (`"...from Lima to Quito"`);
its final answer was a plain cost breakdown that never repeated the city
names. Run 2 (a fresh process, same user) asked *"What did I ship last
time, and to where?"* — the model correctly called `load_memory`, but with
the query `"last shipment previous quote route"`. Checking the on-disk
data directly confirms the write path was never the problem:

```python
>>> await SqliteMemoryService(".data/memory.db").search_memory(
...     app_name="freight_pricing_app", user_id="cli-user",
...     query="Lima Quito shipment")
# -> 1 hit: "I need to ship 12 cubic meters from Lima to Quito."
```

The data was there and perfectly retrievable — but *that specific query*
(`"last shipment previous quote route"`) shares **zero exact tokens**
with anything stored (`"shipment"` ≠ `"ship"`, `"quote"`/`"route"` appear
nowhere in either the user's message or the model's bullet-point answer),
so the score was 0 for every row and `load_memory` correctly, faithfully
returned nothing. The model then (correctly, given empty tool results)
told the user it had no record.

This is the single most important thing to take away from actually running
this, rather than just reading about it: **verifying persistence and
verifying retrieval quality are two different tests, and passing the first
tells you nothing about the second.** The storage layer worked exactly as
designed both times. Whether an answer is recallable depends entirely on
whether *some* past utterance happens to share a literal word with
*whatever query the model happens to generate* — which is precisely why
production systems move to embeddings (semantic similarity, not exact
tokens) once this failure mode starts costing real user trust.

### Why the client — not the agent — owns these services

`Runner(agent=root_agent, session_service=..., memory_service=...)`
(`_build_runner()`) is constructed in `cli_client.py`, not in
`pricing_agent/agent.py`. The agent only knows it *has* a `load_memory`
tool available; it has no reference to which concrete `MemoryService`
implementation backs it. This mirrors dependency injection: the
application (client) decides which backend to wire up per environment —
in-memory for a CLI demo, `VertexAiMemoryBankService` for production —
while the agent's code is unchanged either way.

### `--demo` vs interactive mode

`run_demo()` is a scripted, deterministic walkthrough (fixed prompts, in
order) specifically so the session-state and cross-session-memory claims
are falsifiable and repeatable — this is closer to an integration test
than a chat transcript. `run_interactive()` is the actual "client" a human
would use, and reuses every piece of the demo's plumbing (`_build_runner`,
`_send`).

## Concepts to study for an AI Engineer interview

**Context window vs. session state vs. long-term memory — three different
things people conflate.** The context window is the model's hard token
limit for a single call. Session state is application-level bookkeeping
(here: the full event log) that gets *serialized into* the context window
each call — it's bounded by the window, which is why long conversations
need summarization or truncation strategies. Long-term memory is neither:
it's an external store the model queries *on demand* via a tool, exactly
like RAG, and its size is unbounded because it's never fully loaded into
context — only the retrieved slice is. Be crisp about this distinction;
it's one of the most commonly garbled topics in agent interviews.

**Memory retrieval is RAG.** `search_memory(query)` doing a semantic/
keyword lookup over previously stored session content and returning the
most relevant snippets *is* retrieval-augmented generation, just applied
to "memories" instead of "documents." If asked to explain how you'd
implement conversational memory for a product, you can honestly say
"the same retrieval architecture as RAG, with conversation transcripts as
the corpus" — and this project is a working, minimal example of exactly
that pattern (`InMemoryMemoryService` does simple text matching;
`VertexAiMemoryBankService`/a vector DB would do embedding similarity —
same interface, different retrieval quality).

**Why explicit memory commits, not automatic.** `add_session_to_memory()`
is called explicitly at a natural checkpoint (end of session), not after
every turn. Know the trade-off you'd discuss in an interview: committing
too eagerly (every turn) is expensive and can pollute long-term memory
with irrelevant chatter; committing too rarely risks losing context if a
session ends abruptly. Real systems often use a policy (session end,
explicit "remember this" trigger, or an LLM call that decides what's
memory-worthy) rather than "always" or "never."

**`user_id` vs. `session_id` scoping.** Sessions are keyed by
`(app_name, user_id, session_id)`; memory is keyed by
`(app_name, user_id)` and spans all of that user's sessions. This two-level
key structure is standard in multi-tenant conversational systems — know
how you'd extend it (e.g. add an `org_id` for a B2B product where memory
should be shared across a team, not just one user).

**`async`/`await` in agent frameworks.** Every ADK service method used
here (`create_session`, `run_async`, `add_session_to_memory`,
`search_memory`) is a coroutine. This isn't incidental — agent frameworks
are I/O-bound (network calls to the model, to tool servers, to databases),
so async is the natural concurrency model. Be comfortable explaining why
`async for event in runner.run_async(...)` is an async *generator*
(streaming events back as they're produced, e.g. partial tokens or
intermediate tool-call events) rather than a single awaited call that
returns everything at once.

**Swappable backends via interface segregation.** `InMemorySessionService`
and `DatabaseSessionService` both implement `BaseSessionService`; the
`Runner` and the agent code depend only on that base interface. This is
the classic "program to an interface, not an implementation" principle,
and it's exactly what lets `deployment/DEPLOY.md` say "swap in-memory for
database-backed with no agent code changes" and have that actually be
true, not aspirational.

**Persistence vs. retrieval quality are two separate claims — verify
both, separately.** `persistence.py`'s own live-run finding (above) is a
concrete, defensible interview example: "does the data survive a process
restart" and "will the system find the right memory when asked a vaguely
worded question" are independent properties. A system can pass the first
test perfectly and still fail the second on the very next real query. Be
ready to name the fix for the second problem specifically — semantic
(embedding-based) retrieval instead of exact-token matching — and to
explain *why* it's a different fix than "add a database" (that only ever
solves durability, never recall quality).

**The cost/complexity spectrum for durable agent memory.** In order:
a local SQLite file ($0, what this project uses — the only real cost is
that its retrieval is exact-keyword, as just discussed); a self-hosted
vector DB (Postgres+pgvector, Chroma) plus an embeddings API (cents —
embedding calls are priced per token and are far cheaper than generation
calls); a managed RAG service (`VertexAiRagMemoryService` — real but
modest cost, needs a GCP project with billing); and a fully managed,
LLM-extracted memory service (`VertexAiMemoryBankService` — highest cost,
since it runs an actual generation call on every memory commit to extract
and deduplicate facts, on top of storage/query pricing). Knowing this
spectrum — and that the last two require a genuinely different Google
product (Vertex AI, with a billed GCP project) than the API-key-based
Google AI Studio access used elsewhere in this project — is the kind of
concrete, leveled answer that distinguishes "I've read about agent memory"
from "I've had to actually pick one for a real budget."
