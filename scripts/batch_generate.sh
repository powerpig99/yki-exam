#!/usr/bin/env bash
# Generic batch TTS + video generator
# Usage: scripts/batch_generate.sh <learner_dir> <gender> <delivery_path>
# Example: scripts/batch_generate.sh linh female ~/delivery/YKI_linh
#
# Auto-discovers IDs from learners/<learner_dir>/dialogues/*/fi_en_package.md
# Skips already-delivered items (safe re-runs)
# Retries TTS up to 3 times on failure (checks manifest file, not exit code)
set -uo pipefail

if [ $# -ne 3 ]; then
  echo "Usage: $0 <learner_dir> <gender> <delivery_path>"
  echo "  learner_dir:   directory name under learners/ (e.g. linh)"
  echo "  gender:        male or female (for TTS voice selection)"
  echo "  delivery_path: absolute path to iCloud delivery folder"
  exit 1
fi

LEARNER="$1"
GENDER="$2"
DELIVERY="$3"

cd "$(dirname "$0")/.."

DIALOGUE_DIR="learners/$LEARNER/dialogues"
if [ ! -d "$DIALOGUE_DIR" ]; then
  echo "ERROR: $DIALOGUE_DIR not found"
  exit 1
fi

mkdir -p "$DELIVERY"

# Auto-discover IDs from dialogues directory (sorted for consistent order)
IDS=()
while IFS= read -r dir; do
  id=$(basename "$dir")
  IDS+=("$id")
done < <(find "$DIALOGUE_DIR" -maxdepth 1 -mindepth 1 -type d -exec test -f '{}/fi_en_package.md' \; -print | sort)

TOTAL=${#IDS[@]}
if [ $TOTAL -eq 0 ]; then
  echo "ERROR: No fi_en_package.md files found in $DIALOGUE_DIR"
  exit 1
fi

echo "Batch: $LEARNER ($GENDER) — $TOTAL items → $DELIVERY"
DONE=0
FAIL=0
FAILED_IDS=()

for id in "${IDS[@]}"; do
  DONE=$((DONE + 1))
  echo ""
  echo "=== [$DONE/$TOTAL] $id ==="

  # Skip if already delivered
  if [ -f "$DELIVERY/${id}.mp4" ]; then
    echo "  Already delivered, skipping."
    continue
  fi

  # TTS with up to 3 retries (check manifest file, not exit code)
  MANIFEST="$DIALOGUE_DIR/$id/audio/manifest.json"
  rm -f "$MANIFEST"
  for attempt in 1 2 3; do
    .venv/bin/python3 scripts/generate_dialog_tts_google.py --only "$id" --force --learner-gender "$GENDER" 2>&1 || true
    if [ -f "$MANIFEST" ]; then
      break
    fi
    echo "  TTS attempt $attempt failed (no manifest), waiting 30s..."
    sleep 30
  done

  if [ ! -f "$MANIFEST" ]; then
    echo "  FAILED after 3 TTS attempts: $id"
    FAIL=$((FAIL + 1))
    FAILED_IDS+=("$id")
    continue
  fi

  # Video render (check output file, not exit code)
  VIDEO="$DIALOGUE_DIR/$id/video/dialogue.karaoke.mp4"
  .venv/bin/python3 scripts/render_dialog_karaoke.py --only "$id" --force 2>&1 || true

  if [ ! -f "$VIDEO" ]; then
    echo "  VIDEO RENDER FAILED: $id"
    FAIL=$((FAIL + 1))
    FAILED_IDS+=("$id")
    continue
  fi

  # Copy to delivery
  cp "$VIDEO" "$DELIVERY/${id}.mp4"
  echo "  Delivered: ${id}.mp4"
done

echo ""
echo "========================================="
echo "BATCH COMPLETE: $((DONE - FAIL))/$TOTAL succeeded, $FAIL failed"
if [ $FAIL -gt 0 ]; then
  echo "FAILED: ${FAILED_IDS[*]}"
fi
echo "========================================="
