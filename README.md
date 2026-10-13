# Event Manager Pro

FastAPI event planner with OpenRouter AI, user registration/login, JWT authentication, SQLite event persistence, event history, venue lookup integration, and safer error handling.

## Quick start

1. Install Python 3.10+.
2. Create and activate a virtual environment:
   - Windows: `py -m venv .venv` then `.venv\Scripts\activate`
   - macOS/Linux: `python3 -m venv .venv` then `source .venv/bin/activate`
3. Install packages: `pip install -r requirements.txt`
4. Copy `.env.example` to `.env`.
5. Put your real OpenRouter key in `.env` as `OPENROUTER_API_KEY=...`.
6. Generate a secret with `python -c "import secrets; print(secrets.token_urlsafe(48))"` and set it as `JWT_SECRET`.
7. Start: `uvicorn main:app --reload`
8. Visit `http://127.0.0.1:8000/docs` and `http://127.0.0.1:8000/health`.

## Auth flow

- Register: `POST /api/auth/register` with JSON `{"email":"you@example.com","password":"at-least-10-characters"}`
- Login: `POST /api/auth/token` using form fields `username` (your email) and `password`. The response contains a bearer token.
- Use `Authorization: Bearer YOUR_TOKEN` on protected endpoints.
- Swagger UI: click **Authorize** and enter your email/password.

## Main endpoints

- `GET /health` — health and safe AI configuration status
- `POST /api/auth/register` — create account
- `POST /api/auth/token` — login
- `GET /api/auth/me` — current account
- `POST /api/create-event` — generate plan and save event
- `GET /api/events` — list your saved events
- `GET /api/events/{id}` — read one of your events
- `DELETE /api/events/{id}` — delete one of your events
- `POST /api/chat` — event assistant
- `POST /api/recommend-venues` — venue guidance plus optional live Google Places listings
- `GET /docs` — interactive API docs

Legacy paths (`/create-event`, `/chat`, `/recommend-venues`) are kept as aliases but now require authentication too.

## Real venue search

Set `GOOGLE_PLACES_API_KEY` after enabling Google Places API (New) and billing in Google Cloud. Without it, the endpoint returns AI venue guidance only and an empty `live_places` array. This does not book venues or verify pricing/availability.

## Deployment notes

- Set `OPENROUTER_API_KEY`, `JWT_SECRET`, `DATABASE_URL`, `FRONTEND_ORIGINS`, and optionally `GOOGLE_PLACES_API_KEY` in your hosting provider's environment settings.
- Never commit `.env` or expose keys in frontend code.
- Set `FRONTEND_ORIGINS` to exact trusted production origins; do not use `*` with credentials.
- SQLite is suitable for local/small single-instance use. For production/multiple instances, use managed PostgreSQL and set `DATABASE_URL` to its connection URL.
- Use HTTPS in production. Add rate limiting, email verification, password reset, migrations (Alembic), backups, and monitoring before public launch.
- `openrouter/free` can vary in availability and model capabilities; set `OPENROUTER_MODEL` to a model available to your OpenRouter account if needed.


## If you see "OPENROUTER_API_KEY environment variable is missing"

Make sure `.env` is in the **same project directory as `main.py`**, not inside `frontend/`.

Example:

```dotenv
OPENROUTER_API_KEY=sk-or-v1-your-real-key
JWT_SECRET=your-long-random-secret
```

Then completely stop and restart Uvicorn:

```bash
# stop with Ctrl+C
uvicorn main:app --reload
```

Check:

```text
http://127.0.0.1:8000/health
```

If configured correctly, it should include:

```json
{"status":"ok","ai_configured":true}
```

Never put `OPENROUTER_API_KEY` in `frontend/index.html`.

If you still get `ai_configured:false`, check:
1. The file is literally named `.env` (not `.env.txt`).
2. The variable is literally `OPENROUTER_API_KEY`.
3. There are no accidental quotes/spaces around the variable name.
4. You restarted the Python process after creating/changing `.env`.
5. You are running `uvicorn main:app --reload` from the project containing `main.py`.
