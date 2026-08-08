"""
routers/predictions.py — /predictions endpoints.

  GET /predictions/leaderboard          model-vs-model live accuracy
  GET /predictions/models               registered model versions
  GET /predictions/{ticker}             latest signal
  GET /predictions/{ticker}/history     recent signals, chronological
  GET /predictions/{ticker}/backtest    accuracy summary
  GET /predictions/{ticker}/calibration predicted probability vs. reality
"""

from fastapi import APIRouter, Depends, HTTPException, Query, status
from psycopg2.extensions import connection as PgConnection

import analytics
from db import get_conn
from models import BacktestSummary, PredictionResponse

router = APIRouter(prefix="/predictions", tags=["predictions"])

@router.get("/models")
def list_models(conn: PgConnection = Depends(get_conn)):
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT id, name, description, feature_set, trained_at, train_ticker, "
            "       test_accuracy, test_sharpe, is_active "
            "FROM model_versions ORDER BY id"
        )
        rows = cur.fetchall()
    finally:
        cur.close()

    return [
        {
            "id": r[0], "name": r[1], "description": r[2], "feature_set": r[3],
            "trained_at": r[4].isoformat() if r[4] else None,
            "train_ticker": r[5],
            "test_accuracy": float(r[6]) if r[6] is not None else None,
            "test_sharpe": float(r[7]) if r[7] is not None else None,
            "is_active": r[8],
        }
        for r in rows
    ]

@router.get("/leaderboard")
def leaderboard(ticker: str | None = None, conn: PgConnection = Depends(get_conn)):
    """
    Live accuracy per model version (PDF: "Leaderboard / model versioning").

    This is the ablation the README claims, measured on predictions that
    were logged before their outcome was known — not re-scored on the
    training split, which is where a flattering number would come from.
    """
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT id, name, description, feature_set, trained_at, "
            "       test_accuracy, test_sharpe, is_active FROM model_versions ORDER BY id"
        )
        models = cur.fetchall()
    finally:
        cur.close()

    board = []
    for m in models:
        summary = analytics.accuracy_summary(conn, ticker=ticker, model_version_id=m[0])
        calibration = analytics.prediction_calibration(conn, ticker=ticker, model_version_id=m[0])
        board.append({
            "model_version_id": m[0],
            "name": m[1],
            "description": m[2],
            "feature_set": m[3],
            "trained_at": m[4].isoformat() if m[4] else None,
            "test_accuracy": float(m[5]) if m[5] is not None else None,
            "test_sharpe": float(m[6]) if m[6] is not None else None,
            "is_active": m[7],
            "live_accuracy": summary["accuracy"],
            "resolved_predictions": summary["resolved_predictions"],
            "total_predictions": summary["total_predictions"],
            "brier_score": calibration["brier_score"],
            "expected_calibration_error": calibration["expected_calibration_error"],
        })

    board.sort(key=lambda r: (r["live_accuracy"] is None, -(r["live_accuracy"] or 0)))

    return {
        "ticker": ticker.upper() if ticker else None,
        "models": board,
        "note": (
            "Live accuracy counts only predictions whose outcome has since been "
            "resolved by the nightly backtest job."
        ),
    }

@router.get("/{ticker}", response_model=PredictionResponse)
def get_latest_prediction(ticker: str, model_version_id: int | None = None,
                          conn: PgConnection = Depends(get_conn)):
    sql = [
        "SELECT p.ticker, p.prediction_date, p.target_date, p.predicted_direction,",
        "       p.confidence, p.prob_up, p.actual_direction, m.name",
        "FROM predictions p",
        "JOIN model_versions m ON m.id = p.model_version_id",
        "WHERE p.ticker = %s",
    ]
    params: list = [ticker.upper()]

    if model_version_id is not None:
        sql.append("AND p.model_version_id = %s")
        params.append(model_version_id)
        sql.append("ORDER BY p.prediction_date DESC, p.id DESC")
    else:
        sql.append("ORDER BY m.is_active DESC, p.prediction_date DESC, p.id DESC")

    sql.append("LIMIT 1")

    cur = conn.cursor()
    try:
        cur.execute("\n".join(sql), tuple(params))
        row = cur.fetchone()
    finally:
        cur.close()

    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"No predictions found for {ticker.upper()} yet. "
                   f"The scheduler generates these nightly for watchlisted tickers.",
        )

    return PredictionResponse(
        ticker=row[0], prediction_date=row[1], target_date=row[2],
        predicted_direction=row[3], confidence=float(row[4]),
        prob_up=float(row[5]) if row[5] is not None else None,
        actual_direction=row[6], model_name=row[7],
    )

@router.get("/{ticker}/history")
def get_prediction_history(ticker: str, limit: int = 30, model_version_id: int | None = None,
                           conn: PgConnection = Depends(get_conn)):
    """Recent predictions for a ticker, oldest first — powers the accuracy-over-time chart."""
    limit = max(1, min(limit, 200))

    sql = [
        "SELECT p.ticker, p.prediction_date, p.target_date, p.predicted_direction,",
        "       p.confidence, p.prob_up, p.actual_direction, m.name",
        "FROM predictions p",
        "JOIN model_versions m ON m.id = p.model_version_id",
        "WHERE p.ticker = %s",
    ]
    params: list = [ticker.upper()]
    if model_version_id is not None:
        sql.append("AND p.model_version_id = %s")
        params.append(model_version_id)
    sql.append("ORDER BY p.prediction_date DESC, p.id DESC LIMIT %s")
    params.append(limit)

    cur = conn.cursor()
    try:
        cur.execute("\n".join(sql), tuple(params))
        rows = cur.fetchall()
    finally:
        cur.close()

    if not rows:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"No predictions found for {ticker.upper()}")

    rows = list(reversed(rows))
    return [
        {
            "ticker": r[0],
            "prediction_date": str(r[1]),
            "target_date": str(r[2]),
            "predicted_direction": r[3],
            "confidence": float(r[4]),
            "prob_up": float(r[5]) if r[5] is not None else None,
            "actual_direction": r[6],
            "model_name": r[7],
            "correct": (r[3] == r[6]) if r[6] else None,
        }
        for r in rows
    ]

@router.get("/{ticker}/backtest", response_model=BacktestSummary)
def get_backtest_summary(ticker: str, model_version_id: int | None = None,
                         conn: PgConnection = Depends(get_conn)):
    summary = analytics.accuracy_summary(conn, ticker=ticker, model_version_id=model_version_id)

    if summary["total_predictions"] == 0:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND,
                            detail=f"No predictions found for {ticker.upper()}")

    return BacktestSummary(ticker=ticker.upper(), **summary)

@router.get("/{ticker}/calibration")
def get_calibration(ticker: str, bins: int = Query(default=5, ge=2, le=20),
                    model_version_id: int | None = None,
                    conn: PgConnection = Depends(get_conn)):
    """
    Calibration curve (PDF: "Prediction confidence and calibration view").

    A well-calibrated model that says 70% should be right about 70% of the
    time. That's a stricter and more honest claim than raw directional
    accuracy, which a model can hit by always predicting the majority class.
    """
    return analytics.prediction_calibration(
        conn, ticker=ticker, model_version_id=model_version_id, num_bins=bins
    )
