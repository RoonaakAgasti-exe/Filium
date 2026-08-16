import json
from pathlib import Path

EVAL_DIR = Path(__file__).parent
LABELS_FILE = EVAL_DIR / "retrieval_labels.json"

def precision_at_k(chunk_labels: list[dict], k: int | None = None) -> float:
    subset = chunk_labels[:k] if k is not None else chunk_labels
    if not subset:
        return 0.0
    relevant_count = sum(1 for c in subset if c["relevant"])
    return relevant_count / len(subset)

def score_all(labels: dict, k: int = 5) -> dict:
    per_question = {}
    all_precisions = []
    for q_id, entry in labels.items():
        chunk_labels = entry["chunk_labels"]
        if not chunk_labels:
            continue
        p = precision_at_k(chunk_labels, k)
        per_question[q_id] = p
        all_precisions.append(p)
    overall = sum(all_precisions) / len(all_precisions) if all_precisions else 0.0
    return {
        "overall_precision_at_k": overall,
        "k": k,
        "num_questions_scored": len(all_precisions),
        "per_question": per_question,
    }

def main():
    if not LABELS_FILE.exists():
        print(f"No labels found at {LABELS_FILE}. Run label_retrieval.py first.")
        return
    labels = json.loads(LABELS_FILE.read_text())
    results = score_all(labels, k = 5)
    print(f"Retrieval Precision@{results['k']}")
    print(f"  Overall: {results['overall_precision_at_k']:.1%}")
    print(f"  Based on {results['num_questions_scored']} labeled questions\n")
    print("Per-question breakdown:")
    for q_id, p in results["per_question"].items():
        print(f"  Question {q_id}: {p:.1%}")

if __name__ == "__main__":
    main()