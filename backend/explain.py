# explain.py — Generates AI explanations for trades.
pass

import logging

import llm

logger = logging.getLogger("fincopilot.explain")

SYSTEM_PROMPT = """You explain a single paper trade in two or three sentences,
for someone learning how model signals translate into decisions.

Rules:
- Refer only to the signal, sentiment and price context you are given.
- If the trade runs AGAINST the model's signal, say so plainly rather than
  inventing a justification for it.
- If there is no prediction or no sentiment data, acknowledge the gap instead
  of implying the trade was better-supported than it was.
- Never give investment advice or predict a specific future price.
- Plain prose, no bullet points, no preamble.
"""

def gather_context(conn, ticker: str) -> dict:
    pass
    context: dict = {"prediction": None, "sentiment": None, "recent_close": None}

    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT p.id, p.predicted_direction, p.confidence, p.prediction_date,
                   p.target_date, m.name
            FROM predictions p
            JOIN model_versions m ON m.id = p.model_version_id
            WHERE p.ticker = %s
            ORDER BY p.prediction_date DESC, p.id DESC LIMIT 1
            """,
            (ticker,),
        )
        row = cur.fetchone()
        if row:
            context["prediction"] = {
                "id": row[0], "direction": row[1], "confidence": float(row[2]),
                "prediction_date": str(row[3]), "target_date": str(row[4]), "model": row[5],
            }

        cur.execute(
            "SELECT date, score, article_count FROM sentiment_scores "
            "WHERE ticker = %s AND source = 'news' AND score IS NOT NULL "
            "ORDER BY date DESC LIMIT 1",
            (ticker,),
        )
        row = cur.fetchone()
        if row:
            context["sentiment"] = {
                "date": str(row[0]), "score": float(row[1]), "article_count": row[2] or 0,
            }

        cur.execute(
            "SELECT date, close FROM price_history WHERE ticker = %s AND close IS NOT NULL "
            "ORDER BY date DESC LIMIT 1",
            (ticker,),
        )
        row = cur.fetchone()
        if row:
            context["recent_close"] = {"date": str(row[0]), "close": float(row[1])}
    finally:
        cur.close()

    return context

def _build_prompt(action: str, ticker: str, shares: float, price: float,
                  triggered_by_prediction: bool, context: dict) -> str:
    lines = [
        f"Trade: {action.upper()} {shares:g} share(s) of {ticker} at ${price:,.2f}.",
        f"Marked as acting on the model signal: {'yes' if triggered_by_prediction else 'no'}.",
    ]

    prediction = context.get("prediction")
    if prediction:
        lines.append(
            f"Latest model signal ({prediction['model']}): {prediction['direction'].upper()} "
            f"for {prediction['target_date']}, {prediction['confidence']:.0%} confidence, "
            f"made on {prediction['prediction_date']}."
        )
    else:
        lines.append("No model prediction exists for this ticker yet.")

    sentiment = context.get("sentiment")
    if sentiment:
        tone = "positive" if sentiment["score"] > 0.05 else (
            "negative" if sentiment["score"] < -0.05 else "roughly neutral")
        lines.append(
            f"FinBERT news sentiment on {sentiment['date']}: {sentiment['score']:+.2f} "
            f"({tone}, from {sentiment['article_count']} article(s))."
        )
    else:
        lines.append("No news sentiment has been scored for this ticker.")

    close = context.get("recent_close")
    if close:
        lines.append(f"Most recent stored close: ${close['close']:,.2f} on {close['date']}.")

    return "\n".join(lines)

def template_explanation(action: str, ticker: str, shares: float, price: float,
                         triggered_by_prediction: bool, context: dict) -> str:
    pass

    verb = "Bought" if action == "buy" else "Sold"
    lines = [f"{verb} {shares:g} share(s) of {ticker} at ${price:,.2f}."]

    prediction = context.get("prediction")
    if prediction:
        direction = prediction["direction"].lower()
        agrees = (action == "buy" and direction == "up") or (action == "sell" and direction == "down")
        lines.append(
            f"The {prediction['model']} model's latest signal was "
            f"{direction.upper()} for {prediction['target_date']} at "
            f"{prediction['confidence']:.0%} confidence (made {prediction['prediction_date']}), "
            f"so this trade {'follows' if agrees else 'runs against'} the signal."
        )
    else:
        lines.append("No model prediction exists for this ticker yet, so the trade "
                     "was not backed by a signal.")

    sentiment = context.get("sentiment")
    if sentiment:
        score = sentiment["score"]
        tone = "positive" if score > 0.05 else ("negative" if score < -0.05 else "roughly neutral")
        lines.append(
            f"FinBERT news sentiment was {score:+.2f} ({tone}) on {sentiment['date']}, "
            f"across {sentiment['article_count']} article(s)."
        )
    else:
        lines.append("No news sentiment has been scored for this ticker.")

    if triggered_by_prediction:
        lines.append("Marked as acting on the model signal.")

    return " ".join(lines)

def explain_trade(conn, transaction_id: int, action: str, ticker: str, shares: float,
                  price: float, triggered_by_prediction: bool) -> str | None:
    pass

    context = gather_context(conn, ticker)
    prompt = _build_prompt(action, ticker, shares, price, triggered_by_prediction, context)

    text = None
    if llm.is_configured():
        text = llm.try_complete(prompt, system=SYSTEM_PROMPT, max_tokens=220)

    if not text:
        text = template_explanation(action, ticker, shares, price,
                                    triggered_by_prediction, context)

    prediction_id = (context.get("prediction") or {}).get("id")
    sentiment_score = (context.get("sentiment") or {}).get("score")

    cur = conn.cursor()
    try:
        cur.execute(
            "INSERT INTO trade_explanations "
            "(transaction_id, explanation, prediction_id, sentiment_score) "
            "VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (transaction_id) DO UPDATE SET explanation = EXCLUDED.explanation",
            (transaction_id, text, prediction_id, sentiment_score),
        )
        conn.commit()
    except Exception:
        logger.exception("Failed to store trade explanation for transaction %s", transaction_id)
        conn.rollback()
    finally:
        cur.close()

    return text