#!/usr/bin/env python3
from pathlib import Path
import re

INPUT = Path('/Users/jingliang/Documents/YKI_exam/new_pdf_plain.txt')
OUTPUT = Path('/Users/jingliang/Documents/YKI_exam/yki_highway_to_hill.regen.md')

section_names = [
    'Ihminen ja lähipiiri',
    'Arkielämä',
    'Luonto ja ympäristö',
    'Terveys ja hyvinvointi',
    'Työ ja koulutus',
    'Vapaa-aika',
    'Yhteiskunta',
]
section_set = set(section_names)

sub_heads = {
    'Lämmittely',
    'Reagointi',
    'Kertominen',
    'Mielipide',
    'Kirjoittaminen',
    'Minä kuluttajana',
}

minor_heads = {
    'Luonnonkatastrofit',
    'Jätteiden lajittelu',
    'Kierrätys',
}

raw_lines = INPUT.read_text(encoding='utf-8', errors='ignore').replace('\r', '').splitlines()
out: list[str] = ['# Ykäänkö vai ykiinkö — Highway to Hill (Regenerated from PDF)', '']

started = False
current_section: str | None = None

for raw in raw_lines:
    line = raw.lstrip('\ufeff').lstrip('\x0c').strip()
    if not line:
        if started:
            out.append('')
        continue

    # Skip title page/copyright/toc until the real intro body starts.
    if not started:
        if line != 'Miten kirjaa käytetään':
            continue
        started = True
        out.extend(['## Johdanto', '', '### Miten kirjaa käytetään', ''])
        continue

    # Drop watermark/footer artifacts from the PDF export.
    if line.startswith('Omistaja Jing Liang, jingliang@gmail.com'):
        continue
    if re.fullmatch(r'\d+', line):
        continue

    # Normalize spacing and common extraction glitches.
    line = re.sub(r'\s+', ' ', line)
    line = re.sub(r'(\d)\.([^\s])', r'\1. \2', line)
    line = line.replace('Mksi', 'Miksi')
    line = line.replace('vekkosivun', 'verkkosivun')
    line = line.replace('tylsää?Pitääkö', 'tylsää? Pitääkö')

    if line == 'Johdanto':
        # Skip page header repeats.
        continue

    if line in section_set:
        # Ignore page-header repeats inside same section.
        if line == current_section:
            continue
        out.extend([f'## {line}', ''])
        current_section = line
        continue

    if line == 'Miten kirjaa käytetään':
        continue
    if re.fullmatch(r'Dialogi \d+', line):
        out.extend([f'### {line}', ''])
        continue
    if re.fullmatch(r'MALLI: Dialogi \d+', line):
        out.extend([f'### {line}', ''])
        continue
    if line.startswith('Tehtävä '):
        out.append(f'#### {line}')
        continue
    if line in sub_heads:
        out.extend([f'### {line}', ''])
        continue
    if line in minor_heads:
        out.extend([f'#### {line}', ''])
        continue

    out.append(line)

# Collapse multiple blank lines.
collapsed: list[str] = []
blank = False
for line in out:
    if line == '':
        if not blank:
            collapsed.append('')
        blank = True
    else:
        collapsed.append(line)
        blank = False

OUTPUT.write_text('\n'.join(collapsed).rstrip() + '\n', encoding='utf-8')
print(f'Wrote {OUTPUT}')
