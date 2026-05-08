# AgentKit Studio UI

A clean, local-first frontend for the `api.py` AgentKit Studio backend.

## Files

```text
static/
  index.html
  styles.css
  app.js
```

## Install

Copy the `static` folder next to your existing files:

```text
agentkit.py
tools.py
api.py
static/
```

## Run

```bash
pip install fastapi uvicorn pydantic requests python-multipart
uvicorn api:app --reload --host 127.0.0.1 --port 8000
```

Open:

```text
http://127.0.0.1:8000
```

## Backend endpoints used

- `GET /api/tools`
- `POST /api/generate-code`
- `POST /api/run`
- `GET /api/runs/{run_id}/stream`
- `GET /api/outputs`
- `GET /api/outputs/preview`
- `POST /api/automations`
- `PUT /api/automations/{id}`
- `GET /api/templates`

## Notes

This frontend has no Node.js build step. It is production-friendly for classroom/local use because it is just static HTML, CSS, and JavaScript served by FastAPI.
