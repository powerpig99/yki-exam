#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  soundcloud_url_to_karaoke.sh <soundcloud_playlist_or_track_url> [output_dir]

Example:
  soundcloud_url_to_karaoke.sh "https://soundcloud.com/gi-mara/sets/sisunautti-yhteiskunta-dialogit"

Output:
  <slug>.mp3 and <slug>.mp4 in output_dir (default: current directory)
USAGE
}

fail() {
  echo "Error: $*" >&2
  exit 1
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    fail "missing required command: $1"
  fi
}

canonicalize_and_slugify() {
  python3 - "$1" <<'PY'
import re
import sys
import unicodedata
from urllib.parse import parse_qs, unquote, urlparse

raw = sys.argv[1].strip()
p = urlparse(raw)

if p.scheme and p.netloc:
    base = f"{p.scheme}://{p.netloc}{p.path}"
else:
    base = raw.split("?", 1)[0]

canonical = base
if "/sets/" not in p.path:
    qs = parse_qs(p.query)
    in_val = qs.get("in", [None])[0]
    if in_val:
        decoded = unquote(in_val).strip("/")
        candidate = f"https://soundcloud.com/{decoded}"
        if "/sets/" in candidate:
            canonical = candidate

cp = urlparse(canonical)
parts = [part for part in cp.path.strip("/").split("/") if part]
slug = ""
if "sets" in parts:
    idx = parts.index("sets")
    if idx + 1 < len(parts):
        slug = parts[idx + 1]
if not slug and parts:
    slug = parts[-1]

slug = unicodedata.normalize("NFKD", slug).encode("ascii", "ignore").decode("ascii")
slug = slug.lower()
slug = re.sub(r"[^a-z0-9._-]+", "-", slug).strip("-._")
if not slug:
    slug = "soundcloud-playlist"

print(canonical)
print(slug)
PY
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if (( $# < 1 || $# > 2 )); then
  usage >&2
  exit 1
fi

INPUT_URL="$1"
OUTPUT_DIR="${2:-$PWD}"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MERGE_SCRIPT="$SCRIPT_DIR/soundcloud_playlist_clean_merge.sh"
KARAOKE_SCRIPT="$SCRIPT_DIR/transcribe_karaoke_video.py"

[[ -x "$MERGE_SCRIPT" ]] || fail "missing executable script: $MERGE_SCRIPT"
[[ -x "$KARAOKE_SCRIPT" ]] || fail "missing executable script: $KARAOKE_SCRIPT"
require_cmd python3
require_cmd sed
require_cmd tail

mkdir -p "$OUTPUT_DIR"
OUTPUT_DIR="$(cd "$OUTPUT_DIR" && pwd)"

parsed_output="$(canonicalize_and_slugify "$INPUT_URL")"
PLAYLIST_URL="$(printf '%s\n' "$parsed_output" | sed -n '1p')"
SLUG="$(printf '%s\n' "$parsed_output" | sed -n '2p')"
if [[ -z "$PLAYLIST_URL" || -z "$SLUG" ]]; then
  fail "could not parse SoundCloud URL"
fi

FINAL_MP3="$OUTPUT_DIR/$SLUG.mp3"
FINAL_MP4="$OUTPUT_DIR/$SLUG.mp4"

TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/soundcloud-karaoke.XXXXXX")"
cleanup() {
  rm -rf "$TMP_DIR"
}
trap cleanup EXIT

MERGE_LOG="$TMP_DIR/merge.log"
echo "Step 1/2: Download, clean, and merge playlist"
echo "Playlist URL: $PLAYLIST_URL"
echo "Target MP3: $FINAL_MP3"

set +e
"$MERGE_SCRIPT" "$PLAYLIST_URL" "$SLUG.mp3" 2>&1 | tee "$MERGE_LOG"
merge_status=${PIPESTATUS[0]}
set -e

WORK_ROOT="$(sed -n 's/^Work folder: //p' "$MERGE_LOG" | tail -n 1)"
if [[ -n "$WORK_ROOT" && -d "$WORK_ROOT" ]]; then
  rm -rf "$WORK_ROOT"
fi

if (( merge_status != 0 )); then
  fail "playlist download/merge failed"
fi

MERGED_PATH="$(sed -n 's/^Merged file: //p' "$MERGE_LOG" | tail -n 1)"
if [[ -z "$MERGED_PATH" ]]; then
  MERGED_PATH="$HOME/Downloads/$SLUG.mp3"
fi
[[ -f "$MERGED_PATH" ]] || fail "merged MP3 not found: $MERGED_PATH"

if [[ "$MERGED_PATH" != "$FINAL_MP3" ]]; then
  mv -f "$MERGED_PATH" "$FINAL_MP3"
fi

echo "Step 2/2: Transcribe and render karaoke video"
echo "Target MP4: $FINAL_MP4"

TMP_PREFIX="$TMP_DIR/$SLUG"
"$KARAOKE_SCRIPT" \
  "$FINAL_MP3" \
  --output-prefix "$TMP_PREFIX" \
  --english-translation \
  --force-transcribe \
  --force-translate

TMP_MP4="$TMP_PREFIX.karaoke.mp4"
[[ -f "$TMP_MP4" ]] || fail "rendered MP4 not found: $TMP_MP4"
mv -f "$TMP_MP4" "$FINAL_MP4"

echo
echo "Done"
echo "MP3: $FINAL_MP3"
echo "MP4: $FINAL_MP4"
