# `client/` — Sessions & Memory (Context Engineering)

Day 3 of the capstone. This folder owns the pieces the agent itself never
touches directly: `SessionService` and `MemoryService`. That separation
is the whole lesson.

## Files

| File | Role |
|---|---|
| `cli_client.py` | Owns both services, drives the ADK `Runner`, and (in `--demo` mode) proves session state and cross-session memory both work. |

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
