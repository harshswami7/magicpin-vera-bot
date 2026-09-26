# magicpin AI Challenge — Vera Submission

## Live API

# Magicipin Vera Bot

## Deployed API

Base URL:
https://magicipin-vera-bot-bvyh.onrender.com

## API Endpoints

### GET

#### Health Check
GET /v1/healthz

Full URL:
https://magicipin-vera-bot-bvyh.onrender.com/v1/healthz

#### Metadata
GET /v1/metadata

Full URL:
https://magicipin-vera-bot-bvyh.onrender.com/v1/metadata

### POST

#### Context
POST /v1/context

Full URL:
https://magicipin-vera-bot-bvyh.onrender.com/v1/context

#### Tick
POST /v1/tick

Full URL:
https://magicipin-vera-bot-bvyh.onrender.com/v1/tick

#### Reply
POST /v1/reply

Full URL:
https://magicipin-vera-bot-bvyh.onrender.com/v1/reply
## Approach

This submission uses a deterministic, context-aware rule-based composer. It combines CategoryContext, MerchantContext, TriggerContext, and optional CustomerContext to generate concise WhatsApp messages without inventing facts.

The routing layer specializes messages for research digests, performance changes, customer reminders, milestones, review themes, competitor signals, festivals, renewals, planning intent, and other trigger types.

The HTTP layer maintains state across context updates and conversations, supports versioned context replacement, suppression keys, and the required Vera endpoints. Multi-turn handling explicitly detects canned auto-replies, opt-outs/hostility, clear action intent, and off-topic requests.

## Files

- `bot.py` — submission bot + HTTP server
- `submission.jsonl` — canonical test-pair outputs
