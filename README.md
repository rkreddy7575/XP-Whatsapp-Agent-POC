# WhatsApp B2B Corporate Gifting Sales Agent (Phase 1)

A production-oriented FastAPI backend for receiving and replying to WhatsApp messages using the official **Meta WhatsApp Cloud API**.

> **Note**: This is **Phase 1** of the B2B Corporate Gifting Sales Agent. It establishes the core communication bridge (webhook verification, payload parsing, logging, and outbound messaging service). AI logic, catalog, pricing engine, inventory, and database integrations are reserved for subsequent phases.

---

## Architecture Flow

```
WhatsApp Customer
       │
       ▼
Meta WhatsApp Cloud API
       │  (HTTPS POST /webhook)
       ▼
FastAPI Backend (Webhook Endpoint)
       │
       ▼
Application Parsing & Logging
       │
       ▼  (HTTPS POST Graph API)
Meta WhatsApp Cloud API
       │
       ▼
WhatsApp Customer Reply
```

---

## Prerequisites

- **Python 3.11+** installed
- A **Meta Developer Account** ([developers.facebook.com](https://developers.facebook.com/))
- A registered **Meta WhatsApp Business App** with access to the WhatsApp Cloud API test number

---

## Project Structure

```
Whatsapp-Agent-POC/
├── backend/
│   ├── services/
│   │   ├── __init__.py
│   │   └── whatsapp_service.py   # Meta Graph API outbound messaging service
│   ├── .env.example              # Environment variables template
│   ├── .gitignore                # Git ignore rules
│   ├── main.py                   # FastAPI server & Meta webhook endpoints
│   └── requirements.txt          # Python dependencies
├── .gitignore
└── README.md
```

---

## Step-by-Step Setup Guide

### 1. Create a Python Virtual Environment

Navigate to the `backend` directory:

```bash
cd backend
```

Create a virtual environment:

- **Windows (PowerShell):**
  ```powershell
  python -m venv .venv
  .venv\Scripts\Activate.ps1
  ```
- **macOS / Linux:**
  ```bash
  python3 -m venv .venv
  source .venv/bin/activate
  ```

---

### 2. Install Dependencies

With the virtual environment activated, run:

```bash
pip install --upgrade pip
pip install -r requirements.txt
```

---

### 3. Configure Environment Variables

Create your local `.env` file by copying `.env.example`:

- **Windows (PowerShell):**
  ```powershell
  Copy-Item .env.example .env
  ```
- **macOS / Linux:**
  ```bash
  cp .env.example .env
  ```

Open `.env` in your editor and fill in your Meta Cloud API details:

```env
# Custom secret token you choose for Meta webhook verification handshake
WHATSAPP_VERIFY_TOKEN=your_custom_secure_verify_token_here

# System User or Temporary Access Token from Meta Developer Portal
WHATSAPP_ACCESS_TOKEN=EAAB...your_access_token_here

# Phone Number ID from WhatsApp > API Setup in Meta Developer Dashboard
WHATSAPP_PHONE_NUMBER_ID=123456789012345

# WhatsApp Business Account ID from Meta Developer Dashboard
WHATSAPP_BUSINESS_ACCOUNT_ID=987654321098765

# Graph API Version (e.g., v19.0 or v20.0)
META_GRAPH_API_VERSION=v19.0
```

> ⚠️ **Security Warning**: Never commit `.env` or hardcode tokens in git.

---

### 4. Start the FastAPI Server

Start the Uvicorn development server:

```bash
uvicorn main:app --reload --port 8000
```

You should see logs indicating the server is running on `http://127.0.0.1:8000`.

---

### 5. Test the Health Endpoint

Verify that the server is alive and responding:

```bash
curl http://127.0.0.1:8000/health
```

Expected response:
```json
{
  "status": "ok"
}
```

Interactive API documentation is also available at:
`http://127.0.0.1:8000/docs`

---

### 6. Expose Localhost to the Internet

Meta Cloud API requires an HTTPS URL to deliver webhooks. You can use either **ngrok** or **Cloudflare Tunnel**.

#### Option A: Using ngrok
```bash
ngrok http 8000
```
Copy the forwarding HTTPS URL (e.g. `https://abcd-1234.ngrok-free.app`).

#### Option B: Using Cloudflare Tunnel
```bash
cloudflared tunnel --url http://localhost:8000
```
Copy the generated `https://*.trycloudflare.com` URL.

---

### 7. Configure Meta Webhook in Meta App Dashboard

1. Go to the [Meta for Developers Console](https://developers.facebook.com/).
2. Select your App and navigate to **WhatsApp** > **Configuration** in the left sidebar.
3. In the **Webhook** section, click **Edit**:
   - **Callback URL**: Enter your public HTTPS URL followed by `/webhook` (e.g., `https://abcd-1234.ngrok-free.app/webhook`).
   - **Verify Token**: Enter the exact same string you set for `WHATSAPP_VERIFY_TOKEN` in your `.env` file.
4. Click **Verify and Save**.
   - Meta will send a `GET` request with `hub.mode`, `hub.verify_token`, and `hub.challenge`.
   - The FastAPI backend validates the token and returns `hub.challenge` with HTTP 200.
   - If configured correctly, Meta will display a green checkmark.

---

### 8. Subscribe to Webhook Fields

In the same **Webhook** section under WhatsApp Configuration:
1. Locate the **Webhook fields** table.
2. Find the **`messages`** field row.
3. Click **Subscribe**.

This ensures incoming WhatsApp messages and delivery statuses are routed to your `/webhook` endpoint.

---

### 9. Test by Sending a Message

1. In the Meta Developer Portal, go to **WhatsApp** > **API Setup**.
2. Under **Step 1: Select phone numbers**, locate the test phone number provided by Meta.
3. Add your personal WhatsApp phone number as a **Recipient Phone Number** and verify it via SMS/WhatsApp code.
4. From your personal phone, send a message (e.g., `"Hi"`) to the Meta test number.
5. Watch your terminal logs in the FastAPI backend:
   - You will see the incoming JSON payload logged safely.
   - You will see extracted information:
     ```text
     [INFO] whatsapp_agent: Incoming Meta Webhook Payload: { ... }
     [INFO] whatsapp_agent: Received text message from [1XXXXXXXXXX] (ID: wamid.HBgL...): 'Hi'
     ```
   - The endpoint immediately responds with HTTP 200 (`{"status": "ok"}`).

---

### 10. Sending a Reply via WhatsApp Service (Outbound)

To send an outbound message, import `send_text_message` from `services.whatsapp_service`:

```python
from services.whatsapp_service import send_text_message

# Async call:
response = await send_text_message(
    to="16315551181",  # Recipient phone number in E.164 without '+'
    message="Hello! Welcome to our Corporate Gifting concierge. How may we assist your company today?"
)
```

---

## Phase 1 Verification Checklist

| Requirement | Implementation Status |
| :--- | :--- |
| Python 3.11+ / FastAPI / Uvicorn | Configured in `backend/requirements.txt` & `main.py` |
| Webhook Verification (`GET /webhook`) | Fully validated with `hub.mode`, `hub.verify_token`, and `hub.challenge` |
| Webhook Payload Handler (`POST /webhook`) | Safely logs payloads, extracts sender & text, returns HTTP 200 |
| Non-message event resilience | Handles delivery statuses and other event types without crashing |
| WhatsApp Service (`whatsapp_service.py`) | Async `send_text_message` using official Meta Graph API & env variables |
| Health Check (`GET /health`) | Returns `{"status": "ok"}` with HTTP 200 |
| Secrets Protection | Kept in `.env`, `.gitignore` configured, no token exposure in logs |
| Complete README | Detailed environment setup, tunneling, and Meta verification instructions |
