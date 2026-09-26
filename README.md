# magicpin AI Challenge — Vera Submission

## Approach
This submission uses a deterministic, context-aware rule-based composer. It combines CategoryContext, MerchantContext, TriggerContext, and optional CustomerContext to generate concise WhatsApp messages without inventing facts.

The routing layer specializes messages for research digests, performance changes, customer reminders, milestones, review themes, competitor signals, festivals, renewals, planning intent, and other trigger types. Customer-facing messages are attributed with `merchant_on_behalf`.

The HTTP layer maintains state across context updates and conversations, supports versioned context replacement, suppression keys, and the required Vera endpoints. Multi-turn handling explicitly detects canned auto-replies, opt-outs/hostility, clear action intent, and off-topic requests.

## Tradeoffs
A deterministic approach was chosen for predictable latency and reproducibility. It avoids external API dependencies and therefore cannot generate novel language as flexibly as a frontier LLM, but it is robust to repeated judge runs and post-submission context injection.

## Additional context that would help
Historical conversation outcomes and more detailed category-specific voice examples would allow finer personalization, especially for less common trigger types.

## Files
- `bot.py` — submission bot + HTTP server
- `submission.jsonl` — 30 canonical test-pair outputs
