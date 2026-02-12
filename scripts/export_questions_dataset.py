from __future__ import annotations

import json

from botapp.storage import get_questions_dataset_path, get_data_dir


def main() -> None:
    src_path = get_questions_dataset_path()
    dst_path = get_data_dir() / "questions_for_gpt.jsonl"

    pairs: list[dict] = []
    if src_path.exists():
        with src_path.open("r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except Exception:
                    continue
                q = obj.get("question")
                a = obj.get("answer")
                if not q or not a:
                    continue
                pairs.append({
                    "messages": [
                        {"role": "user", "content": q},
                        {"role": "assistant", "content": a},
                    ]
                })

    with dst_path.open("w", encoding="utf-8") as f:
        for item in pairs:
            f.write(json.dumps(item, ensure_ascii=False) + "\n")

    print(f"Exported {len(pairs)} pairs to {dst_path}")


if __name__ == "__main__":
    main()
