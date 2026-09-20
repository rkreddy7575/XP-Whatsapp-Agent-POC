# Implementation Plan: Cloud Deployment Preparation

Prepares the WhatsApp Corporate Gifting sales platform and Owner Dashboard for a controlled single-tenant cloud deployment without modifying any business logic or existing sales flows.

---

## 1. Production Configuration & Environment Audit

### Required Environment Variables:
- **Google Gemini**:
  - `GEMINI_API_KEY`: Google AI Studio API key for real-time conversational NLU.
  - `GEMINI_MODEL`: Model identifier (default: `gemini-3.1-flash-lite`).
- **Meta WhatsApp Cloud API**:
  - `WHATSAPP_ACCESS_TOKEN`: System User Permanent Access Token with `whatsapp_business_messaging` permissions.
  - `WHATSAPP_PHONE_NUMBER_ID`: WhatsApp Business Phone Number ID from Meta App Dashboard.
  - `WHATSAPP_BUSINESS_ACCOUNT_ID`: WhatsApp Business Account (WABA) ID.
  - `WHATSAPP_VERIFY_TOKEN`: Custom secret string configured in Meta Webhooks Dashboard for GET challenge verification.
  - `WHATSAPP_APP_SECRET`: **Real Meta App Secret** from App Settings > Basic in Meta Developer Portal (used to compute HMAC-SHA256 and validate incoming webhook `X-Hub-Signature-256` headers).
  - `META_GRAPH_API_VERSION`: Graph API version (e.g. `v22.0`).
- **Owner Dashboard Authentication**:
  - `DASHBOARD_USERNAME`: Dashboard login username (`admin`).
  - `DASHBOARD_PASSWORD`: High-entropy dashboard login password.
  - `DASHBOARD_API_KEY`: 32-byte secret hex key for Bearer token and API authentication.
- **Production Server & Networking**:
  - `CORS_ORIGINS`: Allowed web origins (e.g. `https://dashboard.mudhra.com,http://localhost:5173`).

---

## 2. Production Server & Dependencies

- Update `backend/requirements.txt` to include all runtime dependencies:
  - `fastapi>=0.110.0`
  - `uvicorn[standard]>=0.28.0`
  - `python-dotenv>=1.0.1`
  - `httpx>=0.27.0`
  - `pydantic>=2.6.0`
  - `google-genai>=2.24.0`
  - `openpyxl>=3.1.5`
- Enhance CORS handling in `backend/main.py` to read `CORS_ORIGINS` from environment.
- Confirm production uvicorn launch command:
  `uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1`

---

## 3. Frontend Production Build & API Base URL

- Verify `frontend/src/api.ts` dynamic base URL handling (`import.meta.env.VITE_API_BASE`).
- Create `frontend/.env.example` documenting relative and absolute base URLs.
- Verify `npm run build` succeeds without errors.
- Ensure no secrets are embedded in the client build.

---

## 4. Single-Tenant Database & Persistence

- Retain SQLite architecture for the single-tenant pilot:
  - Production database: `backend/data/app.db`
  - WAL journal mode (`PRAGMA journal_mode=WAL;`)
  - Busy timeout (`PRAGMA busy_timeout=5000;`)
  - Automated backups: `backend/scripts/backup_db.py`
  - Strict test database isolation: verified tests never touch `backend/data/app.db`.
- Specify volume mount requirement: `backend/data` directory must be mounted to persistent storage.

---

## 5. Container & Cloud Deployment Artifacts

- Create `backend/Dockerfile` for production containerization.
- Create `frontend/Dockerfile` + `frontend/nginx.conf` for serving compiled SPA.
- Create `docker-compose.yml` for single-tenant pilot orchestration.
- Create `DEPLOYMENT.md` detailing step-by-step instructions for Meta webhook registration, domain routing, volume persistence, and backups.

---

## 6. Verification Plan

1. Run complete backend test suite (159/159 tests).
2. Run frontend production build (`npm run build`).
3. Verify live `/health`, `/webhook` (GET & POST), and `/api/*` endpoints.
4. Verify database persistence and isolation.
