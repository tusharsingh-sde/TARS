# Backend Tests

Automated tests in this directory (`pytest tests -v`) cover configuration, health checks, ERP models, seeding, and repository operations using temporary SQLite databases.

## Manual Live API Smoke Test

The Gemini Live smoke script at `backend/scripts/live_smoke.py` is **not** part of the `pytest` suite because it requires external network access and a paid `TARS_GEMINI_API_KEY`. Run it manually from the repository root (or `backend/`) once `.env` has a valid key:

```powershell
backend\.venv\Scripts\python.exe backend\scripts\live_smoke.py
```

- Exits `0` and prints `chunks`, `first_chunk_size`, and `time_to_first_chunk_ms` on success.
- Exits `2` if `TARS_GEMINI_API_KEY` is missing or empty.
- Exits `1` on a 30-second timeout or connection error.

