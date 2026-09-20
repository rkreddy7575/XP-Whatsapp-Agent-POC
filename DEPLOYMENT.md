# Production Cloud Deployment Guide

This guide details the steps to deploy the **WhatsApp B2B Corporate Gifting Sales Agent** and **Owner Dashboard** to a cloud environment (e.g. AWS EC2, DigitalOcean Droplet, GCP Compute Engine, or a private Linux VPS).

---

## 1. System Architecture Overview

The system consists of two primary services:
1. **FastAPI Backend (Port 8000)**:
   - Webhook ingress for Meta WhatsApp Cloud API (`GET /webhook` handshake, `POST /webhook` events).
   - WhatsApp messaging pipeline & AI router (Google Gemini + Catalogue + Deterministic Pricing Engine).
   - Owner Dashboard REST API (`/api/auth/*`, `/api/orders*`, `/api/conversations/enquiries`).
   - SQLite persistence with Write-Ahead Logging (WAL) and busy timeout.
2. **React Owner Dashboard (Port 80 / Nginx or Static CDN)**:
   - Single-Page Application (SPA) with secure credential authentication.
   - Real-time order monitoring, status transitions, search, and enquiry visibility.

---

## 2. Environment Variables Configuration

Create a `.env` file inside the `backend/` directory based on `backend/.env.example`.

### Required Variables

| Variable | Type | Description |
| :--- | :--- | :--- |
| `GEMINI_API_KEY` | Secret | Google Gemini API key for natural language understanding and catalog routing. |
| `GEMINI_MODEL` | Config | Gemini model identifier (Recommended: `gemini-3.1-flash-lite`). |
| `WHATSAPP_ACCESS_TOKEN` | Secret | System User Permanent Access Token generated in Meta Business Manager. |
| `WHATSAPP_PHONE_NUMBER_ID` | Config | Phone Number ID from Meta WhatsApp Developer Dashboard. |
| `WHATSAPP_BUSINESS_ACCOUNT_ID` | Config | WhatsApp Business Account (WABA) ID. |
| `WHATSAPP_VERIFY_TOKEN` | Secret | Secret token chosen by you that must **strictly match** the token entered in Meta Webhook config. |
| `WHATSAPP_APP_SECRET` | Secret | **Real Meta App Secret** from Meta App Settings > Basic. Used to verify `X-Hub-Signature-256` HMAC signatures on inbound webhooks. |
| `META_GRAPH_API_VERSION` | Config | Meta Graph API version (e.g. `v21.0` or `v20.0`). |
| `DASHBOARD_USERNAME` | Config | Username for Owner Dashboard login (default: `admin`). |
| `DASHBOARD_PASSWORD` | Secret | Strong password for Owner Dashboard access. |
| `DASHBOARD_API_KEY` | Secret | Cryptographic bearer token / API key used to secure `/api/*` endpoints. |

### Optional Variables

| Variable | Default | Description |
| :--- | :--- | :--- |
| `CORS_ORIGINS` | `http://localhost:5173,http://127.0.0.1:5173` | Comma-separated list of allowed frontend domains (e.g., `https://dashboard.yourdomain.com`). |
| `DATABASE_PATH` | `/app/data/app.db` | Absolute path to SQLite database. |

> [!CAUTION]
> - **Never commit `.env` to Git.**
> - `WHATSAPP_APP_SECRET` **must** be the genuine App Secret from Meta's dashboard. A placeholder or random key will cause Meta webhook signature verification to reject all incoming customer messages with HTTP 403.
> - `WHATSAPP_VERIFY_TOKEN` must be identical to what you enter in Meta's Webhook configuration screen.

---

## 3. Production Deployment Methods

### Option A: Docker Compose (Recommended)

Docker Compose containerizes both the FastAPI backend and Nginx-proxied React dashboard with health checks and volume persistence.

#### Prerequisites
- Docker Engine 24+ & Docker Compose v2+
- Public domain or static IP with HTTPS (e.g., via Caddy, Nginx reverse proxy, or Cloudflare)

#### Steps
1. Clone the repository to the cloud server:
   ```bash
   git clone <repository_url>
   cd Whatsapp-Agent-POC
   ```
2. Populate the production environment file:
   ```bash
   cp backend/.env.example backend/.env
   nano backend/.env
   ```
3. Build and launch containers:
   ```bash
   docker compose up -d --build
   ```
4. Verify running services:
   ```bash
   docker compose ps
   ```
5. Check backend logs:
   ```bash
   docker compose logs -f backend
   ```

---

### Option B: Bare-Metal / Direct Linux VM (systemd)

#### Backend Setup
1. Install Python 3.12 and create a virtual environment:
   ```bash
   cd /opt/whatsapp-agent/backend
   python3.12 -m venv .venv
   source .venv/bin/activate
   pip install --upgrade pip
   pip install -r requirements.txt
   ```
2. Configure environment file at `/opt/whatsapp-agent/backend/.env`.
3. Create systemd service `/etc/systemd/system/whatsapp-agent.service`:
   ```ini
   [Unit]
   Description=WhatsApp B2B Sales Agent FastAPI Backend
   After=network.target

   [Service]
   Type=simple
   User=www-data
   Group=www-data
   WorkingDirectory=/opt/whatsapp-agent/backend
   EnvironmentFile=/opt/whatsapp-agent/backend/.env
   ExecStart=/opt/whatsapp-agent/backend/.venv/bin/uvicorn main:app --host 0.0.0.0 --port 8000 --workers 1
   Restart=always
   RestartSec=5

   [Install]
   WantedBy=multi-user.target
   ```
4. Enable and start:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable --now whatsapp-agent.service
   sudo systemctl status whatsapp-agent.service
   ```

#### Frontend Setup
1. Build static production assets:
   ```bash
   cd /opt/whatsapp-agent/frontend
   npm ci
   VITE_API_BASE=/api npm run build
   ```
2. Copy `dist/` to web server directory (e.g., `/var/www/html/dashboard`).
3. Serve with Nginx or Caddy proxying `/api/` to `http://127.0.0.1:8000/api/`.

---

## 4. Meta Webhook Configuration Steps

1. **Prerequisite**: Your backend must be accessible over public HTTPS (Meta requires valid SSL/TLS certificates).
   - Cloud VM URL: `https://api.yourdomain.com/webhook`
2. **Access Meta Developer Dashboard**:
   - Go to [developers.facebook.com](https://developers.facebook.com) > Select your App.
   - Under **WhatsApp**, select **Configuration**.
3. **Configure Webhook Callback**:
   - Click **Edit** in the Webhook section.
   - **Callback URL**: `https://api.yourdomain.com/webhook`
   - **Verify Token**: Enter the exact string stored in `WHATSAPP_VERIFY_TOKEN`.
   - Click **Verify and Save**.
   - *Meta will send a `GET /webhook` request with `hub.challenge`. The backend verifies the token and responds with the raw challenge string.*
4. **Subscribe to Webhook Fields**:
   - Under Webhook fields, subscribe to:
     - `messages` (Mandatory: incoming text, interactive selections)
   - Ensure the permission `whatsapp_business_messaging` is active.
5. **App Secret Verification**:
   - Ensure `WHATSAPP_APP_SECRET` in `.env` is copied from **App Settings > Basic > App Secret**.
   - The backend automatically validates `X-Hub-Signature-256` HMAC-SHA256 signatures on every incoming message.

---

## 5. Health Check & Monitoring

- **Health Endpoint**:
  ```bash
  curl -i http://localhost:8000/health
  ```
  Expected response:
  ```json
  HTTP/1.1 200 OK
  Content-Type: application/json

  {"status": "ok"}
  ```

---

## 6. Database Persistence & Backup Requirements

### Persistence
The SQLite database resides at:
`backend/data/app.db`

- **Docker Deployments**: The volume `./backend/data:/app/data` is mounted into the container. This ensures `app.db`, WAL logs, and backups persist across container lifecycles.
- **SQLite Configuration**:
  - `PRAGMA journal_mode=WAL;` is enabled automatically.
  - `PRAGMA busy_timeout=5000;` is configured on all connections.
  - Worker concurrency: `--workers 1` ensures zero lock contention while handling asynchronous requests.

### Automated Backups
The project includes an online, non-blocking atomic backup script:
`backend/scripts/backup_db.py`

Run backup manually:
```bash
python backend/scripts/backup_db.py
```
Output:
```text
[BACKUP] Initiating online atomic backup...
[BACKUP] Source:      .../backend/data/app.db
[BACKUP] Destination: .../backend/data/backups/app_backup_20260920_013445.db
[BACKUP] Verification OK (Integrity: ok, Orders: 1, Size: 56.0 KB)
```

#### Recommended Automated Cron Schedule
Schedule an automated backup every 6 hours and retain the latest 15 snapshots:
```bash
# Add to crontab: crontab -e
0 */6 * * * cd /opt/whatsapp-agent && backend/.venv/bin/python backend/scripts/backup_db.py >> /var/log/whatsapp_backup.log 2>&1
```

---

## 7. Security Best Practices

1. **Zero Secret Exposure**:
   - Frontend bundle contains no secrets or credentials.
   - All dashboard API requests (`/api/orders`, `/api/conversations/enquiries`) require Bearer token or API key authentication.
   - Timing-attack safe comparisons (`secrets.compare_digest`) prevent side-channel timing attacks.
2. **Reverse Proxy TLS Termination**:
   - Terminate SSL/TLS at Nginx or Cloudflare.
   - Pass `X-Forwarded-For` and `X-Forwarded-Proto` headers to backend.
3. **Webhook Security**:
   - Meta `X-Hub-Signature-256` cryptographic HMAC-SHA256 signature verification is active for all inbound webhook posts.
