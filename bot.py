import json
import re
import time
import unicodedata
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlparse
import os

HOST = "0.0.0.0"
PORT = int(os.environ.get("PORT", "8080"))
START_TIME = time.time()

# In-memory state. The challenge does not require a database.
contexts = {}          # (scope, context_id) -> {"version": int, "payload": dict}
conversations = {}     # conversation_id -> list of turns
sent_keys = set()      # suppression keys already emitted
ended_conversations = set()
conversation_counter = 0
auto_reply_state = {}   # merchant_id -> {text, count}


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

VALID_SCOPES = {"category", "merchant", "customer", "trigger"}


def next_conversation_id(merchant_id, trigger_id, customer_id=None):
    global conversation_counter
    conversation_counter += 1
    suffix = f"_{customer_id}" if customer_id else ""
    return f"conv_{merchant_id}_{trigger_id}{suffix}_{conversation_counter}"


def merchant_name(merchant):
    identity = merchant.get("identity", {}) or {}
    return str(first(
        identity.get("name"),
        merchant.get("owner_name"),
        merchant.get("name"),
        "there",
    ))


def owner_name(merchant):
    identity = merchant.get("identity", {}) or {}
    return str(first(
        identity.get("owner_name"),
        merchant.get("owner_name"),
        identity.get("name"),
        merchant.get("name"),
        "there",
    ))


def category_slug(merchant):
    return str(first(
        merchant.get("category_slug"),
        merchant.get("category"),
        merchant.get("category_id"),
        "",
    ))


def language_pref(merchant):
    identity = merchant.get("identity", {}) or {}
    return str(first(
        identity.get("language_pref"),
        identity.get("language"),
        "en",
    )).lower()


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
    slug = str(category.get("slug", category_slug(merchant))).lower()
    if slug == "dentists" and not name.lower().startswith("dr."):
        return "Dr. " + name
    return name


def active_offer(merchant):
    offers = merchant.get("offers", []) or []
    for offer in offers:
        if isinstance(offer, dict):
            status = str(offer.get("status", "active")).lower()
            if status == "active":
                return offer
    return None


def resolve_trigger(trigger):
    merchant_id = trigger.get("merchant_id")
    customer_id = trigger.get("customer_id")

    merchant = get_context("merchant", merchant_id)
    category = None
    if merchant:
        slug = category_slug(merchant)
        category = get_context("category", slug)

        # Some datasets put the category directly in the trigger.
        if not category:
            category = get_context(
                "category",
                trigger.get("payload", {}).get("category"),
            )

    customer = get_context("customer", customer_id) if customer_id else None
    return category, merchant, customer


def payload(trigger):
    value = trigger.get("payload", {})
    return value if isinstance(value, dict) else {}


def top_item_from_trigger(category, trigger):
    p = payload(trigger)

    # Newer challenge examples use payload["top_item"].
    item = p.get("top_item")
    if isinstance(item, dict):
        return item

    # Other dataset variants use an ID into category["digest"].
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
            f"{name}, a new research update is relevant to your business. "
            "Want me to pull the key point and turn it into one practical next step?"
        )

    title = first(item.get("title"), item.get("headline"), "a new research update")
    source = first(item.get("source"), item.get("citation"))
    sample = first(
        item.get("trial_n"),
        item.get("sample_size"),
        item.get("participants"),
    )
    effect = first(item.get("effect_size"), item.get("result"), item.get("lift"))
    segment = first(
        item.get("patient_segment"),
        item.get("customer_segment"),
        item.get("segment"),
    )

    body = f"{name}, {title}."
    if sample is not None:
        body += f" The evidence covers {sample:,} participants." if isinstance(sample, int) else f" Sample: {sample}."
    if effect:
        body += f" Result: {effect}."
    if segment:
        body += f" This is especially relevant to {segment}."
    if source:
        body += f" Source: {source}."
    body += " Want me to pull the key point and turn it into a practical action?"
    return body


def performance_message(category, merchant, trigger):
    name = display_name(category, merchant)
    kind = str(trigger.get("kind", "")).lower()
    performance = merchant.get("performance", {}) or {}
    peer = category.get("peer_stats", {}) or {}

    current = first(
        performance.get("ctr"),
        performance.get("conversion_rate"),
        performance.get("booking_rate"),
        performance.get("views_change"),
        performance.get("calls_change"),
    )
    benchmark = first(
        peer.get("avg_ctr"),
        peer.get("median_ctr"),
        peer.get("avg_conversion_rate"),
    )

    p = payload(trigger)
    delta = first(
        p.get("change_pct"),
        p.get("delta_pct"),
        p.get("change"),
        p.get("drop_pct"),
        p.get("increase_pct"),
    )
    metric = first(p.get("metric"), p.get("metric_name"), "performance")

    if delta is not None:
        change = fmt_pct(delta)
        if "dip" in kind or (isinstance(delta, (int, float)) and float(delta) < 0):
            body = f"{name}, {metric} is down {change} versus the comparison period."
        else:
            body = f"{name}, {metric} is up {change} versus the comparison period."
    elif current is not None and benchmark is not None:
        body = (
            f"{name}, your current signal is {fmt_pct(current)}, "
            f"versus {fmt_pct(benchmark)} for the available peer benchmark."
        )
    elif current is not None:
        body = f"{name}, your latest {metric} signal is {fmt_pct(current)}."
    else:
        body = f"{name}, there is a recent {metric} signal worth looking at."

    if "dip" in kind:
        body += " I can break down the likely driver and suggest one practical fix."
    else:
        body += " I can turn this signal into one practical next step."
    return body


def generic_trigger_message(category, merchant, trigger):
    name = display_name(category, merchant)
    kind = str(trigger.get("kind", "")).lower()
    p = payload(trigger)

    body = f"{name}, "

    if kind == "milestone_reached":
        metric = first(p.get("metric"), p.get("metric_name"), "metric")
        now = first(p.get("value_now"), p.get("current_value"))
        milestone = first(p.get("milestone_value"), p.get("target"))
        if now is not None and milestone is not None:
            body += f"you’re at {now} {metric}, with the {milestone} milestone close."
        else:
            body += "you’re close to a useful business milestone."

    elif kind == "review_theme_emerged":
        theme = first(p.get("theme"), p.get("review_theme"), "a review theme")
        count = first(p.get("occurrences_30d"), p.get("review_count"), p.get("count"))
        trend = first(p.get("trend"), "")
        body += f"{count} recent reviews are pointing to ‘{theme}’" if count is not None else f"a review theme around ‘{theme}’ is emerging"
        if trend:
            body += f" and the trend is {trend}"
        body += "."

    elif kind == "competitor_opened":
        competitor = first(p.get("competitor_name"), p.get("name"))
        distance = first(p.get("distance_km"), p.get("distance"))
        if competitor and distance is not None:
            body += f"a new local competitor, {competitor}, is listed about {distance} km away."
        elif distance is not None:
            body += f"a new local competitor signal is showing about {distance} km away."
        else:
            body += "a new local competitor signal has appeared."

    elif kind == "festival_upcoming":
        event = first(p.get("festival"), p.get("event"), p.get("name"), "an upcoming local event")
        days = first(p.get("days"), p.get("days_until"))
        body += f"{event} is coming up"
        if days is not None:
            body += f" in {days} days"
        body += "."

    elif kind in ("renewal_due", "winback_eligible"):
        days = first(p.get("days_remaining"), p.get("days_since_expiry"))
        body += "your magicpin plan needs attention"
        if days is not None:
            body += f" — the current window is {days} days"
        body += "."

    elif kind == "curious_ask_due":
        ask = first(p.get("ask_template"), p.get("question"), "a quick demand question")
        body += f"I have {str(ask).replace('_', ' ')} for you."

    elif kind == "active_planning_intent":
        topic = first(p.get("intent_topic"), "the idea we were discussing")
        last = first(p.get("merchant_last_message"), "")
        body += f"we already have an active thread on {str(topic).replace('_', ' ')}."
        if last:
            body += f" You last said: “{last}”."

    elif kind == "ipl_match_today":
        match = first(p.get("match"), "today’s match")
        venue = first(p.get("venue"), "")
        body += f"{match} is on today"
        if venue:
            body += f" near {venue}"
        body += "."

    elif kind in ("perf_dip", "seasonal_perf_dip"):
        metric = first(p.get("metric"), p.get("metric_name"), "performance")
        delta = first(p.get("delta_pct"), p.get("change_pct"), p.get("drop_pct"))
        body += f"your {metric} signal has dipped"
        if delta is not None:
            body += f" by {fmt_pct(delta)}"
        body += "."

    elif kind in ("dormant_with_vera", "scheduled_recurring"):
        days = first(p.get("days_since_last_message"), p.get("days"))
        body += "it’s been a while since we last worked together"
        if days is not None:
            body += f" ({days} days)"
        body += "."

    else:
        opener = {
            "regulation_change": "there’s a relevant regulation update",
            "weather_heatwave": "there’s a weather signal that may affect demand",
            "local_news_event": "there’s a local event/news signal that may affect demand",
            "category_trend_movement": "there’s a category trend worth checking",
            "trial_followup": "a follow-up is due on a recent trial",
        }.get(kind, "there’s a timely update relevant to your business")
        body += opener + "."

    body += " Want me to turn this into one practical next step?"
    return body


def customer_message(category, merchant, trigger, customer):
    cname = str(first(
        (customer.get("identity", {}) or {}).get("name"),
        customer.get("name"),
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

    relationship = customer.get("relationship", {}) or {}
    preferences = customer.get("preferences", {}) or {}
    last_visit = relationship.get("last_visit")
    lang = str(first(
        (customer.get("identity", {}) or {}).get("language_pref"),
        customer.get("language_pref"),
        language_pref(merchant),
    )).lower()

    p = payload(trigger)
    slots = first(
        p.get("available_slots"),
        p.get("slots"),
        customer.get("available_slots"),
    )

    if "recall" in kind or "appointment" in kind:
        body = f"Hi {cname}, {mname} here."
        if last_visit:
            body += f" Based on your last visit on {last_visit}, your next visit is due."
        else:
            body += " It looks like you're due for your next visit."
        if offer_title:
            body += f" {offer_title} is currently available."
        if offer_price is not None:
            body += f" Price: ₹{offer_price}."
        if slots:
            if isinstance(slots, list):
                body += " Available slots: " + ", ".join(str(x) for x in slots[:3]) + "."
            else:
                body += f" Available slot: {slots}."
        body += " Reply with a suitable time and we'll take it from there."
        return body

    if "lapsed" in kind or "winback" in kind:
        body = f"Hi {cname}, {mname} here. We haven't seen you in a while."
        if offer_title:
            body += f" {offer_title} is currently available."
        if offer_price is not None:
            body += f" It's ₹{offer_price}."
        body += " Would you like help finding a convenient time?"
        if "hi" in lang:
            body += " Aap chahein toh main suitable slot mein help kar sakta hoon."
        return body

    if "refill" in kind:
        body = f"Namaste {cname}, {mname} here."
        item = first(p.get("medicines"), p.get("items"), p.get("products"))
        if item:
            if isinstance(item, list):
                body += " Your refill items: " + ", ".join(str(x) for x in item) + "."
            else:
                body += f" Your refill item: {item}."
        due = first(p.get("due_date"), p.get("date"), p.get("expires_on"))
        if due:
            body += f" Refill is due by {due}."
        body += " Reply CONFIRM if you'd like us to prepare it."
        return body

    body = f"Hi {cname}, {mname} here. We have an update relevant to you."
    if offer_title:
        body += f" {offer_title} is currently available."
    if offer_price is not None:
        body += f" Price: ₹{offer_price}."
    body += " Would you like help with the next step?"
    return body


def compose(category, merchant, trigger, customer=None):
    if not all(isinstance(x, dict) for x in (category, merchant, trigger)):
        return None

    kind = str(trigger.get("kind", "")).lower()
    customer_scoped = customer is not None or trigger.get("scope") == "customer"

    if trigger.get("scope") == "customer" and customer is None:
        return None

    if customer_scoped and customer is not None:
        body = customer_message(category, merchant, trigger, customer)
        send_as = "merchant_on_behalf"
        template = "merchant_customer_v2"
        rationale = "Customer-scoped trigger composed from merchant, customer, offer, and trigger context."
    elif "research" in kind or "digest" in kind:
        body = research_message(category, merchant, trigger)
        send_as = "vera"
        template = "vera_research_v2"
        rationale = "Research message uses the trigger's research item and available source/details."
    elif any(x in kind for x in (
        "perf_dip", "performance_dip", "perf_spike", "performance_spike",
        "seasonal_perf_dip",
    )):
        body = performance_message(category, merchant, trigger)
        send_as = "vera"
        template = "vera_performance_v2"
        rationale = "Performance message uses merchant performance and trigger change/benchmark context."
    else:
        body = generic_trigger_message(category, merchant, trigger)
        send_as = "vera"
        template = "vera_trigger_v2"
        rationale = f"Message is anchored to the trigger kind '{kind or 'unknown'}' and merchant context."

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

    opt_out_phrases = (
        "stop messaging me", "stop msging me", "stop texting me",
        "stop contacting me", "do not message me", "don't message me",
        "dont message me", "do not contact me", "don't contact me",
        "dont contact me", "remove me", "unsubscribe", "leave me alone",
        "this is spam", "useless spam", "stop the spam", "no more messages",
        "never message me", "never contact me", "take me off",
        "not interested", "not interested at all",
    )
    if any(p in t for p in opt_out_phrases):
        return "opt_out"

    if t in {"stop", "unsubscribe", "remove me", "no thanks", "not now",
              "leave it", "leave me", "no more"}:
        return "opt_out"

    abuse_markers = (
        "fuck you", "f*** you", "f u", "fucking", "fuck", "shut up",
        "idiot", "stupid", "moron", "useless", "bakwas", "bakwaas",
        "chutiya", "madarchod", "bc", "mc", "gand mara", "pagal ho",
    )
    if any(p in t for p in abuse_markers):
        return "hostile"
    return None


def is_action_intent(text):
    t = normalize(text)
    strong = (
        "let's do it", "lets do it", "ok let's do it", "okay let's do it",
        "ok lets do it", "okay lets do it", "go ahead", "go for it",
        "do it", "send it", "start it", "start now", "proceed",
        "move ahead", "i want to join", "i want to start",
        "i want to sign up", "sign me up", "book it", "whats next",
        "what's next", "mujhe join karna hai", "main join karna chahta hoon",
        "main join karna chahti hoon", "aage badho", "kar do",
    )
    if any(p in t for p in strong):
        return True
    return t in {
        "yes", "yeah", "yep", "sure", "okay", "ok", "interested",
        "yes please", "sure thing", "done", "haan", "han", "ji haan",
    }


def is_off_topic(text):
    t = normalize(text)
    topic_words = (
        "gst", "income tax", "itr", "stock market", "bitcoin",
        "weather tomorrow", "college admission", "exam",
    )
    return any(x in t for x in topic_words)


def reply_action(data):
    if not isinstance(data, dict):
        return {"action": "end", "rationale": "Invalid reply payload; no safe response can be composed."}

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
        return {"action": "end", "rationale": "Conversation was previously closed; no further outreach is appropriate."}

    hostility = is_hostile_or_opt_out(message)
    if hostility == "opt_out":
        ended_conversations.add(conv_id)
        return {
            "action": "end",
            "rationale": "Merchant explicitly asked the assistant to stop; conversation ended respectfully.",
        }

    if hostility == "hostile":
        return {
            "action": "send",
            "body": "Sorry about the frustration. I’ll keep this focused on your magicpin business activity. Tell me what you’d like to do next.",
            "cta": "open_ended",
            "rationale": "Handled hostile language calmly without escalating and kept the conversation on the merchant-assistance task.",
        }

    if is_auto_reply(message):
        normalized = normalize(message)
        state = auto_reply_state.get(merchant_id)
        if state and state.get("text") == normalized:
            count = state.get("count", 0) + 1
        else:
            count = 1
        auto_reply_state[merchant_id] = {"text": normalized, "count": count}

        if count == 1:
            return {
                "action": "send",
                "body": "Looks like an automated reply 😊 When the owner sees this, just reply 'Yes' if you'd like to continue.",
                "cta": "binary_yes_no",
                "rationale": "Detected a likely canned WhatsApp auto-reply; made one low-friction attempt to reach the owner instead of treating it as merchant intent.",
            }
        if count == 2:
            return {
                "action": "wait",
                "wait_seconds": 86400,
                "rationale": "The same canned auto-reply appeared twice; backing off for 24 hours instead of consuming another turn.",
            }
        ended_conversations.add(conv_id)
        return {
            "action": "end",
            "rationale": "The same canned auto-reply repeated three or more times with no engagement signal; closing to avoid spam.",
        }

    # A real merchant reply breaks the repeated auto-reply streak.
    auto_reply_state.pop(merchant_id, None)

    if is_off_topic(message):
        return {
            "action": "send",
            "body": "I can help with your magicpin business and profile activity, but I can’t advise on unrelated topics here. Let’s continue with the business action we were discussing.",
            "cta": "open_ended",
            "rationale": "Stayed on mission and declined an unrelated request without fabricating expertise.",
        }

    if is_action_intent(message):
        return {
            "action": "send",
            "body": "Absolutely — let’s do it. I’m moving to the next step using the context you’ve already shared, without repeating the qualification questions.",
            "cta": "open_ended",
            "rationale": "Detected clear commitment and switched directly from qualification to execution.",
        }

    return {
        "action": "send",
        "body": "Got it. I can take the next practical step from here. Want me to proceed?",
        "cta": "binary_yes_no",
        "rationale": "Acknowledged the merchant and proposed one low-friction next step.",
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

        if path == "/":
            self.json_response(200, {
                "name": "Magicpin Vera Bot",
                "status": "ok",
                "message": "Vera Bot API is running",
                "endpoints": {
                    "GET /v1/healthz": "/v1/healthz",
                    "GET /v1/metadata": "/v1/metadata",
                    "POST /v1/context": "/v1/context",
                    "POST /v1/tick": "/v1/tick",
                    "POST /v1/reply": "/v1/reply",
                    "POST /v1/teardown": "/v1/teardown"
                }
            })
            return
        
        self.json_response(404, {"error": "not_found"})

    def do_POST(self):
        path = urlparse(self.path).path

        try:
            data = self.read_json()
        except (json.JSONDecodeError, UnicodeDecodeError, ValueError, TypeError):
            self.json_response(400, {"error": "invalid_json"})
            return

        if not isinstance(data, dict):
            self.json_response(400, {"error": "invalid_json"})
            return

        if path == "/v1/context":
            scope = data.get("scope")
            context_id = data.get("context_id")
            version_raw = data.get("version")
            payload_value = data.get("payload")

            if scope not in VALID_SCOPES:
                self.json_response(400, {
                    "accepted": False,
                    "reason": "invalid_scope",
                    "details": "scope must be category, merchant, customer, or trigger",
                })
                return

            if not isinstance(context_id, str) or not context_id.strip():
                self.json_response(400, {
                    "accepted": False,
                    "reason": "invalid_context",
                    "details": "context_id is required",
                })
                return

            try:
                version = int(version_raw)
            except (TypeError, ValueError):
                self.json_response(400, {
                    "accepted": False,
                    "reason": "invalid_context",
                    "details": "version must be an integer",
                })
                return

            if version < 1 or not isinstance(payload_value, dict):
                self.json_response(400, {
                    "accepted": False,
                    "reason": "invalid_context",
                    "details": "version must be >= 1 and payload must be an object",
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

            # Re-posting the same version is explicitly idempotent.
            if current and current["version"] == version:
                self.json_response(200, {
                    "accepted": True,
                    "ack_id": f"ack_{context_id}_v{version}",
                    "stored_at": iso_now(),
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
            available = data.get("available_triggers", [])
            if not isinstance(available, list):
                self.json_response(400, {"error": "invalid_available_triggers"})
                return

            actions = []
            used_pairs = set()

            for trigger_id in available[:20]:
                if not isinstance(trigger_id, str):
                    continue
                trigger = get_context("trigger", trigger_id)
                if not trigger:
                    continue

                category, merchant, customer = resolve_trigger(trigger)
                if not merchant or not category:
                    continue
                if trigger.get("scope") == "customer" and not customer:
                    continue

                merchant_id = trigger.get("merchant_id")
                customer_id = trigger.get("customer_id")
                pair = (merchant_id, trigger_id)
                if pair in used_pairs:
                    continue

                result = compose(category, merchant, trigger, customer)
                if not result or not result.get("body"):
                    continue

                suppression_key = result.get("suppression_key", "")
                if suppression_key and suppression_key in sent_keys:
                    continue

                # A tick starts proactive conversations, so use a fresh ID.
                conversation_id = next_conversation_id(merchant_id, trigger_id, customer_id)

                if suppression_key:
                    sent_keys.add(suppression_key)
                used_pairs.add(pair)

                actions.append({
                    "conversation_id": conversation_id,
                    "merchant_id": merchant_id,
                    "customer_id": customer_id,
                    "send_as": result["send_as"],
                    "trigger_id": trigger_id,
                    "template_name": result["template_name"],
                    "template_params": [
                        merchant_name(merchant),
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
            auto_reply_state.clear()
            ended_conversations.clear()
            global conversation_counter
            conversation_counter = 0
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
