import argparse
import json
from collections import Counter
from pathlib import Path


DEFAULT_INPUT = "chatgpt_incremental.jsonl"
DEFAULT_OUTPUT = "chatgpt_incremental_deduped.jsonl"


def dedupe_jsonl(input_file=DEFAULT_INPUT, output_file=DEFAULT_OUTPUT):
    input_path = Path(input_file)
    output_path = Path(output_file)

    if not input_path.exists():
        raise FileNotFoundError(f"Input file not found: {input_path}")

    seen = {}
    total_rows = 0
    kept_rows = 0
    duplicate_rows = 0
    duplicate_ids = Counter()
    conflicting_ids = Counter()

    with input_path.open("r", encoding="utf-8") as src, output_path.open("w", encoding="utf-8") as dst:
        for line_no, line in enumerate(src, 1):
            if not line.strip():
                continue

            row = json.loads(line)
            total_rows += 1

            message_id = row.get("message_id")
            if not message_id:
                print(f"WARNING: line {line_no} has no message_id; keeping it.")
                dst.write(json.dumps(row, ensure_ascii=False) + "\n")
                kept_rows += 1
                continue

            if message_id in seen:
                duplicate_rows += 1
                duplicate_ids[message_id] += 1

                previous = seen[message_id]
                if row.get("content") != previous.get("content") or row.get("role") != previous.get("role"):
                    conflicting_ids[message_id] += 1
                    print(
                        "WARNING: duplicate message_id with different content/role "
                        f"at line {line_no}: {message_id}"
                    )
                continue

            seen[message_id] = row
            dst.write(json.dumps(row, ensure_ascii=False) + "\n")
            kept_rows += 1

    print("Deduplication complete.")
    print(f"  Input:  {input_path}")
    print(f"  Output: {output_path}")
    print(f"  Total rows:      {total_rows}")
    print(f"  Kept rows:       {kept_rows}")
    print(f"  Duplicate rows:  {duplicate_rows}")
    print(f"  Duplicate ids:   {len(duplicate_ids)}")
    print(f"  Conflicting ids: {len(conflicting_ids)}")

    return output_path, kept_rows


def main():
    parser = argparse.ArgumentParser(description="Deduplicate a JSONL chat export by message_id.")
    parser.add_argument("--input", default=DEFAULT_INPUT, help=f"Input JSONL file. Default: {DEFAULT_INPUT}")
    parser.add_argument("--output", default=DEFAULT_OUTPUT, help=f"Output JSONL file. Default: {DEFAULT_OUTPUT}")
    args = parser.parse_args()

    dedupe_jsonl(args.input, args.output)


if __name__ == "__main__":
    main()
