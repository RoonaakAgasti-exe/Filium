import argparse
import json
import re
from pathlib import Path
from bs4 import BeautifulSoup

PROCESSED_DATA_DIR = Path(__file__).parent.parent / "data" / "processed"
PROCESSED_DATA_DIR.mkdir(parents=True, exist_ok=True)
ITEM_PATTERN = re.compile(r"(item\s+\d+[a-c]?\.?\s*[-–—]?\s*[A-Z][A-Za-z ,&'/]{3,60})", re.IGNORECASE)

def strip_html(raw_html: str) -> str:
    soup = BeautifulSoup(raw_html, "html.parser")
    for tag in soup(["script", "style", "head", "meta", "link"]):
        tag.decompose()
    text = soup.get_text(separator="\n")
    text = re.sub(r"[ \t]+", " ", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()

def split_into_sections(text: str) -> list[dict]:
    matches = list(ITEM_PATTERN.finditer(text))
    if not matches:
        return [{"section_label": "Full Document", "text": text}]
    sections = []
    for i, match in enumerate(matches):
        start = match.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        label = match.group(1).strip()
        section_text = text[start:end].strip()
        if len(section_text) < 200:
            continue
        sections.append({"section_label": label, "text": section_text})
    return sections if sections else [{"section_label": "Full Document", "text": text}]

def clean_filing(raw_path: Path) -> Path:
    print(f"Cleaning {raw_path.name}...")
    raw_html = raw_path.read_text(encoding = "utf-8")
    clean_text = strip_html(raw_html)
    sections = split_into_sections(clean_text)
    print(f"  Found {len(sections)} sections")
    for s in sections[:5]:
        print(f"    - {s['section_label'][:60]} ({len(s['text'])} chars)")
    if len(sections) > 5:
        print(f"    ... and {len(sections) - 5} more")
    out_path = PROCESSED_DATA_DIR / raw_path.with_suffix(".json").name
    out_path.write_text(json.dumps(sections, indent = 2), encoding = "utf-8")
    print(f"  Saved to {out_path}")
    return out_path

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description = "Clean a raw SEC filing into sections")
    parser.add_argument("raw_file", help = "Path to raw HTML filing")
    args = parser.parse_args()
    clean_filing(Path(args.raw_file))