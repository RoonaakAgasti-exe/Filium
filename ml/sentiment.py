import argparse
import os
import sys
import psycopg2
from dotenv import load_dotenv
from transformers import AutoTokenizer, AutoModelForSequenceClassification
import torch

load_dotenv()
DB_URL = os.getenv("DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/filium")
MODEL_NAME = "ProsusAI/finbert"
LABELS = ["positive", "negative", "neutral"]
BATCH_SIZE = 16
MAX_FILING_CHUNKS = 40

class FinBertScorer:
    def __init__(self):
        self.tokenizer = AutoTokenizer.from_pretrained(MODEL_NAME)
        self.model = AutoModelForSequenceClassification.from_pretrained(MODEL_NAME)
        self.model.eval()

    def score_text(self, text:str) -> float:
        return self.score_batch([text])[0]

    def score_batch(self, texts:list[str]) -> list[float]:
        out: list[float] = []
        for i in range(0, len(texts), BATCH_SIZE):
            batch = texts[i: i + BATCH_SIZE]
            inputs = self.tokenizer(batch, return_tensors = "pt", truncation = True, max_length = 512, padding = True)
            with torch.no_grad():
                logits = self.model(**inputs).logits
            probs = torch.softmax(logits, dim = -1).tolist()
            for row in probs:
                by_label = dict(zip(LABELS, row))
                out.append(by_label["positive"] - by_label["negative"])
        return out

def aggregate_daily_sentiment(scores:list[float]) -> float:
    if not scores:
        return 0.0
    return sum(scores) / len(scores)

def fetch_news_items(conn, ticker:str, rescore:bool) -> list[dict]:
    where = "WHERE ticker = %s"
    if not rescore:
        where += " AND sentiment_score IS NULL"
    cur = conn.cursor()
    try:
        cur.execute(
            f"SELECT id, published_date, headline, summary FROM news_articles {where} "
            f"ORDER BY published_date DESC, id DESC", ticker)
        rows = cur.fetchall()
    finally:
        cur.close()
    items = []
    for row_id, published, headline, summary in rows:
        text = headline if not summary else f"{headline}. {summary}"
        items.append({"id":row_id, "date":published, "text":text[:2000]})
    return items

def fetch_filing_items(conn, ticker:str) -> list[dict]:
    cur = conn.cursor()
    try:
        cur.execute(
            """
            SELECT fc.id, f.filing_date, fc.chunk_text
            FROM filing_chunks fc
            JOIN filings f ON f.id = fc.filing_id
            WHERE f.ticker = %s
            ORDER BY f.filing_date DESC, fc.chunk_index
            """, ticker)
        rows = cur.fetchall()
    finally:
        cur.close()
    per_date: dict = {}
    items = []
    for row_id, filing_date, text in rows:
        seen = per_date.get(filing_date, 0)
        if seen >= MAX_FILING_CHUNKS:
            continue
        per_date[filing_date] = seen + 1
        items.append({"id":row_id, "date":filing_date, "text":text})
    return items

def existing_news_scores(conn, ticker:str) -> dict:
    cur = conn.cursor()
    try:
        cur.execute(
            "SELECT published_date, headline, sentiment_score FROM news_articles "
            "WHERE ticker = %s AND sentiment_score IS NOT NULL", ticker)
        rows = cur.fetchall()
    finally:
        cur.close()
    by_date: dict = {}
    for published, headline, score in rows:
        by_date.setdefault(published, []).append({"score":float(score), "text":headline})
    return by_date

def store_article_scores(conn, scored:list[tuple[float, int]]) -> None:
    if not scored:
        return
    cur = conn.cursor()
    try:
        cur.executemany(
            "UPDATE news_articles SET sentiment_score = %s WHERE id = %s", scored)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()

def store_daily_scores(conn, ticker:str, source:str, by_date:dict) -> int:
    cur = conn.cursor()
    written = 0
    try:
        for d, entries in sorted(by_date.items()):
            scores = [e["score"] for e in entries]
            daily = aggregate_daily_sentiment(scores)
            cur.execute(
                """
                INSERT INTO sentiment_scores
                    (ticker, date, source, score, article_count, raw_text_snippet)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (ticker, date, source) DO UPDATE SET
                    score = EXCLUDED.score,
                    article_count = EXCLUDED.article_count,
                    raw_text_snippet = EXCLUDED.raw_text_snippet
                """, (ticker, d, source, daily, len(scores), entries[0]["text"][:200]))
            written += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        cur.close()
    return written

def watchlisted_tickers(conn) -> list[str]:
    cur = conn.cursor()
    try:
        cur.execute("SELECT DISTINCT ticker FROM watchlists ORDER BY ticker")
        return [r[0] for r in cur.fetchall()]
    finally:
        cur.close()

def run_sentiment_scoring(conn, ticker:str, source:str, scorer:FinBertScorer, rescore:bool = False) -> dict:
    ticker = ticker.upper()
    if source == "news":
        items = fetch_news_items(conn, ticker, rescore)
    else:
        items = fetch_filing_items(conn, ticker)
    print(f"{ticker} ({source}):{len(items)} new text(s) to score...")
    by_date: dict = {}
    if source == "news" and not rescore:
        by_date = existing_news_scores(conn, ticker)
    if items:
        scores = scorer.score_batch([i["text"] for i in items])
        if source == "news":
            store_article_scores(conn, [(s, i["id"]) for s, i in zip(scores, items)])
        for item, score in zip(items, scores):
            by_date.setdefault(item["date"], []).append({"score":score, "text":item["text"]})
    if not by_date:
        hint = ("ingestion/fetch_news.py" if source == "news"
                else "ingestion/fetch_filing.py + chunk_and_embed.py")
        print(f"  nothing to score — run {hint} for {ticker} first")
        return {"ticker":ticker, "source":source, "scored":0, "days":0}
    days = store_daily_scores(conn, ticker, source, by_date)
    for d in sorted(by_date)[-5:]:
        daily = aggregate_daily_sentiment([e["score"] for e in by_date[d]])
        print(f"  {d}:{daily:+.3f} (from {len(by_date[d])} text(s))")
    if days > 5:
        print(f"  ... plus {days - 5} earlier day(s)")
    return {"ticker":ticker, "source":source, "scored":len(items), "days":days}

def main():
    parser = argparse.ArgumentParser(description = "Score sentiment for a ticker's stored text")
    parser.add_argument("--ticker", action = "append", default = [], help = "Ticker to score; repeatable")
    parser.add_argument("--watchlisted", action = "store_true", help = "Score every ticker on any user's watchlist")
    parser.add_argument("--source", choices = ["news", "filing"], default = "news")
    parser.add_argument("--rescore", action = "store_true", help = "Re-score articles that already have a stored score")
    args = parser.parse_args()
    conn = psycopg2.connect(DB_URL)
    try:
        tickers = [t.upper() for t in args.ticker]
        if args.watchlisted:
            tickers = sorted(set(tickers) | set(watchlisted_tickers(conn)))
        if not tickers:
            parser.error("Pass --ticker at least once, or --watchlisted")
        scorer = FinBertScorer()
        failures = 0
        for ticker in tickers:
            try:
                run_sentiment_scoring(conn, ticker, args.source, scorer, args.rescore)
            except Exception as exc:
                failures += 1
                print(f"{ticker}:FAILED — {exc}", file = sys.stderr)
        if failures:
            sys.exit(1)
    finally:
        conn.close()

if __name__ == "__main__":
    main()