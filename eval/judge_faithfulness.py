
import json
import sys
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI

sys.path.insert(0, str(Path(__file__).parent.parent / "ingestion"))
from answer_query import answer_query, build_prompt, retrieve_chunks, embed_query, DB_URL, TOP_K_FINAL
import psycopg2

load_dotenv()
EVAL_DIR = Path(__file__).parent
QUESTIONS_FILE = EVAL_DIR / "eval_questions.json"
RESULTS_FILE = EVAL_DIR / "faithfulness_results.json"
JUDGE_MODEL = "gpt-4o-mini"
JUDGE_PROMPT_TEMPLATE = """You are grading whether an AI-generated answer is
faithful to a set of source excerpts — meaning every claim in the answer is
actually supported by the excerpts, with no invented facts.

Source excerpts:
{excerpts}

Generated answer:
{answer}

Respond with ONLY a JSON object, no other text:
{{
  "faithful": true or false,
  "unsupported_claims": ["list any specific claims not backed by the excerpts, empty list if none"],
  "reasoning": "one sentence explaining your judgment"
}}
"""

def judge_one(client: OpenAI, excerpts_text: str, answer_text: str) -> dict:
    prompt = JUDGE_PROMPT_TEMPLATE.format(excerpts = excerpts_text, answer = answer_text)
    response = client.chat.completions.create(
        model = JUDGE_MODEL,
        messages = [{"role": "user", "content": prompt}],
        temperature = 0,
        response_format = {"type": "json_object"},
    )
    return json.loads(response.choices[0].message.content)

def run_faithfulness_eval():
    questions = json.loads(QUESTIONS_FILE.read_text())
    client = OpenAI()
    conn = psycopg2.connect(DB_URL)
    results = []
    faithful_count = 0
    try:
        for q in questions:
            print(f"\nQuestion {q['id']}: {q['question']}")
            query_embedding = embed_query(client, q["question"])
            chunks = retrieve_chunks(conn, query_embedding, q["ticker"], TOP_K_FINAL)
            excerpts_text = build_prompt(q["question"], chunks).split("Question:")[0]
            rag_result = answer_query(q["question"], q["ticker"])
            answer_text = rag_result["answer"]
            judgment = judge_one(client, excerpts_text, answer_text)
            judgment["question_id"] = q["id"]
            judgment["answer_text"] = answer_text
            results.append(judgment)
            if judgment["faithful"]:
                faithful_count += 1
            print(f"  Faithful: {judgment['faithful']}")
            if not judgment["faithful"]:
                print(f"  Unsupported claims: {judgment['unsupported_claims']}")
    finally:
        conn.close()
    summary = {
        "faithfulness_rate": faithful_count / len(results) if results else 0.0,
        "num_questions": len(results),
        "grading_method": "LLM-as-judge (gpt-4o-mini), not human-graded",
        "details": results,
    }
    RESULTS_FILE.write_text(json.dumps(summary, indent = 2))
    print(f"\n{'='*50}")
    print(f"Faithfulness rate: {summary['faithfulness_rate']:.1%} "
          f"({faithful_count}/{len(results)}) — LLM-graded, see README caveat")
    print(f"Full results saved to {RESULTS_FILE}")

if __name__ == "__main__":
    run_faithfulness_eval()