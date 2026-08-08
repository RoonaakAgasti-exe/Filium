"""
Tests for transcript ingestion.

Transcripts reuse the filings table (`filing_type='transcript'`) so that
retrieval spans SEC filings and earnings calls without a second query path.
That reuse only holds if the writer agrees with the schema, and for a long
time it didn't: this module looked up `companies.id` and wrote
`filings.company_id`, neither of which exists, and imported two functions
no module defined — so it raised ImportError before any of that SQL could
even fail. Nothing caught it because nothing imported it.

So the first thing asserted here is simply that the module imports and
writes the columns the schema actually has. The rest pins the behaviour
that makes it safe to re-run.

No database; the connection and cursor are faked and their SQL recorded.
"""
import sys
from datetime import date
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "ingestion"))
sys.path.insert(0, str(Path(__file__).parent.parent / "backend"))

import fetch_transcript as ft  # noqa: E402


class FakeCursor:
    def __init__(self, width=384):
        self._width = width
        self._result = None
        self.statements = []
        self.params = []

    def execute(self, sql, params=None):
        self.statements.append(" ".join(sql.split()))
        self.params.append(params)
        if "pg_attribute" in sql:
            self._result = (self._width,)
        elif "SELECT EXISTS" in sql:
            self._result = (False,)
        elif "RETURNING id" in sql:
            self._result = (77,)
        else:
            self._result = None

    def fetchone(self):
        return self._result

    def close(self):
        pass

    def sql_containing(self, needle):
        return [s for s in self.statements if needle in s]


class FakeConn:
    def __init__(self, cur):
        self._cur = cur
        self.committed = False
        self.rolled_back = False

    def cursor(self, *a, **k):
        return self._cur

    def commit(self):
        self.committed = True

    def rollback(self):
        self.rolled_back = True

    def close(self):
        pass


@pytest.fixture
def wired(monkeypatch):
    """Points the module at a fake database and a deterministic embedder."""
    cur = FakeCursor()
    conn = FakeConn(cur)
    monkeypatch.setattr(ft.psycopg2, "connect", lambda *a, **k: conn)
    monkeypatch.setattr(ft.embeddings, "assert_matches_corpus", lambda c: None)
    monkeypatch.setattr(ft.embeddings, "dimension", lambda: 384)
    monkeypatch.setattr(ft.embeddings, "model_name", lambda: "BAAI/bge-small-en-v1.5")
    monkeypatch.setattr(ft, "embed_chunks", lambda chunks: [[0.0] * 384 for _ in chunks])
    return conn, cur


class TestIngestTranscriptWritesTheRealSchema:
    def test_the_filing_is_keyed_by_ticker_not_a_company_id(self, wired):
        # The exact bug this module shipped with: companies is keyed by
        # ticker and has no `id`, and filings has no `company_id`.
        conn, cur = wired

        ft.ingest_transcript("aapl", 2024, "Q1", "some transcript prose")

        inserts = cur.sql_containing("INSERT INTO filings")
        assert len(inserts) == 1
        assert "ticker" in inserts[0]
        assert "company_id" not in inserts[0]
        assert not cur.sql_containing("SELECT id FROM companies")

    def test_the_ticker_is_upper_cased_to_match_the_foreign_key(self, wired):
        conn, cur = wired

        ft.ingest_transcript("aapl", 2024, "Q1", "prose")

        company_insert = cur.params[cur.statements.index(
            cur.sql_containing("INSERT INTO companies")[0])]
        assert company_insert[0] == "AAPL"

    def test_chunks_record_the_embedding_model(self, wired):
        # Without this the corpus-consistency check has nothing to compare
        # against and a later provider switch fails inside pgvector instead.
        conn, cur = wired

        ft.ingest_transcript("AAPL", 2024, "Q1", "prose")

        chunk_insert = cur.sql_containing("INSERT INTO filing_chunks")[0]
        assert "embedding_model" in chunk_insert
        assert "BAAI/bge-small-en-v1.5" in [
            p[-1] for p in cur.params if p and len(p) == 6
        ]

    def test_it_commits(self, wired):
        conn, cur = wired

        ft.ingest_transcript("AAPL", 2024, "Q1", "prose")

        assert conn.committed is True


class TestQuarterHandling:
    @pytest.mark.parametrize("quarter,month", [("Q1", 3), ("Q2", 6), ("Q3", 9), ("Q4", 12)])
    def test_each_quarter_gets_its_own_filing_date(self, wired, quarter, month):
        # filing_date is NOT NULL and part of the uniqueness key, so two
        # quarters landing on one date would silently overwrite each other.
        conn, cur = wired

        ft.ingest_transcript("AAPL", 2024, quarter, "prose")

        filing_params = cur.params[cur.statements.index(
            cur.sql_containing("INSERT INTO filings")[0])]
        assert filing_params[2] == date(2024, month, 15).isoformat()

    def test_an_unknown_quarter_is_refused_before_any_sql(self, wired):
        conn, cur = wired

        with pytest.raises(ValueError, match="quarter must be one of"):
            ft.ingest_transcript("AAPL", 2024, "Q5", "prose")

        assert cur.statements == []


class TestReIngestIsIdempotent:
    def test_existing_chunks_are_cleared_before_reinserting(self, wired):
        # insert_filing upserts and returns the *existing* id, so without
        # this the re-run dies on the (filing_id, chunk_index) unique
        # constraint — which is exactly what a retry after a partial fetch,
        # or a re-embed after a model switch, would hit.
        conn, cur = wired

        ft.ingest_transcript("AAPL", 2024, "Q1", "prose")

        deletes = cur.sql_containing("DELETE FROM filing_chunks")
        assert len(deletes) == 1
        assert deletes[0].endswith("WHERE filing_id = %s")

    def test_the_delete_precedes_the_chunk_insert(self, wired):
        conn, cur = wired

        ft.ingest_transcript("AAPL", 2024, "Q1", "prose")

        first_delete = next(i for i, s in enumerate(cur.statements)
                            if "DELETE FROM filing_chunks" in s)
        first_insert = next(i for i, s in enumerate(cur.statements)
                            if "INSERT INTO filing_chunks" in s)
        assert first_delete < first_insert


class TestFailuresDoNotCommit:
    def test_a_mismatched_corpus_rolls_back_and_propagates(self, wired, monkeypatch):
        conn, cur = wired

        def boom(_conn):
            raise ft.embeddings.CorpusMismatch("different model")

        monkeypatch.setattr(ft.embeddings, "assert_matches_corpus", boom)

        with pytest.raises(ft.embeddings.CorpusMismatch):
            ft.ingest_transcript("AAPL", 2024, "Q1", "prose")

        assert conn.committed is False
        assert conn.rolled_back is True


class TestRecentQuarters:
    def test_returns_the_requested_count_newest_first(self):
        quarters = ft._recent_quarters(8)

        assert len(quarters) == 8
        assert len(set(quarters)) == 8

    def test_never_asks_for_a_quarter_that_has_not_closed(self):
        # Iterating the calendar year requests calls that haven't happened,
        # and each miss costs two HTTP round trips to find out.
        from datetime import datetime

        now = datetime.now()
        current = (now.year, (now.month - 1) // 3 + 1)

        for year, quarter in ft._recent_quarters(8):
            assert (year, int(quarter[1])) < current

    def test_it_walks_backwards_across_a_year_boundary(self):
        quarters = ft._recent_quarters(12)
        years = {y for y, _ in quarters}

        assert len(years) > 1
