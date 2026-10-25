"""Initialize missing local files from tracked, data-free examples; never overwrite user data."""
from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CONFIGS = {
    "projects.json": "projects.example.json",
    "model-config.json": "model-config.example.json",
    "app-settings.json": "app-settings.example.json",
}


def initialize_hub(root=ROOT):
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    created = []
    for name, example in CONFIGS.items():
        path = root / name
        if path.exists():
            continue
        text = (ROOT / "config" / example).read_text(encoding="utf-8-sig")
        json.loads(text)
        try:
            with path.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write(text)
            created.append(name)
        except FileExistsError:
            pass
    common = root / "COMMON.md"
    if not common.exists():
        try:
            with common.open("x", encoding="utf-8", newline="\n") as stream:
                stream.write((ROOT / "COMMON.md").read_text(encoding="utf-8"))
            created.append("COMMON.md")
        except FileExistsError:
            pass
    for directory in ("state", "tasks", "contexts"):
        (root / directory).mkdir(exist_ok=True)
    return {"created": created, "hub": str(root)}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--hub", type=Path, default=ROOT)
    args = parser.parse_args()
    print(json.dumps(initialize_hub(args.hub), ensure_ascii=False, indent=2))
