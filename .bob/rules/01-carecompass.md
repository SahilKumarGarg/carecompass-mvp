# CareCompass project rules

- Safety logic (red-flag rules, urgency tiers, emergency messages, ranking
  formula) must be plain Python code that reads JSON config. Never implement
  it only inside a prompt.
- The emergency path must work with no LLM call and no network.
- Never invent facilities, phone numbers or opening hours. Every service fact
  must come from data/services.json.
- The app must not diagnose, name likely conditions, or suggest medicines or
  treatments. Keep a banned-phrases list and test it.
- Use synthetic data only. Label demo data clearly. No real personal data.
- Secrets live in a .env file and are never committed.
- Keep modules small (under about 200 lines), use type hints and short docstrings.
- Every function gets at least one test in tests/.
- After each change, run pytest and report the result before continuing.
- Do not add new libraries without asking me first.
