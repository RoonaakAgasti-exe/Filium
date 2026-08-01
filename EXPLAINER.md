# FinCopilot — AI Financial Research, Prediction & Paper Trading Assistant

> Ask a company a question. Get an answer backed by their real filings. See what the model thinks the stock does tomorrow. Then put fake money where the model's mouth is, and track whether it actually works.

---

## 1. What is this, explained really simply

Imagine you want to know what a company like Nvidia said about a risk to their business. Normally you'd have to:
1. Find their giant, boring, 100-page legal report (called a "10-K")
2. Read through it yourself to find the one paragraph that matters

**FinCopilot does that for you.** You type a question in plain English, like *"What did Nvidia say about chip export risks?"* — and it reads the real report, finds the right paragraph, and answers you in normal language. It even shows you exactly where it found the answer, so you can double check it's not making things up.

FinCopilot also tries to guess whether a stock will go up or down tomorrow, using two clues:
- **How the stock has been moving lately** (like noticing a ball rolling downhill vs. uphill)
- **How positive or negative the company's recent news and reports sound** (like noticing if someone sounds worried or confident when they talk)

And it keeps score of its own guesses, so you can see "this model was right 6 out of 10 times last month" instead of just trusting it blindly.

Finally, FinCopilot gives you a **pretend wallet with fake money**. You can "buy" and "sell" real stocks at their real current prices — nothing real happens to actual money — so you can test whether following the model's predictions would have actually made you richer or poorer.

---

## 2. What it actually does (the features)

| Feature | In plain words |
|---|---|
| **Ask questions about a company** | Type a question, get an answer with a source you can click to verify |
| **Stock direction prediction** | Shows a daily up/down guess with a confidence score |
| **Backtest scoreboard** | Shows how accurate past predictions actually were — no cherry-picking |
| **Watchlist** | Save companies you care about so the app tracks them automatically |
| **Chat history** | See every question you've asked and the answers you got |
| **Virtual wallet** | Starts you off with fake cash (e.g. $100,000) |
| **Buy/Sell trades** | Place simulated trades at real, live market prices |
| **Portfolio view** | See what you own, what it's worth, and your profit/loss |
| **"Trade the prediction"** | One click to act on a model's prediction, tracked separately from your own manual trades |
| **Performance chart** | Your fake portfolio's growth vs. just holding the overall market |

---

## 3. How it works (the simple version)

Think of it like three people working in the same office: a librarian, a weather forecaster, and a bank teller.

**The Librarian (the "ask questions" part):**
1. Every company report gets read once, chopped into small paragraphs, and each paragraph gets a "meaning fingerprint" (a list of numbers that represents what that paragraph is about).
2. When you ask a question, your question also gets a "meaning fingerprint."
3. The librarian compares your question's fingerprint to every paragraph's fingerprint and pulls out the ones that match best — even if you didn't use the exact same words as the report.
4. Those matching paragraphs get handed to an AI writer, who reads them and writes you a clean answer, only using what's actually in those paragraphs (not making things up).

**The Weather Forecaster (the "predict the stock" part):**
1. It looks at how the stock price has moved over recent days — is it climbing, falling, jumping around?
2. It also checks how the company's recent reports and news "sound" — worried, confident, neutral — using a tool trained to read emotional tone in financial text.
3. It combines both clues into one guess: up or down tomorrow, with a confidence percentage.
4. Every day, it checks yesterday's guess against what actually happened and logs it — this is how you get an honest accuracy score instead of a made-up one.

**The Bank Teller (the "wallet" part):**
1. You start with pretend money in a pretend account.
2. When you "buy" a stock, the teller checks the real current price (from a real trading service called Alpaca, but using their practice-mode account, so no real money is ever involved) and subtracts that cost from your pretend cash, adding the shares to your pretend portfolio.
3. Every day, the teller writes down what your whole portfolio is worth, so later you can draw a chart of it growing or shrinking over time.
4. If you traded because the Weather Forecaster told you to, the teller makes a little note of that — so later you can check: "did listening to the model actually make me more money than not listening to it?"

**The Office (how it's all connected):**
- All the reports, paragraphs, fingerprints, prices, wallets, and trades live in one filing cabinet — a **database**.
- A **backend** (the "receptionist") takes requests from the app and fetches or updates the right information in the filing cabinet.
- A **frontend** (the actual website you see and click on) is what you interact with.

---

## 4. How it's built (the technical version)

```
┌───────────────────────────────────────────────────────────────┐
│                          FRONTEND                                │
│      (React — chat page, dashboard, portfolio, trade panel)         │
└───────────────────────────┬─────────────────────────────────────┘
                             │ HTTP requests
┌───────────────────────────▼─────────────────────────────────────┐
│                          BACKEND                                  │
│                 (FastAPI — Python web server)                       │
│  /query  /predictions  /watchlist  /auth  /trade  /portfolio          │
└──────┬───────────────────────────────┬──────────────────┬──────────┘
       │                               │                    │
┌──────▼──────────┐         ┌─────────▼─────────┐  ┌───────▼────────┐
│    DATABASE       │         │   AI SERVICES        │  │  BROKERAGE API    │
│  (PostgreSQL +      │         │  - Embedding model      │  │  (Alpaca Paper     │
│   pgvector)          │         │  - LLM (answers)          │  │   Trading — fake     │
│  filings, chunks,      │         │  - FinBERT (sentiment)      │  │   money, real prices)  │
│  prices, predictions,    │         │  - LSTM (prediction)          │  └────────────────────┘
│  wallets, trades,          │         └───────────────────────────┘
│  portfolio_snapshots         │
└─────────┬───────────────────┘
          │
┌─────────▼───────────┐
│   DATA INGESTION       │
│  (scheduled scripts)     │
│  pulls from SEC EDGAR,     │
│  news APIs, price APIs       │
└─────────────────────────────┘
```

### Tech stack

| Layer | Tool | Why |
|---|---|---|
| Frontend | React / Next.js | Standard, job-relevant, good charting libraries |
| Backend | FastAPI (Python) | Fast to build, plays nicely with ML code |
| Database | PostgreSQL + pgvector extension | One database for normal data *and* AI "fingerprints" — simpler than running two separate databases |
| Embeddings | OpenAI `text-embedding-3-small` or open-source BGE | Turns text into searchable "meaning fingerprints" |
| LLM | GPT-4o-mini or Claude Haiku | Writes the final answer cheaply |
| Sentiment | FinBERT (pretrained, no training needed) | Reads emotional tone of financial text |
| Prediction model | LSTM (extended with sentiment features) | Learns patterns in price + tone over time |
| Trading | Alpaca Paper Trading API | Real market prices, real order mechanics, zero real money and zero legal/licensing risk |
| Deployment | Docker Compose → Railway or Fly.io | Free-tier friendly, easy to demo |

---

## 5. How to build it, step by step

Build it in this order. Each step should work on its own before you move to the next — don't build everything at once.

### Step 1 — Get the raw data
- Write a script that downloads a company's latest 10-K filing from SEC EDGAR (free, public, no login needed)
- Clean it up (remove HTML junk, headers, footers)
- Test it on 3-4 companies until it reliably produces clean text

### Step 2 — Set up the database
- Install PostgreSQL, add the `pgvector` extension
- Create tables for companies, filings, filing chunks, prices, and predictions
- Chop each filing into small chunks, turn each into a "fingerprint" (embedding), and save it

### Step 3 — Build the question-answering part
- Write a simple script (no website yet): take a hardcoded question, find matching chunks, ask the AI to answer using them, print the result
- Keep tweaking this until the answers are genuinely good

### Step 4 — Check your work
- Write 30-50 test questions with answers you already know are correct
- Score the system: did it find the right paragraphs? Did it answer using only real info?
- This step is what turns "looks cool" into "provably works"

### Step 5 — Build the prediction model
- Add a sentiment score for each company using FinBERT
- Feed price history + sentiment into the LSTM model
- Compare accuracy with vs. without the sentiment feature

### Step 6 — Track prediction accuracy over time
- Every day, save what the model predicted
- Every day, check what actually happened and log it
- This is what makes your accuracy claims honest instead of made up

### Step 7 — Connect everything with a backend
- Turn Steps 3 and 5 into FastAPI endpoints
- Add login/accounts so users can save a watchlist

### Step 8 — Build the website
- Chat page first (it's the most impressive part to show people)
- Dashboard page second (charts of predictions and accuracy)

### Step 9 — Add the paper trading wallet
- Sign up for a free Alpaca account and switch it to paper (practice) trading mode
- Build the `wallets`, `holdings`, `transactions`, and `portfolio_snapshots` tables
- Connect buy/sell buttons to Alpaca's paper order API
- Add a daily job that snapshots every user's total portfolio value
- Build the portfolio page and a "your portfolio vs. the overall market" chart

### Step 10 — Put it online
- Package everything with Docker
- Deploy to a free hosting service so anyone can try it with a link

### Step 11 — Explain it to others
- Write documentation (like this one!) with a diagram and real numbers showing how well it works

---

## 6. Database tables (all of them, together)

```sql
companies (ticker PK, name, sector, cik)

filings (id PK, ticker FK, filing_type, filing_date, source_url, raw_text)

filing_chunks (id PK, filing_id FK, chunk_text, embedding vector(1536), 
               section_label, chunk_index)

price_history (ticker FK, date, open, high, low, close, volume)

sentiment_scores (ticker FK, date, source, score, raw_text_snippet)

predictions (id PK, ticker FK, prediction_date, target_date, 
             predicted_direction, confidence, actual_direction)

users (id PK, email, hashed_password)

watchlists (user_id FK, ticker FK)

query_history (id PK, user_id FK, query_text, response_text, 
               cited_chunk_ids, timestamp)

wallets (user_id FK, cash_balance, created_at)

holdings (id PK, user_id FK, ticker FK, shares, avg_cost_basis)

transactions (id PK, user_id FK, ticker FK, action ('buy'/'sell'), 
              shares, price_per_share, executed_at, 
              triggered_by_prediction BOOLEAN)

portfolio_snapshots (id PK, user_id FK, date, total_value, 
                      cash_value, holdings_value)
```

---

## 7. Backend endpoints (all of them, together)

```
POST /auth/register, /auth/login          — accounts
GET  /companies/{ticker}                   — company info
POST /query                                 — ask a question, get a sourced answer
GET  /predictions/{ticker}                  — latest prediction + confidence
GET  /predictions/{ticker}/backtest         — historical prediction accuracy
GET  /watchlist, POST /watchlist            — tracked tickers
GET  /query-history                         — past questions and answers
POST /wallet/deposit                        — top up / reset virtual cash
POST /trade/buy                             — buy shares (via Alpaca paper API)
POST /trade/sell                            — sell shares (via Alpaca paper API)
GET  /portfolio                             — current holdings + profit/loss
GET  /portfolio/history                     — daily value over time, for charting
GET  /portfolio/vs-benchmark                — your return vs. the overall market
```

---

## 8. Words you'll hear a lot, explained simply

| Word | What it means |
|---|---|
| **RAG** | "Retrieval-Augmented Generation" — fancy way of saying "look up real info first, then write an answer using it" |
| **Embedding** | Turning text into a list of numbers that represents its meaning |
| **Vector database** | A filing cabinet that can search by "meaning" instead of exact words |
| **LLM** | The AI model that writes the actual sentences you read |
| **Backtesting** | Checking old predictions (or old trades) against what really happened, to see if they were actually good |
| **Sentiment analysis** | Teaching a computer to notice if text sounds positive, negative, or neutral |
| **Paper trading** | "Practice mode" trading — real prices, fake money, zero risk |
| **Portfolio** | Everything you currently own — cash plus stocks |
| **Benchmark** | A standard to compare against — usually "what if I'd just bought the whole market" |
| **API** | A way for two computer programs to talk to each other |

---

## 9. Honest limitations (worth saying out loud)

- Predicting stock prices is genuinely hard — this project is about showing solid engineering and honest evaluation, not about actually beating the market
- The AI can still make mistakes even with real sources — that's why every answer shows its source, so you can check it yourself
- This is **paper trading only** — no real money, no real brokerage account, no real financial risk to anyone, and it should never be presented as real investment advice
- Free data sources have rate limits, so pulling data for many companies at once will be slow

---

## 10. What's next (future ideas)

- Add more filing types (earnings call transcripts, 8-Ks for breaking news)
- Compare multiple companies side by side
- Let users ask follow-up questions (multi-turn conversation, not just one-off)
- Add a leaderboard if multiple people use it, ranked by paper portfolio return
