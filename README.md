# CareCompass MVP

CareCompass is a healthcare navigation assistant built for the IBM SkillsBuild programme.
It does not diagnose. A user describes a worry in chat; the app checks safety, finds and
compares nearby services from a demo directory, and gives one clear next step.

> All data in this repository is synthetic demo data. Nothing here is medical advice.

## Status
Week 5: project set up, core flow defined in `docs/SPEC.md`.

## Quick start (after the code is built)
```bash
python -m venv .venv
# Windows: .venv\Scripts\activate     Mac/Linux: source .venv/bin/activate
pip install -r requirements.txt
cp .env.example .env     # optional: add watsonx.ai values
streamlit run app.py
pytest
```

## Folders
- `docs/` spec and documentation
- `data/` synthetic demo data (services, red-flag rules, messages, test scenarios)
- `src/` application modules
- `tests/` automated tests
- `logs/` audit log output (not committed)
- `.bob/rules/` project rules for IBM Bob
