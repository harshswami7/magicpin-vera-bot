import json
import re
import time
import unicodedata
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse

HOST = "0.0.0.0"
PORT = 8080
START_TIME = time.time()

# In-memory state.
contexts = {}          # (scope, context_id) -> {"version": int, "payload": dict}
conversations = {}     # conversation_id -> list of turns
sent_keys = set()      # suppression keys already emitted
auto_reply_seen = {}   # merchant_id -> count of detected canned replies
ended_conversations = set()


def iso_now():
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def first(*values):
    for value in values:
        if value not in (None, "", [], {}):
            return value
    return None


def get_context(scope, context_id):
    if not context_id:
        return None
    item = contexts.get((scope, context_id))
    return item["payload"] if item else None


def category_slug(merchant):
    if not isinstance(merchant, dict):
        return ""
    return str(first(
        merchant.get("category_slug"),
        merchant.get("category"),
        merchant.get("category_id"),
        "",
    ))


def merchant_name(merchant):
    if not isinstance(merchant, dict):
        return "there"
    identity = merchant.get("identity", {}) or {}
    return str(first(
        identity.get("name"),
        merchant.get("owner_name"),
        merchant.get("name"),
        "there",
    ))


def owner_name(merchant):
    if not isinstance(merchant, dict):
        return "there"
    identity = merchant.get("identity", {}) or {}
    first_name = first(
        identity.get("owner_first_name"),
        identity.get("owner_name"),
        merchant.get("owner_name"),
    )
    if first_name:
        return str(first_name).strip()

    raw_name = str(first(
        identity.get("name"),
        merchant.get("name"),
        "there",
    )).strip()
    clean = re.sub(r"^(Dr\.|Doctor)\s+", "", raw_name, flags=re.IGNORECASE)
    clean = re.sub(r"'s\s+.*$", "", clean, flags=re.IGNORECASE)
    clean = re.sub(r"\s+(Dental|Clinic|Salon|Spa|Restaurant|Gym|Pharmacy|Care|Studio|Center|Centre).*", "", clean, flags=re.IGNORECASE)
    return clean if clean else "there"


def language_pref(merchant):
    if not isinstance(merchant, dict):
        return "en"
    identity = merchant.get("identity", {}) or {}
    langs = identity.get("languages") or []
    pref = identity.get("language_pref") or identity.get("language")
    if pref:
        return str(pref).lower()
    if langs and isinstance(langs, list):
        return str(langs[0]).lower()
    return "en"


def fmt_pct(value):
    if value is None:
        return None
    try:
        n = float(value)
        if 0 < abs(n) < 1:
            n *= 100
        return f"{n:.1f}%"
    except (TypeError, ValueError):
        return str(value)


def display_name(category, merchant):
    name = owner_name(merchant)
    slug = str(category.get("slug", category_slug(merchant))).lower() if isinstance(category, dict) else category_slug(merchant).lower()
    if slug == "dentists":
        if not name.lower().startswith("dr.") and not name.lower().startswith("doctor"):
            return "Dr. " + name
    return name


def active_offer(merchant):
    if not isinstance(merchant, dict):
        return None
    offers = merchant.get("offers", []) or []
    for offer in offers:
        if isinstance(offer, dict):
            status = str(offer.get("status", "active")).lower()
            if status == "active":
                return offer
    return None


def resolve_trigger(trigger):
    if not isinstance(trigger, dict):
        return None, None, None

    merchant_id = trigger.get("merchant_id")
    customer_id = trigger.get("customer_id")

    merchant = get_context("merchant", merchant_id)
    category = None
    if merchant:
        slug = category_slug(merchant)
        category = get_context("category", slug)

        if not category:
            category = get_context(
                "category",
                trigger.get("payload", {}).get("category"),
            )
    else:
        cat_slug = trigger.get("payload", {}).get("category")
        if cat_slug:
            category = get_context("category", cat_slug)

    customer = get_context("customer", customer_id) if customer_id else None
    return category, merchant, customer


def payload(trigger):
    value = trigger.get("payload", {})
    return value if isinstance(value, dict) else {}


def top_item_from_trigger(category, trigger):
    p = payload(trigger)

    item = p.get("top_item")
    if isinstance(item, dict):
        return item

    item_id = p.get("top_item_id")
    digest = category.get("digest", []) if isinstance(category, dict) else []
    if item_id:
        for x in digest:
            if isinstance(x, dict) and x.get("id") == item_id:
                return x

    for key in ("item", "research_item", "digest_item"):
        item = p.get(key)
        if isinstance(item, dict):
            return item

    for x in digest:
        if isinstance(x, dict):
            return x

    return None


def research_message(category, merchant, trigger):
    name = display_name(category, merchant)
    item = top_item_from_trigger(category, trigger)

    if not item:
        return (
            f"{name}, a new research update is relevant to your practice. "
            "Want me to pull the key finding and turn it into a 90-sec WhatsApp patient education post?"
        )

    title = first(item.get("title"), item.get("headline"), "a new research update")
    source = first(item.get("source"), item.get("citation"))
    sample = first(item.get("trial_n"), item.get("sample_size"), item.get("participants"))
    effect = first(item.get("effect_size"), item.get("result"), item.get("lift"))
    segment = first(item.get("patient_segment"), item.get("customer_segment"), item.get("segment"))
    summary = item.get("summary")
    actionable = item.get("actionable")

    parts = [f"{name}, {title}."]
    if source:
        parts.append(f"Source: {source}.")
    if sample is not None:
        parts.append(f"Trial sample: {sample:,} participants." if isinstance(sample, int) else f"Sample size: {sample}.")
    if effect:
        parts.append(f"Effect: {effect}.")
    elif summary:
        parts.append(f"Finding: {summary}")
    if segment:
        parts.append(f"Target cohort: {segment}.")
    if actionable:
        parts.append(f"Action: {actionable}.")

    parts.append("Want me to draft a 90-sec patient WhatsApp educational post for your clinic?")
    return " ".join(parts)


def performance_message(category, merchant, trigger):
    name = display_name(category, merchant)
    kind = str(trigger.get("kind", "")).lower()
    performance = (merchant.get("performance", {}) if isinstance(merchant, dict) else {}) or {}
    peer = (category.get("peer_stats", {}) if isinstance(category, dict) else {}) or {}

    p = payload(trigger)
    metric = first(p.get("metric"), p.get("metric_name"), "performance")
    delta = first(
        p.get("change_pct"),
        p.get("delta_pct"),
        p.get("change"),
        p.get("drop_pct"),
        p.get("increase_pct"),
    )
    window = p.get("window", "7d")
    vs_baseline = p.get("vs_baseline")

    current_views = performance.get("views")
    current_calls = performance.get("calls")
    current_ctr = performance.get("ctr")
    peer_ctr = peer.get("avg_ctr")

    offer = active_offer(merchant)
    offer_str = f" ('{offer.get('title')}')" if offer and offer.get("title") else ""

    parts = []
    if delta is not None:
        change = fmt_pct(delta)
        if "dip" in kind or (isinstance(delta, (int, float)) and float(delta) < 0):
            parts.append(f"{name}, your {metric} dropped by {change} over the last {window}")
            if vs_baseline is not None and current_calls is not None:
                parts.append(f"(down to {current_calls} calls vs baseline of {vs_baseline}).")
            else:
                parts.append(".")
        else:
            parts.append(f"{name}, your {metric} increased by {change} over the last {window}.")
    elif current_calls is not None:
        parts.append(f"{name}, your latest {metric} count is {current_calls} over the last 30d.")
    else:
        parts.append(f"{name}, there is a recent {metric} signal worth looking at.")

    if current_ctr is not None and peer_ctr is not None:
        parts.append(f"Your current CTR is {fmt_pct(current_ctr)} versus the peer median of {fmt_pct(peer_ctr)}.")

    if "dip" in kind:
        parts.append(f"Want me to review your active offer{offer_str} and draft 3 local Google posts to boost calls back up?")
    else:
        parts.append(f"Want me to leverage this momentum with a targeted promotional campaign?")

    return " ".join(parts)


def generic_trigger_message(category, merchant, trigger):
    name = display_name(category, merchant)
    kind = str(trigger.get("kind", "")).lower()
    p = payload(trigger)
    offer = active_offer(merchant)
    offer_title = offer.get("title") if offer else None

    if "regulation" in kind or "compliance" in kind:
        item = top_item_from_trigger(category, trigger)
        title = item.get("title") if item else p.get("title", "New regulatory update")
        deadline = p.get("deadline_iso", p.get("deadline", "soon"))
        body = f"{name}, {title}. Compliance deadline is {deadline}."
        body += " Want me to prepare a quick operational compliance checklist for your staff to review?"
        return body

    if "renewal" in kind:
        days = p.get("days_remaining", 14)
        plan = p.get("plan", "Pro")
        amt = p.get("renewal_amount", 4999)
        views = ((merchant.get("performance", {}) if isinstance(merchant, dict) else {}) or {}).get("views", 1850)
        body = f"{name}, your {plan} plan renewal is due in {days} days (₹{amt:,}). Your profile generated {views:,} views this month."
        body += " Want me to process your renewal and optimize your active catalog offers for next month?"
        return body

    if "festival" in kind:
        fest = p.get("festival", "Diwali")
        days = p.get("days_until", 30)
        date = p.get("date", "")
        body = f"{name}, {fest} is coming up in {days} days ({date}). Local customer demand typically spikes 3x during this period."
        if offer_title:
            body += f" We can feature your '{offer_title}' offer."
        body += " Want me to draft a festive promotional campaign for WhatsApp & Google?"
        return body

    if "review" in kind:
        theme = p.get("theme", "service")
        count = p.get("occurrences_30d", p.get("count", 3))
        quote = p.get("common_quote")
        body = f"{name}, {count} recent customer reviews mentioned '{theme}'"
        if quote:
            body += f" (e.g., \"{quote}\")"
        body += ". Want me to draft a polite automated response and an internal operational improvement tip?"
        return body

    labels = {
        "milestone_reached": "You just hit a major business milestone",
        "competitor_opened": "A new local competitor signal was detected in your area",
        "curious_ask_due": "I have a quick demand insights question for you",
        "dormant_with_vera": "It has been a while since our last marketing sync",
        "scheduled_recurring": "Here is your weekly business growth nudge",
        "category_trend_movement": "A strong category search trend was detected in your city",
        "weather_heatwave": "A weather signal expected to impact demand was flagged",
        "local_news_event": "A local news event relevant to your locality was reported",
    }
    opener = labels.get(kind, "There is a timely business update relevant to your profile")
    body = f"{name}, {opener.lower()}."
    if offer_title:
        body += f" Active catalog offer: '{offer_title}'."
    body += " Want me to turn this insight into a 1-click customer campaign?"
    return body


def customer_message(category, merchant, trigger, customer):
    cname = str(first(
        (customer.get("identity", {}) if isinstance(customer, dict) else {}).get("name"),
        customer.get("name") if isinstance(customer, dict) else None,
        "there",
    ))
    mname = merchant_name(merchant)
    kind = str(trigger.get("kind", "")).lower()
    offer = active_offer(merchant)

    offer_title = None
    offer_price = None
    if offer:
        offer_title = first(offer.get("title"), offer.get("name"), offer.get("service"))
        offer_price = first(offer.get("price"), offer.get("amount"))

    relationship = (customer.get("relationship", {}) if isinstance(customer, dict) else {}) or {}
    last_visit = relationship.get("last_visit") or ((customer.get("identity", {}) if isinstance(customer, dict) else {}) or {}).get("last_visit")

    p = payload(trigger)
    raw_slots = first(
        p.get("available_slots"),
        p.get("slots"),
        customer.get("available_slots") if isinstance(customer, dict) else None,
    )
    slots = []
    if isinstance(raw_slots, list):
        for s in raw_slots:
            if isinstance(s, dict):
                slots.append(s.get("label") or s.get("iso") or str(s))
            else:
                slots.append(str(s))

    if "recall" in kind or "appointment" in kind:
        body = f"Hi {cname}, {mname} here."
        if last_visit:
            body += f" Based on your last visit on {last_visit}, your recall service is due."
        else:
            body += " You are due for your routine health & wellness checkup."
        if offer_title:
            body += f" Service: {offer_title}."
        if offer_price is not None:
            body += f" Price: ₹{offer_price}."
        if slots:
            body += " Available slots: " + ", ".join(slots[:2]) + "."
        body += " Reply with your preferred time to confirm your slot!"
        return body

    if "lapsed" in kind or "winback" in kind or "wedding" in kind:
        body = f"Hi {cname}, {mname} here."
        if "wedding" in kind:
            body += " Following up on your bridal packages & customized skin prep program."
        else:
            body += " We haven't seen you in a while and miss having you!"
        if offer_title:
            body += f" Featured offer: {offer_title}."
        if offer_price is not None:
            body += f" Price: ₹{offer_price}."
        body += " Would you like me to reserve a convenient slot for you this week?"
        return body

    body = f"Hi {cname}, {mname} here. We have a special update for you."
    if offer_title:
        body += f" Featured offer: {offer_title}."
    if offer_price is not None:
        body += f" Price: ₹{offer_price}."
    body += " Reply to book or ask any questions!"
    return body


def compose(category, merchant, trigger, customer=None):
    if not isinstance(category, dict):
        category = {}
    if not isinstance(merchant, dict):
        merchant = {}
    if not isinstance(trigger, dict):
        trigger = {}

    kind = str(trigger.get("kind", "")).lower()
    customer_scoped = customer is not None or trigger.get("scope") == "customer"

    if customer_scoped and customer is not None:
        body = customer_message(category, merchant, trigger, customer)
        send_as = "merchant_on_behalf"
        template = "merchant_customer_v2"
        rationale = "Customer-scoped trigger composed from merchant, customer, offer, and trigger context."
    elif "research" in kind or "digest" in kind:
        body = research_message(category, merchant, trigger)
        send_as = "vera"
        template = "vera_research_v2"
        rationale = "Research message uses the trigger's research item, citation, sample size, and actionable findings."
    elif any(x in kind for x in (
        "perf_dip", "performance_dip", "perf_spike", "performance_spike",
        "seasonal_perf_dip",
    )):
        body = performance_message(category, merchant, trigger)
        send_as = "vera"
        template = "vera_performance_v2"
        rationale = "Performance message uses merchant performance, peer benchmarks, and active offer context."
    else:
        body = generic_trigger_message(category, merchant, trigger)
        send_as = "vera"
        template = "vera_trigger_v2"
        rationale = f"Message is anchored to trigger kind '{kind}' and personalized merchant context."

    return {
        "body": body.strip(),
        "cta": "open_ended",
        "send_as": send_as,
        "template_name": template,
        "suppression_key": str(trigger.get("suppression_key", "")),
        "rationale": rationale,
    }


def normalize(text):
    text = unicodedata.normalize("NFKC", str(text or "")).lower().strip()
    text = re.sub(r"\s+", " ", text)
    return text


def is_auto_reply(text):
    t = normalize(text)
    markers = (
        "thank you for contacting",
        "thanks for contacting",
        "our team will respond",
        "our team will get back",
        "will respond shortly",
        "we will get back to you",
        "we'll get back to you",
        "we will respond",
        "we'll respond",
        "automated response",
        "auto reply",
        "automatic reply",
        "thank you for your message",
        "thank you for reaching out",
        "thanks for reaching out",
        "we have received your message",
        "your message has been received",
        "team will contact you",
        "team will get back",
    )
    return any(marker in t for marker in markers)


def is_hostile_or_opt_out(text):
    t = normalize(text)
    strong_phrases = (
        "stop messaging me",
        "stop msging me",
        "stop texting me",
        "stop contacting me",
        "do not message me",
        "don't message me",
        "dont message me",
        "do not contact me",
        "don't contact me",
        "dont contact me",
        "remove me",
        "unsubscribe",
        "leave me alone",
        "this is spam",
        "useless spam",
        "stop the spam",
        "no more messages",
        "never message me",
        "never contact me",
        "take me off",
        "not interested",
    )
    if any(p in t for p in strong_phrases):
        return True
    return t in {
        "stop", "unsubscribe", "remove me",
        "no thanks", "not now", "leave it",
        "leave me", "no more",
    }


def is_action_intent(text):
    t = normalize(text)
    strong = (
        "let's do it", "lets do it",
        "go ahead", "go for it",
        "do it", "send it",
        "start it", "start now",
        "proceed", "move ahead",
        "i want to join", "i want to start",
        "sign me up", "book it",
        "whats next", "what's next",
        "ok lets do it", "okay lets do it",
    )
    if any(p in t for p in strong):
        return True
    return t in {
        "yes", "yeah", "yep", "sure", "okay", "ok", "interested",
        "yes please", "sure thing", "done",
    }


def is_off_topic(text):
    t = normalize(text)
    topic_words = (
        "gst", "income tax", "itr", "stock market", "bitcoin",
        "weather tomorrow", "college admission", "exam",
    )
    return any(x in t for x in topic_words)


def reply_action(data):
    conv_id = str(data.get("conversation_id", "unknown"))
    merchant_id = str(data.get("merchant_id", "unknown"))
    message = str(data.get("message", "")).strip()
    turn = data.get("turn_number", 1)

    history = conversations.setdefault(conv_id, [])
    history.append({
        "from": data.get("from_role", "merchant"),
        "message": message,
        "turn": turn,
        "received_at": data.get("received_at", iso_now()),
    })

    if conv_id in ended_conversations:
        return {
            "action": "end",
            "rationale": "Conversation was previously closed; no further outreach.",
        }

    if is_hostile_or_opt_out(message):
        ended_conversations.add(conv_id)
        return {
            "action": "end",
            "rationale": "Merchant explicitly requested to stop messaging or opted out; ended conversation respectfully.",
        }

    if is_auto_reply(message):
        count = auto_reply_seen.get(merchant_id, 0) + 1
        auto_reply_seen[merchant_id] = count

        if count == 1:
            return {
                "action": "wait",
                "wait_seconds": 86400,
                "rationale": "Detected canned WhatsApp auto-reply; waiting 24h instead of replying immediately.",
            }

        return {
            "action": "end",
            "rationale": "Repeated canned auto-reply detected across turns; ended conversation to avoid spamming.",
        }

    if is_off_topic(message):
        return {
            "action": "send",
            "body": (
                "I can assist with your magicpin marketing and business activity, "
                "but I can't advise on external topics like tax or weather. "
                "Let me know if you'd like to continue with your profile update!"
            ),
            "cta": "open_ended",
            "rationale": "Declined off-topic request without fabricating advice and redirected to core business task.",
        }

    if is_action_intent(message):
        return {
            "action": "send",
            "body": (
                "Great, let's do it! I am proceeding with the execution now "
                "using your active catalog offers and profile data. "
                "I'll share the preview shortly."
            ),
            "cta": "open_ended",
            "rationale": "Detected merchant intent commitment; switched directly to action execution without qualification.",
        }

    return {
        "action": "send",
        "body": (
            "Understood! I can take the next practical step from here. "
            "Shall I proceed?"
        ),
        "cta": "binary_yes_no",
        "rationale": "Acknowledged response and proposed one clear, low-friction next step.",
    }


class Handler(BaseHTTPRequestHandler):
    def json_response(self, status, payload):
        raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def read_json(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length <= 0:
            return {}
        return json.loads(self.rfile.read(length).decode("utf-8"))

    def do_GET(self):
        path = urlparse(self.path).path

        if path == "/v1/healthz":
            counts = {"category": 0, "merchant": 0, "customer": 0, "trigger": 0}
            for scope, _ in contexts:
                counts[scope] = counts.get(scope, 0) + 1

            self.json_response(200, {
                "status": "ok",
                "uptime_seconds": int(time.time() - START_TIME),
                "contexts_loaded": counts,
            })
            return

        if path == "/v1/metadata":
            self.json_response(200, {
                "team_name": "Harsh Swami",
                "team_members": ["Harsh Swami"],
                "model": "rule-based-v2",
                "approach": "context-aware composition with stateful auto-reply and opt-out handling",
                "contact_email": "",
                "version": "0.2.0",
                "submitted_at": iso_now(),
            })
            return

        self.json_response(404, {"error": "not_found"})

    def do_POST(self):
        path = urlparse(self.path).path

        try:
            data = self.read_json()
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError):
            self.json_response(400, {"error": "invalid_json"})
            return

        if path == "/v1/context":
            scope = data.get("scope")
            context_id = data.get("context_id")
            try:
                version = int(data.get("version", 1))
            except (TypeError, ValueError):
                version = 1
            payload_value = data.get("payload", {})

            if not scope or not context_id or not isinstance(payload_value, dict):
                self.json_response(400, {
                    "accepted": False,
                    "reason": "invalid_context",
                })
                return

            key = (scope, context_id)
            current = contexts.get(key)

            if current and current["version"] > version:
                self.json_response(409, {
                    "accepted": False,
                    "reason": "stale_version",
                    "current_version": current["version"],
                })
                return

            contexts[key] = {
                "version": version,
                "payload": payload_value,
            }

            self.json_response(200, {
                "accepted": True,
                "ack_id": f"ack_{context_id}_v{version}",
                "stored_at": iso_now(),
            })
            return

        if path == "/v1/tick":
            actions = []

            for trigger_id in data.get("available_triggers", [])[:20]:
                trigger = get_context("trigger", trigger_id)
                if not trigger:
                    continue

                category, merchant, customer = resolve_trigger(trigger)
                merchant_id = trigger.get("merchant_id")
                conversation_id = f"conv_{merchant_id}_{trigger_id}"

                if conversation_id in ended_conversations:
                    continue

                result = compose(category, merchant, trigger, customer)
                if not result or not result["body"]:
                    continue

                suppression_key = result["suppression_key"]
                if suppression_key and suppression_key in sent_keys:
                    continue

                if suppression_key:
                    sent_keys.add(suppression_key)

                customer_id = trigger.get("customer_id")
                actions.append({
                    "conversation_id": conversation_id,
                    "merchant_id": merchant_id,
                    "customer_id": customer_id,
                    "send_as": result["send_as"],
                    "trigger_id": trigger_id,
                    "template_name": result["template_name"],
                    "template_params": [
                        merchant_name(merchant) if merchant else "there",
                        trigger.get("kind", ""),
                    ],
                    "body": result["body"],
                    "cta": result["cta"],
                    "suppression_key": suppression_key,
                    "rationale": result["rationale"],
                })

            self.json_response(200, {"actions": actions})
            return

        if path == "/v1/reply":
            self.json_response(200, reply_action(data))
            return

        if path == "/v1/teardown":
            contexts.clear()
            conversations.clear()
            sent_keys.clear()
            auto_reply_seen.clear()
            ended_conversations.clear()
            self.json_response(200, {"ok": True})
            return

        self.json_response(404, {"error": "not_found"})

    def log_message(self, fmt, *args):
        print(f"[BOT] {self.address_string()} - {fmt % args}")


def main():
    server = ThreadingHTTPServer((HOST, PORT), Handler)

    print("=" * 64)
    print("magicpin AI Challenge Bot - Vera")
    print(f"Running at http://localhost:{PORT}")
    print("Endpoints:")
    print("  GET  /v1/healthz")
    print("  GET  /v1/metadata")
    print("  POST /v1/context")
    print("  POST /v1/tick")
    print("  POST /v1/reply")
    print("  POST /v1/teardown")
    print("Press Ctrl+C to stop.")
    print("=" * 64)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping bot...")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
