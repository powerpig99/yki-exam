#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from pathlib import Path


def norm(s: str) -> str:
    s = s.strip()
    s = s.replace("—", "-")
    s = re.sub(r"\s+", " ", s)
    return s


def extract_section(lines: list[str], section_title: str) -> list[str]:
    wanted = f"## {section_title}".strip()
    start = None
    for i, line in enumerate(lines):
        if line.strip() == wanted:
            start = i
            break
    if start is None:
        raise ValueError(f"section not found: {section_title!r}")

    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].startswith("## "):
            end = i
            break
    return lines[start:end]


def main() -> None:
    parser = argparse.ArgumentParser(description="Check how many source section lines are present in a target file.")
    parser.add_argument("--source", required=True)
    parser.add_argument("--section", required=True)
    parser.add_argument("--target", required=True)
    parser.add_argument("--ignore-prefix", action="append", default=["## "])
    args = parser.parse_args()

    source_path = Path(args.source).expanduser().resolve()
    target_path = Path(args.target).expanduser().resolve()

    source_lines = source_path.read_text(encoding="utf-8").splitlines()
    section_lines = extract_section(source_lines, args.section)

    wanted: list[str] = []
    for line in section_lines:
        if not line.strip():
            continue
        if any(line.startswith(p) for p in args.ignore_prefix):
            continue
        wanted.append(norm(line))

    target_text = norm(target_path.read_text(encoding="utf-8"))
    missing = [line for line in wanted if line not in target_text]

    print(f"source={source_path}")
    print(f"section={args.section}")
    print(f"target={target_path}")
    print(f"checked_lines={len(wanted)}")
    print(f"missing_lines={len(missing)}")
    if missing:
        print("missing_examples:")
        for line in missing[:30]:
            print(f"- {line}")


if __name__ == "__main__":
    main()
