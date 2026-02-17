#!/usr/bin/env python3
from pathlib import Path
import re

SRC = Path('/Users/jingliang/Documents/YKI_exam/yki_highway_to_hill.md')
OUT = Path('/Users/jingliang/Documents/YKI_exam/yki_highway_to_hill.polished.md')

ROLE_SET = {
    'Sinä',
    'Ystävä',
    'Tuttava',
    'Myyjä',
    'Lähetti',
    'Isännöitsijä',
    'Nainen',
    'Mies',
    'Virkailija',
    'Kampaaja',
    'Pomo',
    'Kollega',
    'Työkaveri',
    'Portsari',
    'Tutkija',
    'Sihteeri',
}

PROMPT_VERBS = (
    'Vastaa', 'Kerro', 'Kysy', 'Kiitä', 'Lopeta', 'Tervehdi', 'Esittele',
    'Reagoi', 'Tarjoa', 'Ihmettele', 'Ihmettelet', 'Neuvo', 'Kieltäydy',
    'Pyydä', 'Kommentoi', 'Olet', 'Et',
)


def norm_space(s: str) -> str:
    return re.sub(r'\s+', ' ', s).strip()


def is_time(s: str) -> bool:
    return bool(re.fullmatch(r'\d+\s*sek', s.strip(), flags=re.IGNORECASE))


def is_prompt(s: str) -> bool:
    t = s.strip()
    if not t:
        return False
    if re.fullmatch(r'\([^()]+\)', t):
        return True
    if any(t.startswith(v) for v in PROMPT_VERBS):
        # Non-parenthesized prompts appear in OCR output; keep it conservative.
        if len(t.split()) <= 12 and not re.search(r'[.!?]$', t):
            return True
    return False


def normalize_prompt(s: str) -> str:
    t = norm_space(s)
    if t.startswith('(') and t.endswith(')'):
        return t
    return f'({t})'


def compact_paragraphs(lines: list[str]) -> list[str]:
    out: list[str] = []
    buf: list[str] = []
    for raw in lines:
        t = norm_space(raw)
        if not t:
            if buf:
                out.append(' '.join(buf))
                buf = []
            if out and out[-1] != '':
                out.append('')
            continue
        buf.append(t)
    if buf:
        out.append(' '.join(buf))

    # Remove trailing/leading and duplicate blanks.
    while out and out[0] == '':
        out.pop(0)
    while out and out[-1] == '':
        out.pop()
    cleaned: list[str] = []
    prev_blank = False
    for t in out:
        if t == '':
            if not prev_blank:
                cleaned.append(t)
            prev_blank = True
        else:
            cleaned.append(t)
            prev_blank = False
    return cleaned


def join_parenthetical_wrapped(lines: list[str]) -> list[str]:
    out: list[str] = []
    i = 0
    while i < len(lines):
        t = lines[i].strip()
        if not t:
            out.append('')
            i += 1
            continue

        # Join prompts broken across multiple lines.
        if t.startswith('(') and ')' not in t:
            parts = [t]
            i += 1
            while i < len(lines):
                n = lines[i].strip()
                if n:
                    parts.append(n)
                if n.endswith(')'):
                    i += 1
                    break
                i += 1
            out.append(norm_space(' '.join(parts)))
            continue

        # Join non-parenthesized short imperative prompts split across lines.
        if any(t.startswith(v) for v in PROMPT_VERBS) and len(t.split()) <= 6 and not re.search(r'[.!?]$', t):
            parts = [t]
            j = i + 1
            while j < len(lines):
                n = lines[j].strip()
                if not n:
                    break
                if n in ROLE_SET or n == 'Audiomateriaali' or n == '***' or is_time(n) or n.startswith('('):
                    break
                if len(n.split()) > 8 or re.search(r'[.!?]$', n):
                    break
                parts.append(n)
                j += 1
            out.append(norm_space(' '.join(parts)))
            i = j
            continue

        out.append(t)
        i += 1
    return out


def format_dialog_block(block: list[str]) -> list[str]:
    lines = join_parenthetical_wrapped(block)

    # Description = before metadata/prompts.
    first_meta = len(lines)
    for i, raw in enumerate(lines):
        t = raw.strip()
        if not t:
            continue
        if t in ROLE_SET or t in {'Audiomateriaali', '***'} or is_prompt(t) or is_time(t):
            first_meta = i
            break

    desc_lines = lines[:first_meta]
    desc = compact_paragraphs(desc_lines)
    # Keep instruction marker on its own line when it was glued by extraction.
    desc_fixed: list[str] = []
    for d in desc:
        if ' Lue ja kuuntele' in d and d != 'Lue ja kuuntele':
            head, tail = d.split(' Lue ja kuuntele', 1)
            if head.strip():
                desc_fixed.append(head.strip())
            desc_fixed.append('Lue ja kuuntele')
            if tail.strip():
                desc_fixed.append(tail.strip())
        else:
            desc_fixed.append(d)
    desc = desc_fixed

    roles: list[str] = []
    for raw in lines[first_meta:]:
        t = raw.strip()
        if not t or t in {'Audiomateriaali', '***'}:
            continue
        if t in ROLE_SET:
            if t not in roles:
                roles.append(t)
            continue
        if is_prompt(t) or is_time(t):
            continue
        # Unrecognized content; keep scanning but do not treat as role.

    prompts: list[tuple[str, str]] = []
    pending_prompt: str | None = None
    pending_time: str | None = None

    for raw in lines[first_meta:]:
        t = raw.strip()
        if not t or t in {'Audiomateriaali', '***'} or t in ROLE_SET:
            continue
        if is_time(t):
            if pending_prompt is not None:
                prompts.append((pending_prompt, norm_space(t)))
                pending_prompt = None
            else:
                pending_time = norm_space(t)
            continue
        if is_prompt(t):
            p = normalize_prompt(t)
            if pending_time is not None:
                prompts.append((p, pending_time))
                pending_time = None
            else:
                pending_prompt = p
            continue

    if pending_prompt is not None:
        prompts.append((pending_prompt, ''))

    out: list[str] = []
    out.extend(desc)
    if out:
        out.append('')

    if roles:
        out.append(f"**Roolit:** {', '.join(roles)}")
    else:
        out.append('**Roolit:** Sinä')
    out.append('')

    out.append('**Sinun repliikit (aikaraja):**')
    if prompts:
        for idx, (p, sec) in enumerate(prompts, start=1):
            if sec:
                out.append(f'{idx}. {p} — {sec}')
            else:
                out.append(f'{idx}. {p}')
    else:
        out.append('1. (Vastaa.)')

    return out


def choose_roles(roles: list[str]) -> tuple[str, str]:
    if 'Sinä' in roles:
        others = [r for r in roles if r != 'Sinä']
        if others:
            return others[0], 'Sinä'
    if len(roles) >= 2:
        return roles[0], roles[1]
    if roles == ['Sinä']:
        return 'Vastapuoli', 'Sinä'
    return 'Vastapuoli', 'Sinä'


def format_malli_block(block: list[str]) -> list[str]:
    lines = join_parenthetical_wrapped(block)

    # Description before first explicit role marker.
    first_role = None
    for i, raw in enumerate(lines):
        t = raw.strip()
        if t in ROLE_SET:
            first_role = i
            break

    if first_role is None:
        # Fallback: compact only.
        return compact_paragraphs(lines)

    desc = compact_paragraphs(lines[:first_role])

    # Roles can appear with intervening time/prompt lines; collect until first spoken text.
    roles: list[str] = []
    dialog_start = first_role
    for i in range(first_role, len(lines)):
        t = lines[i].strip()
        if not t or t in {'Audiomateriaali', '***'}:
            continue
        if t in ROLE_SET:
            if t not in roles:
                roles.append(t)
            continue
        if is_prompt(t) or is_time(t):
            continue
        dialog_start = i
        break

    left_role, right_role = choose_roles(roles)

    rows: list[tuple[str, str]] = []
    text_buf: list[str] = []
    pending_prompt: str | None = None
    pending_speaker: str = ''
    pending_time: str | None = None

    for raw in lines[dialog_start:]:
        t = raw.strip()
        if not t or t in {'Audiomateriaali', '***'} or t in ROLE_SET:
            continue

        if is_time(t):
            if pending_prompt is not None:
                rows.append((pending_speaker, f'{pending_prompt} — {norm_space(t)}'))
                pending_prompt = None
                pending_speaker = ''
            else:
                pending_time = norm_space(t)
            continue

        if is_prompt(t):
            p = normalize_prompt(t)
            speaker = norm_space(' '.join(text_buf))
            text_buf = []
            if pending_time is not None:
                rows.append((speaker, f'{p} — {pending_time}'))
                pending_time = None
            else:
                pending_prompt = p
                pending_speaker = speaker
            continue

        if pending_prompt is not None:
            rows.append((pending_speaker, pending_prompt))
            pending_prompt = None
            pending_speaker = ''

        text_buf.append(t)

    if pending_prompt is not None:
        rows.append((pending_speaker, pending_prompt))
    if text_buf:
        rows.append((norm_space(' '.join(text_buf)), ''))

    out: list[str] = []
    out.extend(desc)
    if out:
        out.append('')
    out.append(f'| {left_role} | {right_role} |')
    out.append('|---|---|')
    for left, right in rows:
        if not left and not right:
            continue
        l = left.replace('|', '\\|')
        r = right.replace('|', '\\|')
        out.append(f'| {l} | {r} |')
    return out


def format_reagointi_block(block: list[str]) -> list[str]:
    lines = [norm_space(l) for l in block]
    lines = [l for l in lines if l]

    out: list[str] = []
    current: str | None = None
    for l in lines:
        if l.startswith('• '):
            l = '- ' + l[2:]
        if re.match(r'^\d+\.\s*', l):
            if current:
                out.append(norm_space(current))
            current = l
        else:
            if current:
                current = f'{current} {l}'
            else:
                out.append(l)
    if current:
        out.append(norm_space(current))
    return out


def format_topic_block(block: list[str]) -> list[str]:
    def is_bullet_marker(s: str) -> bool:
        return bool(re.fullmatch(r'[•▪◦·]+', s))

    def is_bullet_line(s: str) -> bool:
        return bool(re.match(r'^[•▪◦·]\s*', s))

    raw = [norm_space(l) for l in block]

    # Build logical lines while preserving section letters and bullet markers.
    logical: list[str] = []
    for t in raw:
        if not t:
            if logical and logical[-1] != '':
                logical.append('')
            continue

        if not logical or logical[-1] == '':
            logical.append(t)
            continue

        prev = logical[-1]
        is_struct = bool(re.match(r'^[A-ZÅÄÖ]\.\s+', t) or is_bullet_line(t) or is_bullet_marker(t) or re.match(r'^\d+\.\s+', t))

        if is_struct:
            logical.append(t)
            continue

        # Continue wrapped bullet/question lines.
        if is_bullet_line(prev):
            logical[-1] = f'{prev} {t}'
            continue

        if is_bullet_marker(prev):
            logical.append(t)
            continue

        # Keep heading lines separate from following content.
        if re.match(r'^[A-ZÅÄÖ]\.\s+', prev):
            if re.match(r'^[a-zåäö]', t):
                logical[-1] = f'{prev} {t}'
            else:
                logical.append(t)
            continue

        # General line wrap recovery.
        if not re.search(r'[.!?:]$', prev):
            logical[-1] = f'{prev} {t}'
        elif re.match(r'^[a-zåäö]', t):
            logical[-1] = f'{prev} {t}'
        else:
            logical.append(t)

    out: list[str] = []
    in_topic = False
    for l in logical:
        if not l:
            if out and out[-1] != '':
                out.append('')
            continue

        if re.match(r'^[A-ZÅÄÖ]\.\s+', l):
            out.append(f'**{l}**')
            in_topic = True
            continue

        if is_bullet_marker(l):
            # Placeholder bullets in source; skip marker and keep following lines as bullets.
            continue

        if is_bullet_line(l):
            out.append('- ' + re.sub(r'^[•▪◦·]\s*', '', l).strip())
            continue

        if in_topic:
            if l.endswith(':'):
                out.append(l)
            else:
                out.append(f'- {l}')
        else:
            out.append(l)

    cleaned: list[str] = []
    prev_blank = False
    for l in out:
        if l == '':
            if not prev_blank:
                cleaned.append(l)
            prev_blank = True
        else:
            cleaned.append(l)
            prev_blank = False
    return cleaned


def main() -> None:
    lines = SRC.read_text(encoding='utf-8').splitlines()
    out: list[str] = []

    # Recursive split: first by sections (##), then by subsections (###).
    section_starts = [i for i, l in enumerate(lines) if l.startswith('## ')]
    if not section_starts:
        section_starts = [0]
    boundaries = section_starts + [len(lines)]

    # Preserve preamble before first ## (title line etc.).
    if section_starts and section_starts[0] > 0:
        out.extend(lines[:section_starts[0]])

    for s_idx, e_idx in zip(boundaries, boundaries[1:]):
        section = lines[s_idx:e_idx]
        if not section:
            continue

        # Keep section header as-is.
        out.append(section[0])
        if len(section) == 1:
            out.append('')
            continue

        body = section[1:]
        i = 0
        n = len(body)
        while i < n:
            line = body[i]

            m_dialog = re.match(r'^### Dialogi \d+$', line)
            m_malli = re.match(r'^### MALLI: Dialogi \d+$', line)

            if m_dialog or m_malli or line in {'### Reagointi', '### Kertominen', '### Mielipide'}:
                j = i + 1
                while j < n and not re.match(r'^(###|####)\s', body[j]):
                    j += 1
                block = body[i + 1:j]

                out.append('')
                out.append(line)
                out.append('')

                if m_dialog:
                    out.extend(format_dialog_block(block))
                elif m_malli:
                    out.extend(format_malli_block(block))
                elif line == '### Reagointi':
                    out.extend(format_reagointi_block(block))
                else:
                    out.extend(format_topic_block(block))

                out.append('')
                i = j
                continue

            out.append(line)
            i += 1

    # Final compact blank lines.
    final: list[str] = []
    prev_blank = False
    for l in out:
        if norm_space(l) == '':
            if not prev_blank:
                final.append('')
            prev_blank = True
        else:
            final.append(l.rstrip())
            prev_blank = False

    OUT.write_text('\n'.join(final).rstrip() + '\n', encoding='utf-8')
    print(f'Wrote {OUT}')


if __name__ == '__main__':
    main()
