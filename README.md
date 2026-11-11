# TARS

TARS is a voice-first order-capture agent for FMCG field sales reps in India built with Gemini Live.
Reps speak orders in mixed Hindi-English (Hinglish) while TARS resolves outlets, SKUs, schemes, stock, and credit in real time.
Rule breaches route to a sales manager for approval, and orders commit to the ERP only after explicit spoken confirmation.

## Getting started

### Backend environment and checks (Phase 1)

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

*Frontend and Docker Compose run commands will be added once P0-03 and P0-04 are complete.*

