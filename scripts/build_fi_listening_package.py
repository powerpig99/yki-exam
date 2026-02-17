#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
from dataclasses import dataclass
from pathlib import Path


ENTRY_RE = re.compile(r"^####\s+((?:SPK|WRT)-[A-Z]+-\d{2}[A-Z]?)(?:\s*-\s*(.+?))?\s*$")


@dataclass
class Entry:
    id: str
    title: str
    lines: list[str]


def read_lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def clean_md(s: str) -> str:
    s = s.strip()
    s = s.replace("**", "")
    s = re.sub(r"^\d+\.\s*", "", s)
    if s.startswith("- "):
        s = s[2:].strip()
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def extract_section(lines: list[str], section_title: str) -> list[str]:
    wanted = f"## {section_title}".strip()
    start = None
    for i, line in enumerate(lines):
        if line.strip() == wanted:
            start = i
            break
    if start is None:
        raise ValueError(f"Section not found: {section_title}")
    end = len(lines)
    for i in range(start + 1, len(lines)):
        if lines[i].startswith("## "):
            end = i
            break
    return lines[start:end]


def split_entries(lines: list[str]) -> list[Entry]:
    out: list[Entry] = []
    cur_id = None
    cur_title = None
    cur_lines: list[str] = []
    for line in lines:
        m = ENTRY_RE.match(line)
        if m:
            if cur_id:
                out.append(Entry(cur_id, cur_title or "", cur_lines))
            cur_id = m.group(1)
            cur_title = (m.group(2) or "").strip()
            cur_lines = []
        elif cur_id:
            cur_lines.append(line)
    if cur_id:
        out.append(Entry(cur_id, cur_title or "", cur_lines))
    return out


def extract_block_label(lines: list[str], label: str) -> str:
    prefix = f"**{label}:**"
    start = None
    inline = ""
    for i, line in enumerate(lines):
        if line.startswith(prefix):
            start = i
            inline = line[len(prefix) :].strip()
            break
    if start is None:
        return ""

    out: list[str] = []
    if inline:
        out.append(clean_md(inline))
    for line in lines[start + 1 :]:
        s = line.strip()
        if not s:
            continue
        if s.startswith("**FI ") or s.startswith("**EN ") or s.startswith("#### ") or s.startswith("---"):
            break
        out.append(clean_md(s))
    return " ".join(x for x in out if x).strip()


def extract_block_lines(lines: list[str], label: str) -> list[str]:
    prefix = f"**{label}:**"
    start = None
    inline = ""
    for i, line in enumerate(lines):
        if line.startswith(prefix):
            start = i
            inline = line[len(prefix) :].strip()
            break
    if start is None:
        return []

    out: list[str] = []
    if inline:
        out.append(clean_md(inline))
    for line in lines[start + 1 :]:
        s = line.strip()
        if not s:
            continue
        if s.startswith("**FI ") or s.startswith("**EN ") or s.startswith("#### ") or s.startswith("---"):
            break
        out.append(clean_md(s))
    return [x for x in out if x]


def block_after_heading(lines: list[str], heading: str) -> list[str]:
    idx = None
    for i, line in enumerate(lines):
        if line.strip() == heading:
            idx = i
            break
    if idx is None:
        return []
    out: list[str] = []
    for line in lines[idx + 1 :]:
        if line.startswith("### "):
            break
        out.append(line)
    return out


def extract_dialog_prompt(section_lines: list[str], n: int) -> list[str]:
    heading = f"### Dialogi {n}"
    idx = None
    for i, line in enumerate(section_lines):
        if line.strip() == heading:
            idx = i
            break
    if idx is None:
        return []
    out: list[str] = []
    for line in section_lines[idx + 1 :]:
        if line.startswith("### "):
            break
        s = line.strip()
        if s:
            out.append(clean_md(s))
    return out


def dialog_context_sentence(section_lines: list[str], n: int) -> str:
    prompt = extract_dialog_prompt(section_lines, n)
    return prompt[0] if prompt else ""


def dialog_requires_self_opening(section_lines: list[str], n: int) -> bool:
    prompt = " ".join(extract_dialog_prompt(section_lines, n)).lower()
    return "aloita keskustelu vastaamalla puhelimeen" in prompt


def extract_dialog_model_rows(section_lines: list[str], n: int) -> tuple[str, str, list[tuple[str, str]]]:
    idx = None
    for heading in (f"### MALLI: Dialogi {n}", f"### Dialogi {n}"):
        for i, line in enumerate(section_lines):
            if line.strip() == heading:
                idx = i
                break
        if idx is not None:
            break
    if idx is None:
        return ("", "", [])

    role1 = ""
    role2 = ""
    rows: list[tuple[str, str]] = []
    in_table = False
    for line in section_lines[idx + 1 :]:
        s = line.strip()
        if s.startswith("### "):
            break
        if not s.startswith("|"):
            continue
        in_table = True
        parts = [p.strip() for p in s.strip("|").split("|")]
        if len(parts) < 2:
            continue
        if not role1 and not role2:
            role1, role2 = clean_md(parts[0]), clean_md(parts[1])
            continue
        if parts[0].startswith("---") or parts[1].startswith("---"):
            continue
        rows.append((clean_md(parts[0]), clean_md(parts[1])))
    if not in_table:
        return ("", "", [])
    return (role1, role2, rows)


def your_dialog_replies(e: Entry) -> list[str]:
    return extract_block_lines(e.lines, "FI Sinun repliikit (täydet mallivastaukset)")


def build_dialog_lines_from_source_and_answers(section_lines: list[str], n: int, e: Entry) -> list[str]:
    role1, role2, rows = extract_dialog_model_rows(section_lines, n)
    if not rows:
        return dialog_lines_for_entry(e)
    your = your_dialog_replies(e)
    yi = 0
    out: list[str] = []
    # Some phone-call tasks require "Sinä" to answer first before table rows.
    if dialog_requires_self_opening(section_lines, n) and your:
        out.append(f"{role2}: {your[0]}")
        yi = 1
    for left, right in rows:
        if left:
            out.append(f"{role1}: {left}")
        # Right side in source table is a placeholder. Replace with sample reply when available.
        right_out = right
        if yi < len(your):
            right_out = your[yi]
            yi += 1
        if right_out:
            out.append(f"{role2}: {right_out}")
    return out


def extract_reagointi_prompts(section_lines: list[str]) -> list[str]:
    block = block_after_heading(section_lines, "### Reagointi")
    out: list[str] = []
    for line in block:
        s = line.strip()
        if re.match(r"^\d+\.\s+", s):
            out.append(re.sub(r"^\d+\.\s+", "", s).strip())
    return out


def extract_letter_topics(section_lines: list[str], heading: str) -> dict[str, tuple[str, list[str]]]:
    block = block_after_heading(section_lines, heading)
    out: dict[str, tuple[str, list[str]]] = {}
    cur_letter = None
    cur_title = ""
    cur_prompts: list[str] = []

    def flush() -> None:
        nonlocal cur_letter, cur_title, cur_prompts
        if cur_letter:
            out[cur_letter] = (cur_title, cur_prompts[:])
        cur_letter = None
        cur_title = ""
        cur_prompts = []

    for line in block:
        s = line.strip()
        m = re.match(r"^\*\*([A-ZÅÄÖ])\.\s*(.+?)\*\*$", s)
        if m:
            flush()
            cur_letter = m.group(1)
            cur_title = clean_md(m.group(2))
            continue
        if cur_letter and s.startswith("- "):
            cur_prompts.append(clean_md(s))
    flush()
    return out


def extract_writing_tasks(section_lines: list[str]) -> list[tuple[str, list[str]]]:
    idx = None
    for i, line in enumerate(section_lines):
        if line.strip() == "### Kirjoittaminen":
            idx = i
            break
    if idx is None:
        return []

    out: list[tuple[str, list[str]]] = []
    cur_name = None
    cur_lines: list[str] = []
    for line in section_lines[idx + 1 :]:
        if line.startswith("## ") or line.startswith("### "):
            break
        if line.startswith("#### "):
            if cur_name is not None:
                out.append((cur_name, cur_lines[:]))
            cur_name = line.replace("#### ", "", 1).strip()
            cur_lines = []
            continue
        if cur_name is not None and line.strip():
            cur_lines.append(clean_md(line))
    if cur_name is not None:
        out.append((cur_name, cur_lines[:]))
    return out


def prompt_for_id(
    eid: str,
    section_lines: list[str],
    reagointi: list[str],
    kert_topics: dict[str, tuple[str, list[str]]],
    miel_topics: dict[str, tuple[str, list[str]]],
    writing_tasks: list[tuple[str, list[str]]],
) -> str:
    if eid.startswith("SPK-DIA-"):
        n = int(eid.split("-")[-1])
        return " ".join(extract_dialog_prompt(section_lines, n))
    if eid.startswith("SPK-REA-"):
        n = int(eid.split("-")[-1])
        return reagointi[n - 1] if 0 < n <= len(reagointi) else ""
    if eid.startswith("SPK-KER-"):
        letter = eid[-1]
        t = kert_topics.get(letter)
        if not t:
            return ""
        title, prompts = t
        return f"{title}. " + " ".join(prompts)
    if eid.startswith("SPK-MIE-"):
        letter = eid[-1]
        t = miel_topics.get(letter)
        if not t:
            return ""
        title, prompts = t
        return f"{title}. " + " ".join(prompts)
    if eid == "WRT-SAH-01":
        idx = 0
    elif eid == "WRT-VIE-01":
        idx = 1
    elif eid in ("WRT-MIE-01A", "WRT-MIE-01B"):
        idx = 2
    else:
        idx = -1
    if 0 <= idx < len(writing_tasks):
        name, lines = writing_tasks[idx]
        text = f"{name}. " + " ".join(lines)
        return text
    return ""


def answer_for_entry(e: Entry) -> str:
    eid = e.id
    lines = e.lines
    if eid.startswith("SPK-DIA-"):
        fi_rep = extract_block_label(lines, "FI Sinun repliikit (täydet mallivastaukset)")
        fi_full = extract_block_label(lines, "FI Koko mallidialogi (täysi)")
        parts = []
        if fi_rep:
            parts.append("Sinun mallirepliikit. " + fi_rep)
        if fi_full:
            parts.append("Koko mallidialogi. " + fi_full)
        return " ".join(parts).strip()
    if eid.startswith("SPK-KER-"):
        return extract_block_label(lines, "FI Mallipuhe")
    if eid.startswith("WRT-"):
        return extract_block_label(lines, "FI Malliteksti")
    return extract_block_label(lines, "FI Mallivastaus")


def context_for_entry(e: Entry) -> str:
    return extract_block_label(e.lines, "FI Konteksti")


def dialog_lines_for_entry(e: Entry) -> list[str]:
    raw = extract_block_lines(e.lines, "FI Koko mallidialogi (täysi)")
    out: list[str] = []
    for line in raw:
        s = line.strip()
        # Preserve all dialogue speakers (e.g. Sinä, Ystävä, Myyjä).
        if re.match(r"^[A-ZÅÄÖa-zåäö0-9][^:]{0,30}:\s+\S", s):
            out.append(s)
    return out


def build_listening_text(eid: str, context: str, prompt: str, answer: str, dialog_lines: list[str]) -> str:
    if eid.startswith("SPK-DIA-"):
        parts: list[str] = []
        if context:
            parts.append(context)
        if dialog_lines:
            parts.extend(dialog_lines)
        elif answer:
            parts.append(answer)
        return "\n".join(parts).strip()

    parts = []
    if prompt:
        parts.append(prompt)
    if answer:
        parts.append(answer)
    return " ".join(parts).strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Build Finnish complete listening package with context + prompt + answer per ID.")
    parser.add_argument("--source-md", required=True)
    parser.add_argument("--section", required=True)
    parser.add_argument("--fi-en-package", required=True)
    parser.add_argument("--out", required=True)
    args = parser.parse_args()

    source_lines = read_lines(Path(args.source_md).expanduser().resolve())
    section_lines = extract_section(source_lines, args.section)
    reagointi = extract_reagointi_prompts(section_lines)
    kert_topics = extract_letter_topics(section_lines, "### Kertominen")
    miel_topics = extract_letter_topics(section_lines, "### Mielipide")
    writing_tasks = extract_writing_tasks(section_lines)

    entries = split_entries(read_lines(Path(args.fi_en_package).expanduser().resolve()))
    if not entries:
        raise RuntimeError("No ID entries found in FI-EN package.")

    out_lines: list[str] = []
    out_lines.append(f"# {args.section} - Kuuntelupaketti (FI Complete)")
    out_lines.append("")
    out_lines.append("Tämä paketti on kuunteluharjoittelua varten.")
    out_lines.append("Teksti on tiivis: vain olennainen konteksti, kysymys/tehtävä ja mallivastaus suomeksi.")
    out_lines.append("")
    out_lines.append("Lähteet:")
    out_lines.append(f"- source: `{Path(args.source_md).expanduser().resolve()}`")
    out_lines.append(f"- answers: `{Path(args.fi_en_package).expanduser().resolve()}`")
    out_lines.append("")
    out_lines.append("---")
    out_lines.append("")

    for e in entries:
        prompt = prompt_for_id(e.id, section_lines, reagointi, kert_topics, miel_topics, writing_tasks)
        answer = answer_for_entry(e)
        dlines: list[str] = []
        if e.id.startswith("SPK-DIA-"):
            n = int(e.id.split("-")[-1])
            # Prefer package context so FI listening text matches FI-EN mapping
            # exactly; fall back to source prompt sentence only if missing.
            context = context_for_entry(e) or dialog_context_sentence(section_lines, n)
            # Prefer canonical full dialogue from package so FI and EN stay 1:1.
            # Fall back to source+answers reconstruction only if needed.
            dlines = dialog_lines_for_entry(e)
            if not dlines:
                dlines = build_dialog_lines_from_source_and_answers(section_lines, n, e)
        else:
            context = context_for_entry(e)
        listen = build_listening_text(e.id, context, prompt, answer, dlines)

        out_lines.append(f"#### {e.id} - {e.title or e.id}")
        if context:
            out_lines.append(f"**Konteksti (FI):** {context}")
        else:
            out_lines.append("**Konteksti (FI):**")
        if not e.id.startswith("SPK-DIA-"):
            out_lines.append("**Tehtävä ja kysymykset (FI):**")
            out_lines.append(prompt if prompt else "(ei löydy)")
            out_lines.append("**Mallivastaus (FI):**")
            out_lines.append(answer if answer else "(ei löydy)")
        else:
            out_lines.append("**Dialogi (FI):**")
            if dlines:
                out_lines.extend(dlines)
            else:
                out_lines.append("(ei löydy)")
        out_lines.append("**FI Kuunteluteksti:**")
        out_lines.append(listen if listen else "(ei löydy)")
        out_lines.append("")

    out_path = Path(args.out).expanduser().resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(out_lines).rstrip() + "\n", encoding="utf-8")
    print(f"Wrote: {out_path}")
    print(f"Entries: {len(entries)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
