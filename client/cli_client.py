"""CLI client for the Freight Pricing Agent.

This is the "Day 3: Context Engineering — Sessions & Memory" showcase piece.
It plays the role of the application embedding the agent: it owns the
SessionService (short-term, per-conversation state) and MemoryService
(long-term, cross-session recall), and drives the Runner.

Two modes, each of which can run against either durability backend:
  python -m client.cli_client                  interactive REPL, single session
  python -m client.cli_client --demo           scripted, non-interactive demo that
                                                proves session state AND cross-
                                                session memory both work:
                                                  1. Session #1: ask for a quote,
                                                     then a follow-up that only
                                                     makes sense with session state
                                                     ("make that express instead").
                                                  2. The session is committed to
                                                     long-term memory.
                                                  3. Session #2 (brand new session,
                                                     same user): ask the agent to
                                                     recall the earlier route using
                                                     the `load_memory` tool.

Add --persist to either mode to back sessions/memory with SQLite files
under .data/ instead of process RAM (see client/persistence.py and
client/README.md's "where does the data live" section). With --persist,
memory survives across *separate runs* of this script, not just across
sessions within one run — try:

  python -m client.cli_client --persist          # ask something, then quit
  python -m client.cli_client --persist           # run again: a NEW process,
                                                    # same recall via memory
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import uuid

from google.adk.runners import Runner
from google.genai import types

from client.persistence import DATA_DIR, build_services
from pricing_agent.agent import root_agent
from pricing_agent.config import settings

logger = logging.getLogger("pricing_agent.client")


def _build_runner(persistent: bool) -> tuple[Runner, object, object]:
    session_service, memory_service = build_services(persistent)
    runner = Runner(
        agent=root_agent,
        app_name=settings.app_name,
        session_service=session_service,
        memory_service=memory_service,
    )
    return runner, session_service, memory_service


async def _send(runner: Runner, user_id: str, session_id: str, text: str) -> str:
    message = types.Content(role="user", parts=[types.Part(text=text)])
    final_text = ""
    async for event in runner.run_async(
        user_id=user_id, session_id=session_id, new_message=message
    ):
        if event.content and event.content.parts:
            piece = "".join(p.text or "" for p in event.content.parts)
            if piece and event.is_final_response():
                final_text = piece
    return final_text


async def _commit_to_memory(session_service, memory_service, user_id: str, session_id: str) -> None:
    """Re-fetches the session (to pick up events written during the
    conversation) and commits it to long-term memory. Mirrors the same
    "commit at a checkpoint, not every turn" policy described in
    client/README.md.
    """
    committed_session = await session_service.get_session(
        app_name=settings.app_name, user_id=user_id, session_id=session_id
    )
    await memory_service.add_session_to_memory(committed_session)


async def run_interactive(persistent: bool) -> None:
    runner, session_service, memory_service = _build_runner(persistent)
    user_id = "cli-user"
    session = await session_service.create_session(
        app_name=settings.app_name, user_id=user_id
    )
    if persistent:
        print(f"(persistent mode — SQLite files under {DATA_DIR})")
    print("Freight Pricing Agent — type 'quit' to exit.\n")
    while True:
        try:
            text = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if not text:
            continue
        if text.lower() in {"quit", "exit"}:
            break
        reply = await _send(runner, user_id, session.id, text)
        print(f"agent> {reply}\n")

    # Commit on the way out so a later run (same user_id) can recall this
    # conversation via long-term memory — see _commit_to_memory's docstring.
    await _commit_to_memory(session_service, memory_service, user_id, session.id)


async def run_demo(persistent: bool) -> None:
    runner, session_service, memory_service = _build_runner(persistent)
    user_id = "demo-user"
    if persistent:
        print(f"(persistent mode — SQLite files under {DATA_DIR})\n")

    print("=" * 70)
    print("SESSION #1 — short-term state (does the agent remember the turn?)")
    print("=" * 70)
    session_1 = await session_service.create_session(
        app_name=settings.app_name, user_id=user_id, session_id=str(uuid.uuid4())
    )

    turn_1 = "I need to ship 8 cubic meters from Bogota to Santiago."
    print(f"you> {turn_1}")
    reply_1 = await _send(runner, user_id, session_1.id, turn_1)
    print(f"agent> {reply_1}\n")

    turn_2 = "Actually make that express instead of standard, same route and volume."
    print(f"you> {turn_2}")
    reply_2 = await _send(runner, user_id, session_1.id, turn_2)
    print(f"agent> {reply_2}\n")
    print("(^ note the agent didn't need origin/destination/volume repeated — ")
    print(" that came from session state, not from re-parsing the whole chat.)\n")

    # Commit session #1 into long-term memory, then start a brand new session.
    await _commit_to_memory(session_service, memory_service, user_id, session_1.id)

    print("=" * 70)
    print("SESSION #2 — brand new session, same user: cross-session MEMORY")
    print("=" * 70)
    session_2 = await session_service.create_session(
        app_name=settings.app_name, user_id=user_id, session_id=str(uuid.uuid4())
    )
    turn_3 = (
        "Quick check: what route and volume did I quote before? "
        "Look it up instead of asking me again."
    )
    print(f"you> {turn_3}")
    reply_3 = await _send(runner, user_id, session_2.id, turn_3)
    print(f"agent> {reply_3}\n")
    print("(^ this is a NEW session with empty state — any correct recall here")
    print(" came from the long-term MemoryService via the `load_memory` tool.)")


def main() -> None:
    parser = argparse.ArgumentParser(description="Freight Pricing Agent CLI client")
    parser.add_argument(
        "--demo", action="store_true", help="Run the scripted sessions+memory demo"
    )
    parser.add_argument(
        "--persist",
        action="store_true",
        help="Back sessions/memory with SQLite files under .data/ instead of "
        "process RAM, so memory survives across separate runs of this script.",
    )
    args = parser.parse_args()
    coro = run_demo(args.persist) if args.demo else run_interactive(args.persist)
    asyncio.run(coro)


if __name__ == "__main__":
    main()
