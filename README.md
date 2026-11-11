# TARS

TARS is a voice-first order-capture agent for FMCG field sales reps in India built with Gemini Live.
Reps speak orders in mixed Hindi-English (Hinglish) while TARS resolves outlets, SKUs, schemes, stock, and credit in real time.
Rule breaches route to a sales manager for approval, and orders commit to the ERP only after explicit spoken confirmation.

## Getting started

### Docker Compose (recommended)

```bash
cp .env.example .env
# Fill in TARS_GEMINI_API_KEY in .env (frontend VITE_* vars are baked in at build time)
docker compose up --build
```

Open `http://localhost:5173` for the frontend and `http://localhost:8000/health` for the backend health check.

> **CORS note:** The backend allows `http://localhost:5173` and `http://127.0.0.1:5173`, so no CORS change is needed when serving the Compose frontend on port `5173`. If you change the frontend host port, update CORS in `backend/app/main.py` and record the change in `docs/decisions.md`.

### Backend environment and checks (local dev)

Create the backend virtual environment and install dependencies (run from the repo root):

```powershell
python -m venv backend\.venv
backend\.venv\Scripts\python.exe -m pip install -e "backend[dev]"
```

On macOS/Linux:

```bash
python3 -m venv backend/.venv
backend/.venv/bin/python -m pip install -e "backend[dev]"
```

Run backend tests and strict type checks:

```powershell
backend\.venv\Scripts\python.exe -m pytest backend/tests -v
backend\.venv\Scripts\python.exe -m mypy --strict backend/app/erp
```

