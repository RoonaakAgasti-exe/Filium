# alerts.py — API router for alerts.
pass

import logging

from fastapi import APIRouter, Body, Depends, HTTPException, Query, status
from psycopg2.extensions import connection as PgConnection

import alerts_engine
import emailer
import llm
import market_data
from auth import get_current_user_id
from db import get_conn
from models import AlertCreate, NaturalLanguageAlertCreate

logger = logging.getLogger("fincopilot.alerts")

router = APIRouter(prefix="/alerts", tags=["alerts"])

def _describe(rule_type: str, threshold: float | None) -> str:
    if rule_type == "prediction_flip":
        return "When the daily prediction flips direction"
    if rule_type == "price_move":
        return f"When the close moves more than {threshold:g}% in a day"
    if rule_type == "sentiment_below":
        return f"When news sentiment falls below {threshold:+.2f}"
    if rule_type == "sentiment_above":
        return f"When news sentiment rises above {threshold:+.2f}"
    if rule_type == "confidence_above":
        return f"When prediction confidence exceeds {threshold:.0%}"
    return rule_type

def _serialise(row: tuple) -> dict:
    threshold = float(row[3]) if row[3] is not None else None
    return {
        "id": row[0],
        "ticker": row[1],
        "rule_type": row[2],
        "threshold": threshold,
        "natural_language": row[4],
        "is_active": row[5],
        "last_fired_at": row[6].isoformat() if row[6] else None,
        "created_at": row[7].isoformat(),
        "description": _describe(row[2], threshold),
    }

_SELECT = (
    "SELECT id, ticker, rule_type, threshold, natural_language, is_active, "
    "       last_fired_at, created_at FROM alerts"
)

@router.get("/rule-types")
def rule_types():
    pass
    return {
        "rule_types": [
            {
                "value": r,
                "label": alerts_engine.RULE_DESCRIPTIONS[r],
                "requires_threshold": r != "prediction_flip",
                "threshold_hint": {
                    "price_move": "Percent, e.g. 5 for a 5% daily move",
                    "sentiment_below": "Between -1 and 1, e.g. -0.2",
                    "sentiment_above": "Between -1 and 1, e.g. 0.3",
                    "confidence_above": "Between 0 and 1, e.g. 0.75",
                }.get(r),
            }
            for r in alerts_engine.RULE_TYPES
        ],
        "natural_language_available": True,
        "natural_language_uses_llm": llm.is_configured(),
        "email_delivery_configured": emailer.is_configured(),
    }

@router.get("")
def list_alerts(user_id: int = Depends(get_current_user_id),
                conn: PgConnection = Depends(get_conn)):
    cur = conn.cursor()
    try:
        cur.execute(f"{_SELECT} WHERE user_id = %s ORDER BY created_at DESC", (user_id,))
        rows = cur.fetchall()
    finally:
        cur.close()
    return [_serialise(r) for r in rows]

def _create(conn: PgConnection, user_id: int, ticker: str, rule_type: str,
            threshold: float | None, natural_language: str | None) -> dict:
    if rule_type != "prediction_flip" and threshold is None:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST,
                            detail=f"A threshold is required for {rule_type} alerts")

    cur = conn.cursor()
    try:
        market_data.ensure_company(conn, ticker)
        cur.execute(
            "INSERT INTO alerts (user_id, ticker, rule_type, threshold, natural_language) "
            "VALUES (%s, %s, %s, %s, %s) "
            "RETURNING id, ticker, rule_type, threshold, natural_language, is_active, "
            "          last_fired_at, created_at",
            (user_id, ticker, rule_type, threshold, natural_language),
        )
        row = cur.fetchone()
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()

    return _serialise(row)

@router.post("", status_code=status.HTTP_201_CREATED)
def create_alert(payload: AlertCreate, user_id: int = Depends(get_current_user_id),
                 conn: PgConnection = Depends(get_conn)):
    return _create(conn, user_id, payload.ticker.strip().upper(), payload.rule_type,
                   payload.threshold, None)

@router.post("/natural", status_code=status.HTTP_201_CREATED)
def create_alert_from_text(payload: NaturalLanguageAlertCreate,
                           user_id: int = Depends(get_current_user_id),
                           conn: PgConnection = Depends(get_conn)):
    pass

    try:
        parsed = alerts_engine.parse_natural_language_alert(payload.text)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))

    alert = _create(conn, user_id, parsed["ticker"], parsed["rule_type"],
                    parsed["threshold"], payload.text)
    alert["interpreted_as"] = parsed["explanation"]
    return alert

@router.patch("/{alert_id}")
def update_alert(alert_id: int, is_active: bool = Body(embed=True),
                 user_id: int = Depends(get_current_user_id),
                 conn: PgConnection = Depends(get_conn)):
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE alerts SET is_active = %s WHERE id = %s AND user_id = %s "
            "RETURNING id, ticker, rule_type, threshold, natural_language, is_active, "
            "          last_fired_at, created_at",
            (is_active, alert_id, user_id),
        )
        row = cur.fetchone()
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()

    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found")
    return _serialise(row)

@router.delete("/{alert_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_alert(alert_id: int, user_id: int = Depends(get_current_user_id),
                 conn: PgConnection = Depends(get_conn)):
    cur = conn.cursor()
    try:
        cur.execute("DELETE FROM alerts WHERE id = %s AND user_id = %s", (alert_id, user_id))
        deleted = cur.rowcount
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()

    if not deleted:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="Alert not found")

@router.post("/check")
def check_alerts_now(force: bool = Query(default=True),
                     user_id: int = Depends(get_current_user_id),
                     conn: PgConnection = Depends(get_conn)):
    pass

    fired = alerts_engine.evaluate_all_alerts(conn, user_id=user_id, force=force)
    return {"fired": fired, "count": len(fired)}

@router.get("/events")
def list_events(limit: int = Query(default=50, ge=1, le=200), unread_only: bool = False,
                user_id: int = Depends(get_current_user_id),
                conn: PgConnection = Depends(get_conn)):
    sql = ["SELECT id, alert_id, ticker, message, is_read, emailed, created_at",
           "FROM alert_events WHERE user_id = %s"]
    params: list = [user_id]
    if unread_only:
        sql.append("AND is_read = FALSE")
    sql.append("ORDER BY created_at DESC LIMIT %s")
    params.append(limit)

    cur = conn.cursor()
    try:
        cur.execute("\n".join(sql), tuple(params))
        rows = cur.fetchall()
        cur.execute(
            "SELECT count(*) FROM alert_events WHERE user_id = %s AND is_read = FALSE",
            (user_id,),
        )
        unread_count = cur.fetchone()[0]
    finally:
        cur.close()

    return {
        "unread_count": unread_count,
        "events": [
            {
                "id": r[0], "alert_id": r[1], "ticker": r[2], "message": r[3],
                "is_read": r[4], "emailed": r[5], "created_at": r[6].isoformat(),
            }
            for r in rows
        ],
    }

@router.post("/events/read")
def mark_events_read(event_ids: list[int] | None = Body(default=None, embed=True),
                     user_id: int = Depends(get_current_user_id),
                     conn: PgConnection = Depends(get_conn)):
    pass

    cur = conn.cursor()
    try:
        if event_ids is not None:
            cur.execute(
                "UPDATE alert_events SET is_read = TRUE WHERE user_id = %s AND id = ANY(%s)",
                (user_id, event_ids),
            )
        else:
            cur.execute(
                "UPDATE alert_events SET is_read = TRUE WHERE user_id = %s AND is_read = FALSE",
                (user_id,),
            )
        updated = cur.rowcount
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()

    return {"marked_read": updated}