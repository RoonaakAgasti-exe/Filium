import os
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
for sub in ("backend", "ingestion", "ml"):
    path = os.path.join(REPO_ROOT, sub)
    if path not in sys.path:
        sys.path.insert(0, path)
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)