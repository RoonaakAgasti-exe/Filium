# Evaluation Harness

This is what turns "the RAG system looks like it works" into a real,
defensible number for your README.

## Step 1: Write real questions

Open `eval_questions.json` and replace the 2 template questions with
30-50 real ones. To write good ones:

1. Pick 3-5 tickers you've already ingested
2. Actually read a chunk of each filing yourself
3. Write a question whose answer you already know is in there
4. Optionally jot the expected answer in the `notes` field so you can
   sanity-check the RAG system's answer later — but that's for your own
   reference, `label_retrieval.py` doesn't read that field

Mix easy questions (a fact stated in one sentence) with harder ones
(something that requires connecting two parts of the filing) — an eval
set that's all easy questions inflates your score and tells you nothing.

## Step 2: Label retrieval quality

```
python label_retrieval.py
```

For each question, it runs real retrieval and shows you each retrieved
chunk. You mark each one relevant or not. This is genuinely tedious —
that's expected, it's the part of the process most people skip, which is
exactly why doing it properly is worth something on a resume.

You can quit partway (Ctrl+C) and resume later — already-labeled
questions are skipped automatically.

## Step 3: Score retrieval precision

```
python score_retrieval.py
```

Gives you an overall precision@5 number plus a per-question breakdown —
useful for spotting whether failures cluster around a particular kind of
question (e.g. questions needing cross-section reasoning tend to score
worse than single-fact lookups).

## Step 4: Score answer faithfulness

```
python judge_faithfulness.py
```

Runs each question through the real RAG pipeline and asks an LLM to
judge whether the generated answer is fully backed by the retrieved
excerpts. **This is LLM-graded, not human-graded** — state that plainly
wherever you report this number. It's a legitimate, widely-used
technique, but conflating it with human evaluation overstates your
result.

## What to put in your README

Report at minimum:
- Retrieval precision@5 (human-labeled) — e.g. "84% (34/40 questions)"
- Faithfulness rate (LLM-graded) — e.g. "91%, LLM-as-judge"
- How many questions your eval set has, and how many tickers it spans

If precision or faithfulness comes out low, don't hide it — go back and
improve chunking, prompt wording, or reranking, then re-run. A documented
before/after improvement is a better story than a single suspiciously
perfect number.
