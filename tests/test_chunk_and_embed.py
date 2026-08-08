"""
Tests for the ingestion side of provider selection.

The vector column's width has to match whatever model embedded the
corpus, and that model is a runtime setting no .sql file can read. So
ingestion sets the width itself — but only while the table is empty,
which is the whole safety argument. These tests pin down both halves:
that a fresh database is adjusted silently, and that a populated one is
never altered out from under its data.

No database here; the cursor is faked and its SQL recorded.
"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent / "ingestion"))

import chunk_and_embed as cae  # noqa: E402


class FakeCursor:
    """
    Answers the two questions ensure_embedding_width asks — the declared
    width, and whether any rows exist — and records every statement so a
    test can assert that no ALTER was issued.
    """

    def __init__(self, width, has_rows=False):
        self._width = width
        self._has_rows = has_rows
        self._result = None
        self.statements = []

    def execute(self, sql, params=None):
        self.statements.append(" ".join(sql.split()))
        if "pg_attribute" in sql:
            self._result = (self._width,)
        elif "SELECT EXISTS" in sql:
            self._result = (self._has_rows,)
        else:
            self._result = None

    def fetchone(self):
        return self._result

    def close(self):
        pass

    @property
    def altered(self):
        return [s for s in self.statements if s.startswith("ALTER TABLE")]


class TestWidthOnAFreshDatabase:
    def test_narrows_the_column_to_the_local_model(self):
        # The keyless path: schema.sql ships OpenAI's 1536 and the first
        # local ingestion has to bring it down to 384, or every insert
        # fails on a dimension error.
        cur = FakeCursor(width=1536, has_rows=False)

        cae.ensure_embedding_width(cur, 384)

        assert cur.altered == [
            "ALTER TABLE filing_chunks ALTER COLUMN embedding TYPE vector(384)"
        ]

    def test_widens_the_column_for_openai(self):
        cur = FakeCursor(width=384, has_rows=False)

        cae.ensure_embedding_width(cur, 1536)

        assert cur.altered == [
            "ALTER TABLE filing_chunks ALTER COLUMN embedding TYPE vector(1536)"
        ]

    def test_sets_a_width_on_an_unconstrained_column(self):
        # atttypmod is -1 when the column was declared as bare `vector`.
        cur = FakeCursor(width=None, has_rows=False)

        cae.ensure_embedding_width(cur, 384)

        assert len(cur.altered) == 1


class TestWidthWhenAlreadyCorrect:
    def test_does_nothing_when_the_width_already_matches(self):
        cur = FakeCursor(width=384, has_rows=False)

        cae.ensure_embedding_width(cur, 384)

        assert cur.altered == []

    def test_a_matching_width_is_not_even_checked_for_rows(self):
        # Rewriting a large, correct table would be a pointless cost on
        # every single ingestion run.
        cur = FakeCursor(width=384, has_rows=True)

        cae.ensure_embedding_width(cur, 384)

        assert cur.altered == []
        assert not any("SELECT EXISTS" in s for s in cur.statements)


class TestWidthOnAPopulatedDatabase:
    def test_refuses_to_alter_a_table_that_has_rows(self):
        cur = FakeCursor(width=1536, has_rows=True)

        with pytest.raises(RuntimeError) as exc:
            cae.ensure_embedding_width(cur, 384)

        assert cur.altered == []
        assert "TRUNCATE filing_chunks" in str(exc.value)

    def test_the_error_offers_both_ways_out(self):
        # Re-embed, or go back to the model that built the corpus. Being
        # told only "wrong width" leaves someone with a broken deployment
        # and no next step.
        cur = FakeCursor(width=1536, has_rows=True)

        with pytest.raises(RuntimeError) as exc:
            cae.ensure_embedding_width(cur, 384)

        message = str(exc.value)
        assert "re-run this ingestion" in message
        assert "EMBEDDING_PROVIDER" in message


class TestCurrentEmbeddingWidth:
    def test_reads_the_declared_width(self):
        assert cae.current_embedding_width(FakeCursor(width=1536)) == 1536

    def test_an_unconstrained_column_reads_as_none(self):
        # pg_attribute stores -1 for "no type modifier", which must not be
        # mistaken for a real width.
        assert cae.current_embedding_width(FakeCursor(width=-1)) is None
