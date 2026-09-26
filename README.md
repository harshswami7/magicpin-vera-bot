# magicpin AI Challenge — Vera Submission

## Live API

**Base URL:**  https://magicpin-vera-bot-bvyh.onrender.com

### GET Endpoints

- [GET /v1/healthz]( https://magicpin-vera-bot-bvyh.onrender.com/v1/healthz)
- [GET /v1/metadata]( https://magicpin-vera-bot-bvyh.onrender.com/v1/metadata)

### POST Endpoints

- [POST /v1/context]( https://magicpin-vera-bot-bvyh.onrender.com/v1/context)
- [POST /v1/tick]( https://magicpin-vera-bot-bvyh.onrender.com/v1/tick)
- [POST /v1/reply]( https://magicpin-vera-bot-bvyh.onrender.com/v1/reply)

## Approach

This submission uses a deterministic, context-aware rule-based composer. It combines CategoryContext, MerchantContext, TriggerContext, and optional CustomerContext to generate concise WhatsApp messages without inventing facts.

The routing layer specializes messages for research digests, performance changes, customer reminders, milestones, review themes, competitor signals, festivals, renewals, planning intent, and other trigger types.

The HTTP layer maintains state across context updates and conversations, supports versioned context replacement, suppression keys, and the required Vera endpoints. Multi-turn handling explicitly detects canned auto-replies, opt-outs/hostility, clear action intent, and off-topic requests.

## Files

- `bot.py` — submission bot + HTTP server
- `submission.jsonl` — canonical test-pair outputs
