# magicpin AI Challenge — Vera Submission

## Live API

# Magicpin Vera Bot

## Deployed API

Base URL:

https://magicpin-vera-bot-bvyh.onrender.com

> **Important:** The deployed service exposes both GET and POST API endpoints.
> GET endpoints can be opened directly in a browser.
> POST endpoints cannot be tested by simply opening their URL in a browser because
> they require a POST request with a JSON request body. Use Postman, PowerShell,
> curl, or another HTTP client to test the POST endpoints.

## API Endpoints

### GET

#### Health Check

**Method:** GET

**Endpoint:**
`/v1/healthz`

**Full URL:**
https://magicpin-vera-bot-bvyh.onrender.com/v1/healthz

This endpoint can be opened directly in a browser.

---

#### Metadata

**Method:** GET

**Endpoint:**
`/v1/metadata`

**Full URL:**
https://magicpin-vera-bot-bvyh.onrender.com/v1/metadata

This endpoint can be opened directly in a browser.

---

### POST

> **POST endpoints must be called using an HTTP client with
> `Content-Type: application/json` and the required JSON request body.
> Opening these URLs directly in a browser sends a GET request, so it will
> not execute the POST endpoint.**

#### Context

**Method:** POST

**Endpoint:**
`/v1/context`

**Full URL:**
https://magicpin-vera-bot-bvyh.onrender.com/v1/context

Requires a JSON request body.

---

#### Tick

**Method:** POST

**Endpoint:**
`/v1/tick`

**Full URL:**
https://magicpin-vera-bot-bvyh.onrender.com/v1/tick

Requires a JSON request body.

---

#### Reply

**Method:** POST

**Endpoint:**
`/v1/reply`

**Full URL:**
https://magicpin-vera-bot-bvyh.onrender.com/v1/reply

Requires a JSON request body.

---

## Testing

### GET Endpoints

The following endpoints can be tested directly by opening them in a browser:

- Health:
  https://magicpin-vera-bot-bvyh.onrender.com/v1/healthz

- Metadata:
  https://magicpin-vera-bot-bvyh.onrender.com/v1/metadata

### POST Endpoints

The following endpoints require POST requests and JSON bodies:

- Context:
  https://magicpin-vera-bot-bvyh.onrender.com/v1/context

- Tick:
  https://magicpin-vera-bot-bvyh.onrender.com/v1/tick

- Reply:
  https://magicpin-vera-bot-bvyh.onrender.com/v1/reply

For POST requests, use an HTTP client such as PowerShell, curl, or Postman.

Example request structure:


POST <endpoint>
Content-Type: application/json

<JSON request body>

Opening a POST endpoint directly in a browser is not a valid POST test because
the browser performs a GET request.

Approach

This submission uses a deterministic, context-aware rule-based composer. It
combines CategoryContext, MerchantContext, TriggerContext, and optional
CustomerContext to generate concise WhatsApp messages without inventing facts.

The routing layer specializes messages for research digests, performance
changes, customer reminders, milestones, review themes, competitor signals,
festivals, renewals, planning intent, and other trigger types.

The HTTP layer maintains state across context updates and conversations,
supports versioned context replacement, suppression keys, and the required Vera
endpoints.

Multi-turn handling explicitly detects canned auto-replies, opt-outs/hostility,
clear action intent, and off-topic requests.

Files
bot.py — submission bot + HTTP server
submission.jsonl — canonical test-pair output
Files
bot.py — submission bot + HTTP server
submission.jsonl — canonical test-pair output
