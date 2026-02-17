#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'USAGE'
Usage:
  soundcloud_playlist_clean_merge.sh <soundcloud_playlist_or_track_url> [output_name]

Examples:
  soundcloud_playlist_clean_merge.sh "https://soundcloud.com/user/sets/my-set"
  soundcloud_playlist_clean_merge.sh "https://soundcloud.com/user/track?in=user/sets/my-set" finnish_lessons.mp3

Environment overrides (optional):
  NOISE_FLOOR_DB      Default: -30   (afftdn noise floor, lower = stronger denoise)
  SILENCE_THRESH_DB   Default: -50   (silence threshold)
  MIN_SILENCE_SEC     Default: 1.5   (only remove silence longer than this)
  KEEP_SILENCE_SEC    Default: 0.30  (leave this much silence where cuts happen)
USAGE
}

require_cmd() {
  if ! command -v "$1" >/dev/null 2>&1; then
    echo "Missing required command: $1" >&2
    exit 1
  fi
}

resolve_ffmpeg_tools() {
  local full_ffmpeg="/opt/homebrew/opt/ffmpeg-full/bin/ffmpeg"
  local full_ffprobe="/opt/homebrew/opt/ffmpeg-full/bin/ffprobe"

  if [[ -x "$full_ffmpeg" && -x "$full_ffprobe" ]]; then
    FFMPEG_BIN="$full_ffmpeg"
    FFPROBE_BIN="$full_ffprobe"
  else
    FFMPEG_BIN="$(command -v ffmpeg || true)"
    FFPROBE_BIN="$(command -v ffprobe || true)"
  fi

  if [[ -z "${FFMPEG_BIN:-}" || -z "${FFPROBE_BIN:-}" ]]; then
    echo "Missing required tools: ffmpeg and/or ffprobe" >&2
    exit 1
  fi

  FFMPEG_DIR="$(dirname "$FFMPEG_BIN")"
}

is_valid_audio() {
  local file="$1"
  local duration

  [[ -s "$file" ]] || return 1

  duration="$("$FFPROBE_BIN" -v error -show_entries format=duration -of default=noprint_wrappers=1:nokey=1 "$file" 2>/dev/null || true)"
  [[ "$duration" =~ ^[0-9]+([.][0-9]+)?$ ]] || return 1

  awk -v d="$duration" 'BEGIN { exit !(d > 0.05) }'
}

canonicalize_playlist_url() {
  python3 - "$1" <<'PY'
import sys
from urllib.parse import urlparse, parse_qs, unquote

raw = sys.argv[1].strip()
p = urlparse(raw)

if p.scheme and p.netloc:
    base = f"{p.scheme}://{p.netloc}{p.path}"
else:
    base = raw.split("?", 1)[0]

if "/sets/" in p.path:
    print(base)
    raise SystemExit

qs = parse_qs(p.query)
in_val = qs.get("in", [None])[0]
if in_val:
    decoded = unquote(in_val).strip("/")
    if decoded:
        candidate = f"https://soundcloud.com/{decoded}"
        if "/sets/" in candidate:
            print(candidate)
            raise SystemExit

print(base)
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

require_cmd yt-dlp
require_cmd python3
resolve_ffmpeg_tools

INPUT_URL="$1"
OUTPUT_NAME="${2:-}"

NOISE_FLOOR_DB="${NOISE_FLOOR_DB:--30}"
SILENCE_THRESH_DB="${SILENCE_THRESH_DB:--50}"
MIN_SILENCE_SEC="${MIN_SILENCE_SEC:-1.5}"
KEEP_SILENCE_SEC="${KEEP_SILENCE_SEC:-0.30}"

PLAYLIST_URL="$(canonicalize_playlist_url "$INPUT_URL")"

stamp="$(date +%Y%m%d-%H%M%S)"
WORK_ROOT="$HOME/Downloads/soundcloud-clean-$stamp"
RAW_DIR="$WORK_ROOT/raw"
CLEAN_DIR="$WORK_ROOT/clean"
mkdir -p "$RAW_DIR" "$CLEAN_DIR"

echo "Using playlist URL: $PLAYLIST_URL"
echo "Work folder: $WORK_ROOT"
echo "Using ffmpeg tools from: $FFMPEG_DIR"

yt-dlp \
  --yes-playlist \
  --extract-audio \
  --audio-format mp3 \
  --audio-quality 0 \
  --ffmpeg-location "$FFMPEG_DIR" \
  -o "$RAW_DIR/%(playlist_index)03d - %(title).200B.%(ext)s" \
  "$PLAYLIST_URL"

shopt -s nullglob
raw_tracks=("$RAW_DIR"/*.mp3)

if (( ${#raw_tracks[@]} == 0 )); then
  echo "No tracks were downloaded. Check URL/access permissions." >&2
  exit 1
fi

echo "Cleaning ${#raw_tracks[@]} track(s)..."
valid_tracks=()
skipped_tracks=0
for in_file in "${raw_tracks[@]}"; do
  base="$(basename "$in_file")"
  out_file="$CLEAN_DIR/$base"
  echo "  -> $base"
  "$FFMPEG_BIN" -hide_banner -loglevel error -y \
    -i "$in_file" \
    -af "highpass=f=80,afftdn=nf=${NOISE_FLOOR_DB},silenceremove=start_periods=1:start_duration=0.30:start_threshold=${SILENCE_THRESH_DB}dB:stop_periods=-1:stop_duration=${MIN_SILENCE_SEC}:stop_threshold=${SILENCE_THRESH_DB}dB:stop_silence=${KEEP_SILENCE_SEC}:detection=rms,loudnorm=I=-16:LRA=11:TP=-1.5" \
    -c:a libmp3lame -q:a 2 \
    "$out_file" || true

  if ! is_valid_audio "$out_file"; then
    rm -f "$out_file"
    echo "     primary clean too aggressive; retrying with gentler filter"
    "$FFMPEG_BIN" -hide_banner -loglevel error -y \
      -i "$in_file" \
      -af "highpass=f=80,afftdn=nf=${NOISE_FLOOR_DB},loudnorm=I=-16:LRA=11:TP=-1.5" \
      -c:a libmp3lame -q:a 2 \
      "$out_file" || true
  fi

  if is_valid_audio "$out_file"; then
    valid_tracks+=("$out_file")
  else
    rm -f "$out_file"
    if is_valid_audio "$in_file"; then
      echo "     warning: cleaned output invalid; using original track"
      cp "$in_file" "$out_file"
      valid_tracks+=("$out_file")
    else
      echo "     warning: source track invalid; skipping track"
      skipped_tracks=$((skipped_tracks + 1))
    fi
  fi
done

if (( ${#valid_tracks[@]} == 0 )); then
  echo "Cleaning stage produced no valid tracks." >&2
  exit 1
fi

concat_list="$WORK_ROOT/concat.txt"
: > "$concat_list"
for f in "${valid_tracks[@]}"; do
  escaped="${f//\'/\'\\\'\'}"
  printf "file '%s'\n" "$escaped" >> "$concat_list"
done

if [[ -z "$OUTPUT_NAME" ]]; then
  OUTPUT_NAME="soundcloud-playlist-clean-$stamp.mp3"
fi
if [[ "$OUTPUT_NAME" != *.mp3 ]]; then
  OUTPUT_NAME="$OUTPUT_NAME.mp3"
fi

OUTPUT_PATH="$HOME/Downloads/$OUTPUT_NAME"
"$FFMPEG_BIN" -hide_banner -loglevel error -y \
  -f concat -safe 0 -i "$concat_list" \
  -c:a libmp3lame -q:a 2 \
  "$OUTPUT_PATH"

echo
echo "Done"
echo "Merged file: $OUTPUT_PATH"
echo "Intermediate files: $WORK_ROOT"
if (( skipped_tracks > 0 )); then
  echo "Skipped tracks: $skipped_tracks (invalid after cleaning)"
fi
