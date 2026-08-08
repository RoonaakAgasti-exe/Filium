"""
alerts_engine.py

Evaluates standing alert rules against the latest predictions, prices and
sentiment, records anything that fires in `alert_events`, and optionally
emails the user.

Called from two places: the nightly scheduler (ml/scheduler.py) and the
"check now" button in the app. Both go through `evaluate_all_alerts`, so
there is exactly one definition of when a rule fires.

Rules are a fixed set of shapes rather than free-form expressions. That's
what makes natural-language alert creation safe: the LLM's job is to pick
a shape and a threshold, not to emit code that later gets evaluated.
"""

import logging
import re
from datetime import date, datetime, timezone

import config
import emailer

logger = logging.getLogger("fincopilot.alerts")

RULE_TYPES = (
    "prediction_flip",
    "price_move",
    "sentiment_below",
    "sentiment_above",
    "confidence_above",
)

RULE_DESCRIPTIONS = {
    "prediction_flip": "the daily prediction flips direction",
    "price_move": "the close moves more than a set percentage in a day",
    "sentiment_below": "news sentiment drops below a threshold",
    "sentiment_above": "news sentiment rises above a threshold",
    "confidence_above": "prediction confidence exceeds a threshold",
}

def _latest_two_predictions(conn, ticker: str) -> list[dict]:
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT prediction_date, predicted_direction, confidence
            FROM predictions
            WHERE ticker = %s
            ORDER BY prediction_date DESC, id DESC
            LIMIT 2
            """,
            (ticker,),
        )
        return [
            {"prediction_date": r[0], "predicted_direction": r[1], "confidence": float(r[2])}
            for r in cur.fetchall()
        ]
    finally:
        cur.close()

def _latest_two_closes(conn, ticker: str) -> list[dict]:
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT date, close FROM price_history
            WHERE ticker = %s AND close IS NOT NULL
            ORDER BY date DESC LIMIT 2
            """,
            (ticker,),
        )
        return [{"date": r[0], "close": float(r[1])} for r in cur.fetchall()]
    finally:
        cur.close()

def _latest_sentiment(conn, ticker: str) -> dict | None:
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT date, score FROM sentiment_scores
            WHERE ticker = %s AND source = 'news' AND score IS NOT NULL
            ORDER BY date DESC LIMIT 1
            """,
            (ticker,),
        )
        row = cur.fetchone()
    finally:
        cur.close()
    if row is None:
        return None
    return {"date": row[0], "score": float(row[1])}

def evaluate_rule(conn, alert: dict) -> str | None:
    """
    Returns the alert message if the rule fires, else None.
    `alert` needs: ticker, rule_type, threshold.
    """
    ticker = alert["ticker"]
    rule_type = alert["rule_type"]
    threshold = float(alert["threshold"]) if alert.get("threshold") is not None else None

    if rule_type == "prediction_flip":
        predictions = _latest_two_predictions(conn, ticker)
        if len(predictions) < 2:
            return None
        latest, previous = predictions[0], predictions[1]
        if latest["predicted_direction"] != previous["predicted_direction"]:
            return (
                f"{ticker} signal flipped from {previous['predicted_direction'].upper()} "
                f"to {latest['predicted_direction'].upper()} "
                f"({latest['confidence']:.0%} confidence, {latest['prediction_date']})."
            )
        return None

    if rule_type == "price_move":
        closes = _latest_two_closes(conn, ticker)
        if len(closes) < 2 or threshold is None:
            return None
        latest, previous = closes[0], closes[1]
        if not previous["close"]:
            return None
        change_pct = (latest["close"] - previous["close"]) / previous["close"] * 100
        if abs(change_pct) >= threshold:
            direction = "up" if change_pct > 0 else "down"
            return (
                f"{ticker} moved {direction} {abs(change_pct):.2f}% on {latest['date']} "
                f"(${previous['close']:,.2f} -> ${latest['close']:,.2f}), "
                f"past your {threshold:g}% threshold."
            )
        return None

    if rule_type in ("sentiment_below", "sentiment_above"):
        sentiment = _latest_sentiment(conn, ticker)
        if sentiment is None or threshold is None:
            return None
        score = sentiment["score"]
        fired = score < threshold if rule_type == "sentiment_below" else score > threshold
        if fired:
            comparison = "fell below" if rule_type == "sentiment_below" else "rose above"
            return (
                f"{ticker} news sentiment {comparison} {threshold:+.2f} "
                f"(now {score:+.2f}, as of {sentiment['date']})."
            )
        return None

    if rule_type == "confidence_above":
        predictions = _latest_two_predictions(conn, ticker)
        if not predictions or threshold is None:
            return None
        latest = predictions[0]
        if latest["confidence"] >= threshold:
            return (
                f"{ticker} signal is {latest['predicted_direction'].upper()} at "
                f"{latest['confidence']:.0%} confidence, past your {threshold:.0%} threshold "
                f"({latest['prediction_date']})."
            )
        return None

    logger.warning("Unknown alert rule_type %r on alert %s", rule_type, alert.get("id"))
    return None

def _already_fired_today(alert: dict, today: date) -> bool:
    last_fired = alert.get("last_fired_at")
    if last_fired is None:
        return False
    if isinstance(last_fired, datetime):
        last_fired = last_fired.date()
    return last_fired >= today

def record_event(conn, alert: dict, message: str, send_email: bool = True) -> dict:
    """Writes the alert_event, stamps last_fired_at, and optionally emails."""
    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO alert_events (alert_id, user_id, ticker, message) "
            "VALUES (%s, %s, %s, %s) RETURNING id, created_at",
            (alert["id"], alert["user_id"], alert["ticker"], message),
        )
        event_id, created_at = cur.fetchone()

        cur.execute(
            "UPDATE alerts SET last_fired_at = %s WHERE id = %s",
            (datetime.now(timezone.utc), alert["id"]),
        )

        emailed = False
        if send_email and emailer.is_configured():
            cur.execute("SELECT email FROM users WHERE id = %s", (alert["user_id"],))
            row = cur.fetchone()
            if row and row[0] and not config.is_guest_email(row[0]):
                emailed = emailer.send_alert_email(row[0], alert["ticker"], message)
                if emailed:
                    cur.execute("UPDATE alert_events SET emailed = TRUE WHERE id = %s", (event_id,))

        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()

    return {
        "id": event_id,
        "alert_id": alert["id"],
        "ticker": alert["ticker"],
        "message": message,
        "created_at": created_at.isoformat(),
        "emailed": emailed,
    }

def evaluate_all_alerts(conn, user_id: int | None = None, force: bool = False,
                        send_email: bool = True) -> list[dict]:
    """
    Evaluates every active alert (optionally scoped to one user) and
    returns the events that fired.

    `force=False` suppresses a rule that already fired today — otherwise
    a persistent condition like "sentiment below -0.2" would re-notify on
    every single scheduler run until the sentiment happened to move.
    """
    sql = [
        "SELECT id, user_id, ticker, rule_type, threshold, natural_language, last_fired_at",
        "FROM alerts WHERE is_active = TRUE",
    ]
    params: list = []
    if user_id is not None:
        sql.append("AND user_id = %s")
        params.append(user_id)

    cur = conn.cursor()
    try:
        cur.execute("\n".join(sql), tuple(params))
        columns = [d[0] for d in cur.description]
        alerts = [dict(zip(columns, row)) for row in cur.fetchall()]
    finally:
        cur.close()

    today = date.today()
    fired = []

    for alert in alerts:
        if not force and _already_fired_today(alert, today):
            continue
        try:
            message = evaluate_rule(conn, alert)
        except Exception:
            logger.exception("Alert %s failed to evaluate", alert["id"])
            conn.rollback()
            continue

        if message:
            fired.append(record_event(conn, alert, message, send_email=send_email))

    return fired

_NL_SYSTEM_PROMPT = """You convert a user's plain-English stock alert request into a
structured rule. Respond with ONLY a JSON object.

Available rule types:
- "prediction_flip"   — the daily up/down prediction changes direction. No threshold.
- "price_move"        — absolute daily price change. threshold = percent, e.g. 5 for 5%.
- "sentiment_below"   — news sentiment drops below threshold. threshold between -1 and 1.
- "sentiment_above"   — news sentiment rises above threshold. threshold between -1 and 1.
- "confidence_above"  — prediction confidence exceeds threshold. threshold between 0 and 1.

Schema:
{
  "ticker": "UPPERCASE TICKER",
  "rule_type": "one of the five above",
  "threshold": number or null,
  "understood": true or false,
  "explanation": "one short sentence restating the rule in plain English"
}

If the request doesn't map cleanly onto one of these rule types, or no ticker is
identifiable, set "understood" to false and explain why in "explanation".
"""

_NOT_TICKERS = {
    "A", "I", "IF", "IT", "IS", "IN", "ON", "AT", "TO", "BY", "OR", "AND", "THE", "ME",
    "MY", "WHEN", "NOT", "US", "GO", "UP", "DO", "SO", "BE", "AN", "AS", "OF", "FOR",
    "ANY", "ALL", "NEW", "LOW", "HIGH", "BUY", "SELL", "PCT", "USD", "AI", "CEO", "SEC",
    "P", "E", "EPS", "ETF", "IPO", "YOY", "Q", "FY",
}

_TICKER_RE = re.compile(r"\$([A-Za-z]{1,5})\b|\b([A-Z]{1,5})\b")
_NUMBER_RE = re.compile(r"(-?\d+(?:\.\d+)?)\s*(%?)")

def _extract_ticker(text: str) -> str | None:
    """First $TICKER, else the first all-caps token that isn't an English word."""
    for match in _TICKER_RE.finditer(text):
        dollar, bare = match.group(1), match.group(2)
        if dollar:
            return dollar.upper()
        if bare and bare.upper() not in _NOT_TICKERS:
            return bare.upper()
    return None

def parse_alert_text(text: str) -> dict:
    """
    Keyword parser for alert requests, used when no LLM is available.

    The rule vocabulary is five fixed shapes, which is small enough to
    match on directly — the LLM was always choosing from this same menu,
    never writing anything that gets evaluated. So the keyless path loses
    tolerance for unusual phrasing, not capability, and when the phrasing
    is too unusual it raises rather than guessing.

    Raises ValueError with a message naming what it could not work out.
    """
    lowered = text.lower()
    ticker = _extract_ticker(text)
    if not ticker:
        raise ValueError(
            "Could not identify a ticker in that request. Write it in capitals "
            "or with a dollar sign, e.g. \"tell me if NVDA sentiment turns negative\"."
        )

    numbers = [(float(n), bool(pct)) for n, pct in _NUMBER_RE.findall(text)]

    def first_number(default=None, *, as_fraction=False):
        for value, is_pct in numbers:
            return value / 100 if (as_fraction and (is_pct or value > 1)) else value
        return default

    if any(w in lowered for w in ("flip", "flips", "reverse", "changes direction",
                                  "change direction", "switches")):
        return {"ticker": ticker, "rule_type": "prediction_flip", "threshold": None,
                "explanation": f"Alert when {ticker}'s daily prediction flips direction."}

    if "sentiment" in lowered:
        negative = any(w in lowered for w in ("negative", "below", "drops", "falls", "worse", "sours"))
        rule_type = "sentiment_below" if negative else "sentiment_above"
        threshold = first_number(0.0)
        return {"ticker": ticker, "rule_type": rule_type, "threshold": float(threshold),
                "explanation": (
                    f"Alert when {ticker}'s news sentiment "
                    f"{'falls below' if negative else 'rises above'} {threshold:+.2f}.")}

    if "confidence" in lowered:
        threshold = first_number(as_fraction=True)
        if threshold is None:
            raise ValueError("A confidence alert needs a threshold, e.g. \"above 80%\".")
        return {"ticker": ticker, "rule_type": "confidence_above", "threshold": float(threshold),
                "explanation": f"Alert when {ticker}'s prediction confidence exceeds {threshold:.0%}."}

    if any(w in lowered for w in ("move", "moves", "%", "percent", "price", "drop", "rise",
                                 "gain", "fall", "jump")):
        threshold = first_number()
        if threshold is None:
            raise ValueError("A price-move alert needs a percentage, e.g. \"more than 5%\".")
        return {"ticker": ticker, "rule_type": "price_move", "threshold": abs(float(threshold)),
                "explanation": f"Alert when {ticker} moves more than {abs(threshold):g}% in a day."}

    raise ValueError(
        "Could not map that request onto a supported alert. Supported: prediction "
        "flips, price moves by a percentage, news sentiment above/below a level, and "
        "prediction confidence above a level."
    )

def parse_natural_language_alert(text: str) -> dict:
    """
    Turns "notify me if NVDA's sentiment turns negative" into
    {ticker: NVDA, rule_type: sentiment_below, threshold: 0}.

    Uses the LLM when one is configured, because it handles phrasing the
    keyword parser will not. Falls back to `parse_alert_text` otherwise,
    so the feature degrades to stricter phrasing rather than to a 503 —
    which is what it used to do on any deployment without a key, or with a
    key that had run out of credit.
    """
    import llm

    if not llm.is_configured():
        return parse_alert_text(text)

    try:
        result = llm.complete_json(f"Alert request: {text}", system=_NL_SYSTEM_PROMPT)
    except llm.LLMUnavailable as exc:
        logger.info("Parsing alert text without the LLM: %s", exc)
        return parse_alert_text(text)

    if not result.get("understood"):
        raise ValueError(
            result.get("explanation") or "Could not interpret that alert request."
        )

    rule_type = result.get("rule_type")
    if rule_type not in RULE_TYPES:
        raise ValueError(f"Unsupported rule type returned by the parser: {rule_type!r}")

    ticker = (result.get("ticker") or "").strip().upper()
    if not ticker:
        raise ValueError("No ticker identified in that request.")

    threshold = result.get("threshold")
    if rule_type != "prediction_flip" and threshold is None:
        raise ValueError(f"A threshold is required for {rule_type} alerts.")

    return {
        "ticker": ticker,
        "rule_type": rule_type,
        "threshold": float(threshold) if threshold is not None else None,
        "explanation": result.get("explanation") or "",
    }
