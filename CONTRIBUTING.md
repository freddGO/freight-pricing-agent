# Contributing

This started as a personal capstone project by **Alfredo Guillen - Solaria
Nexus** for Kaggle's *5-Day Agents* course. It's shared publicly as a
reference implementation, and contributions are welcome.

## Before opening a PR

1. Run the deterministic checks (no API key needed):
   ```bash
   pytest
   ```
2. If you touched `pricing_agent/`, `mcp_server/`, or prompts/guardrails,
   also run the live eval set (needs `GOOGLE_API_KEY`):
   ```bash
   python -m eval.run_evals
   ```
3. Keep the per-folder `README.md` files in sync with any behavioral change
   — they're documentation of *why* the code is structured this way, not
   just what it does, so a change in approach should update the reasoning
   too, not just the code.

## Scope

Issues and PRs are welcome for:
- Bug fixes in the pricing logic, guardrails, or MCP wiring
- Additional eval cases (especially adversarial/red-team ones)
- Clarifications or corrections to the README/interview-study content
- Extending the synthetic pricing model or city catalog

Please open an issue first for larger changes (e.g. swapping in a new
memory backend, adding new tools) so the direction can be agreed on before
you invest the work.

## Style

- No inline comments explaining *what* code does — names should do that.
  Comments are reserved for non-obvious *why*.
- New business logic should stay framework-agnostic and unit-testable
  (see `mcp_server/pricing_data.py` and `pricing_agent/callbacks.py` for
  the pattern: pure functions, thin framework adapters).

## Attribution

Please retain the copyright notice in `LICENSE`
(Alfredo Guillen - Solaria Nexus) in any redistributed copies or forks.
