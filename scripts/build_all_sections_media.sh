#!/usr/bin/env bash
set -euo pipefail

ROOT="."
SCRIPT="$ROOT/scripts/build_section_media.py"

# NOTE:
# - This pipeline only uses newly generated section packages/audio.
# - It does NOT use the old root-level 01..07 mp3 files as final outputs.

declare -a SECTIONS=(
  "01|ihminen_ja_lahipiiri|Ihminen ja lähipiiri|$ROOT/study_packages/01_ihminen_ja_lahipiiri_complete_package.fi_en.md"
  "02|arkielama|Arkielämä|$ROOT/study_packages/02_arkielama_complete_package.fi_en.md"
  "03|luonto_ja_ymparisto|Luonto ja ympäristö|$ROOT/study_packages/03_luonto_ja_ymparisto_complete_package.fi_en.md"
  "04|tyo_ja_koulutus|Työ ja koulutus|$ROOT/study_packages/04_tyo_ja_koulutus_complete_package.fi_en.md"
  "05|terveys_ja_hyvinvointi|Terveys ja hyvinvointi|$ROOT/study_packages/05_terveys_ja_hyvinvointi_complete_package.fi_en.md"
  "06|vapaa_aika|Vapaa-aika|$ROOT/study_packages/06_vapaa_aika_complete_package.fi_en.md"
  "07|yhteiskunta|Yhteiskunta|$ROOT/study_packages/07_yhteiskunta_complete_package.fi_en.md"
)

for row in "${SECTIONS[@]}"; do
  IFS='|' read -r sid slug stitle pkg <<<"$row"
  echo "== Section $sid: $stitle =="
  if [[ ! -f "$pkg" ]]; then
    echo "SKIP: missing package $pkg"
    continue
  fi

  python3 "$SCRIPT" \
    --section-id "$sid" \
    --section-slug "$slug" \
    --section-title "$stitle" \
    --fi-en-package "$pkg" \
    --translation-batch-size 1
done

echo "All available sections processed."
