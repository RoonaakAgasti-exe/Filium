# Demo Script

A 2-3 minute walkthrough matters more than people expect for getting a
project actually looked at — a lot of reviewers watch the demo before
they read a line of code. Record with Loom or similar screen recorder.

## Before recording

- [ ] Ingest at least 2-3 real tickers so the demo has real variety
- [ ] Run the eval harness and have real numbers ready to reference out loud
- [ ] Let the scheduler run for a few real days beforehand so
      `/portfolio/history` and the backtest accuracy have actual data
      points, not an empty chart
- [ ] Do a full dry run once, unrecorded, to catch anything broken
- [ ] Close unrelated tabs/notifications — this is being recorded

## Script (~2.5 minutes)

**[0:00–0:15] Cold open — state the problem in one sentence**
"Reading a company's SEC filing to answer one question takes forever.
This finds the answer and shows you exactly where it came from."

**[0:15–0:50] Chat demo**
- Ask a real, specific question about an ingested company
- Let the answer render, then click a citation card to show it links to
  the actual filing
- One sentence: "This is RAG — retrieval-augmented generation. The
  model only answers from real retrieved text, not its own memory,
  which is why every claim is sourced."

**[0:50–1:20] Predictions dashboard**
- Look up a ticker, show the prediction + confidence
- Show the backtest track record — say the real accuracy number out loud
- One honest sentence about what the number means: "This is measured
  against real outcomes we logged in advance, not cherry-picked
  afterward" — this is the single most credible line in the whole demo

**[1:20–2:00] Portfolio / paper trading**
- Show current holdings and the value-over-time chart
- Make one buy or sell live on camera
- Show the portfolio-vs-S&P-500 comparison line — say the real number

**[2:00–2:20] Architecture, fast**
- Show the architecture diagram from the README for 5 seconds
- One sentence: "React frontend, FastAPI backend, Postgres with
  pgvector doing double duty as the relational store and the vector
  search index, deployed with Docker Compose."

**[2:20–2:30] Close**
- State what's honestly still rough or a known limitation (pick one —
  e.g. "the sentiment signal needs more training data to reliably beat
  the baseline, which the README covers")
- Link to the repo

## What NOT to do

- Don't narrate every line of code — the viewer can read the repo
- Don't hide a mediocre number — say it and say why it's still a fair
  test (see Phase 4/5's honesty notes in `eval/README.md` and `ml/README.md`)
- Don't over-script it into something stiff — reacting naturally to a
  real answer/prediction on screen reads better than a memorized line
