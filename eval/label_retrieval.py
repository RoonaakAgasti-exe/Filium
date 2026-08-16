import json
import sys
from pathlib import Path
import psycopg2
from dotenv import load_dotenv

sys.path.insert(0, str(Path(__file__).parent.parent / "ingestion"))
from answer_query import embed_query, retrieve_chunks, DB_URL, TOP_K_RETRIEVE

load_dotenv()
EVAL_DIR = Path(__file__).parent
QUESTIONS_FILE = EVAL_DIR / "eval_questions.json"
LABELS_FILE = EVAL_DIR / "retrieval_labels.json"

def load_existing_labels() -> dict:
    if LABELS_FILE.exists():
        return json.loads(LABELS_FILE.read_text())
    return {}

def save_labels(labels: dict):
    LABELS_FILE.write_text(json.dumps(labels, indent = 2))

def label_question(client, conn, question_obj: dict, existing_labels: dict) -> dict:
    q_id = str(question_obj["id"])
    if q_id in existing_labels:
        print(f"Question {q_id} already labeled, skipping. (Delete its entry in "
              f"{LABELS_FILE.name} to relabel.)")
        return existing_labels[q_id]
    print(f"\n{'='*70}")
    print(f"Question {q_id} [{question_obj['ticker']}]: {question_obj['question']}")
    print(f"{'='*70}")
    query_embedding = embed_query(client, question_obj["question"])
    chunks = retrieve_chunks(conn, query_embedding, question_obj["ticker"], TOP_K_RETRIEVE)
    chunk_labels = []
    for i, chunk in enumerate(chunks, start = 1):
        print(f"\n--- Retrieved chunk {i}/{len(chunks)} (section: {chunk['section_label']}) ---")
        print(chunk["chunk_text"][:400])
        while True:
            resp = input("Relevant to the question? [y/n/skip-rest]: ").strip().lower()
            if resp in ("y", "n"):
                chunk_labels.append({
                    "chunk_id": chunk["chunk_id"],
                    "relevant": resp == "y",
                    "distance": chunk["distance"],
                })
                break
            elif resp in ("skip-rest", "s"):
                return {"question_id": question_obj["id"], "chunk_labels": chunk_labels, "partial": True}
            else:
                print("Enter y, n, or skip-rest")
    return {"question_id": question_obj["id"], "chunk_labels": chunk_labels, "partial": False}

def main():
    questions = json.loads(QUESTIONS_FILE.read_text())
    labels = load_existing_labels()
    client = OpenAI()
    conn = psycopg2.connect(DB_URL)
    try:
        for q in questions:
            result = label_question(client, conn, q, labels)
            labels[str(q["id"])] = result
            save_labels(labels)
            print(f"Saved. Progress: {len(labels)}/{len(questions)} questions labeled.")
    finally:
        conn.close()
    print(f"\nDone. Labels saved to {LABELS_FILE}")

if __name__ == "__main__":
    main()