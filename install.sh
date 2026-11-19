#!/usr/bin/env bash
# Install the SourceFidelity skills into a coding harness skills directory.
#
#   ./install.sh [--target DIR] [--dry-run] [--no-venv]
#
# The installer does three things: copy the three skill folders, check for
# Python 3.12, and (unless --no-venv) build the shared .venv. It never
# installs Python, pip packages beyond pypdf/pymupdf, or OCR tools silently.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILLS=(sourcefidelity-review sourcefidelity-batch sourcefidelity-reappraise)
TARGET=""
DRY_RUN="0"
WITH_VENV="1"

usage() {
  sed -n '2,9p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

while [ $# -gt 0 ]; do
  case "$1" in
    --target) TARGET="${2:-}"; shift 2 ;;
    --dry-run) DRY_RUN="1"; shift ;;
    --no-venv) WITH_VENV="0"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [ -z "$TARGET" ]; then
  for candidate in "$HOME/.claude/skills" "$HOME/.agents/skills" \
                   "$HOME/.codex/skills" "$HOME/.cursor/skills" \
                   "$HOME/.dsh/skills"; do
    if [ -d "$candidate" ]; then TARGET="$candidate"; break; fi
  done
fi
if [ -z "$TARGET" ]; then
  echo "No harness skills directory found." >&2
  echo "Pass one explicitly, for example:" >&2
  echo "  ./install.sh --target \"\$HOME/.agents/skills\"" >&2
  exit 1
fi

PYTHON=""
for candidate in python3.12 python3; do
  if command -v "$candidate" >/dev/null 2>&1; then
    if "$candidate" -c 'import sys; raise SystemExit(0 if sys.version_info >= (3, 12) else 1)' 2>/dev/null; then
      PYTHON="$candidate"; break
    fi
  fi
done

echo "SourceFidelity skills installer"
echo "  target: $TARGET"
if [ "$DRY_RUN" = "1" ]; then echo "  mode:   dry run (nothing is written)"; fi
echo

for skill in "${SKILLS[@]}"; do
  if [ ! -d "$HERE/$skill" ]; then
    echo "missing skill folder: $HERE/$skill" >&2
    exit 1
  fi
  if [ "$DRY_RUN" = "1" ]; then
    echo "would install: $skill -> $TARGET/$skill"
  else
    mkdir -p "$TARGET/$skill"
    cp -R "$HERE/$skill/." "$TARGET/$skill/"
    echo "installed: $skill"
  fi
done

echo
if [ -z "$PYTHON" ]; then
  echo "Python 3.12 was not found. The skills still run in prompt-only mode:"
  echo "  counts, page mapping and verbatim checks are unavailable."
  echo "  Python: https://www.python.org/downloads/"
  echo "  Then re-run: ./install.sh --target \"$TARGET\""
else
  echo "Python found: $PYTHON"
  if [ "$WITH_VENV" = "1" ]; then
    # One venv serves all three skills: it is built beside sourcefidelity-review
    # and every script resolves it (an existing per-skill venv still wins).
    SHARED="$TARGET/sourcefidelity-review/.venv"
    if [ "$DRY_RUN" = "1" ]; then
      echo "would create: $SHARED (pypdf, pymupdf, zstandard, shared by all three skills)"
    elif "$SHARED/bin/python" -c "import pypdf, pymupdf, zstandard" >/dev/null 2>&1; then
      echo "venv ready (already complete): sourcefidelity-review"
    else
      echo "building the shared venv in sourcefidelity-review (pip downloads pypdf, pymupdf and zstandard)"
      "$PYTHON" -m venv "$SHARED"
      "$SHARED/bin/python" -m pip install --quiet --upgrade pip || true
      if "$SHARED/bin/python" -m pip install --quiet pypdf pymupdf zstandard; then
        echo "venv ready: sourcefidelity-review"
      else
        echo "the shared venv is incomplete; re-run this installer" >&2
        exit 1
      fi
    fi
  fi
  if ! command -v tesseract >/dev/null 2>&1; then
    echo "Tesseract OCR not found: image-only PDFs will be recorded as limitations."
    echo "  Install it from https://github.com/tesseract-ocr/tesseract"
    HINT_PY="python"
    if [ "$WITH_VENV" = "1" ]; then
      # A run that reached here either built the venv or is a dry run of one.
      HINT_PY="$TARGET/sourcefidelity-review/.venv/bin/python"
    fi
    echo "  Then add language data, for example:"
    echo "    \"$HINT_PY\" -I scripts/pdf_quality.py languages --install chi_sim"
  fi
  echo
  echo "Next: relaunch your harness and ask for a source-use review."
fi

echo "Optional: store a contact email and free API keys with"
echo "  $SHARED/bin/python -I scripts/user_settings.py --prompt"
