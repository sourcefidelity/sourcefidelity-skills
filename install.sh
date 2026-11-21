#!/usr/bin/env bash
# Install the SourceFidelity skills into a coding harness skills directory.
#
#   ./install.sh [--target DIR] [--dev] [--link] [--force]
#                [--dry-run] [--no-venv] [--check] [--update]
#                [--channel NAME] [--version TAG]
#
# The default is a development install: copy the working tree, record channel
# dev, and leave VERSION/latest.json alone. --link symlinks the skill folders
# to the checkout instead (macOS/Linux; --force replaces existing folders and
# their venv). --check asks whether a newer release exists; --update downloads
# the release named in latest.json, verifies its checksum, and installs it.
# It never installs Python, pip packages beyond pypdf/pymupdf, or OCR tools
# silently.
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILLS=(sourcefidelity-review sourcefidelity-batch sourcefidelity-reappraise)
TARGET=""
DRY_RUN="0"
WITH_VENV="1"
CHANNEL="dev"
DEV="0"
LINK="0"
FORCE="0"
CHECK="0"
UPDATE="0"
ALLOW_UNVERIFIED="0"
PIN_VERSION=""

usage() {
  sed -n '2,9p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//'
}

while [ $# -gt 0 ]; do
  case "$1" in
    --target) TARGET="${2:-}"; shift 2 ;;
    --dev) DEV="1"; shift ;;
    --link) LINK="1"; DEV="1"; shift ;;
    --force) FORCE="1"; shift ;;
    --dry-run) DRY_RUN="1"; shift ;;
    --no-venv) WITH_VENV="0"; shift ;;
    --check) CHECK="1"; shift ;;
    --update) UPDATE="1"; shift ;;
    --channel) CHANNEL="${2:-}"; shift 2 ;;
    --version) PIN_VERSION="${2:-}"; shift 2 ;;
    --allow-unverified) ALLOW_UNVERIFIED="1"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) echo "unknown option: $1" >&2; usage >&2; exit 2 ;;
  esac
done

if [ "$DEV" = "1" ]; then
  CHANNEL="dev"
  WITH_VENV="0"
fi
if [ "$LINK" = "1" ]; then
  case "$(uname -s)" in
    Darwin|Linux) : ;;
    *) echo "--link is available on macOS and Linux; use --dev (copy) on this platform" >&2; exit 2 ;;
  esac
fi

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

LATEST_URL="${SOURCEFIDELITY_LATEST_URL:-https://raw.githubusercontent.com/sourcefidelity/sourcefidelity-skills/main/latest.json}"

target_python() {
  local candidate="$TARGET/sourcefidelity-review/.venv/bin/python"
  if [ -x "$candidate" ]; then printf '%s' "$candidate"; else printf '%s' "$PYTHON"; fi
}

installed_version() {
  local json="$TARGET/.sourcefidelity/installed.json"
  if [ -f "$json" ]; then
    "$(target_python)" -c 'import json,sys; print(json.load(open(sys.argv[1])).get("version",""))' "$json" 2>/dev/null || true
  elif [ -f "$TARGET/sourcefidelity-review/VERSION" ]; then
    cat "$TARGET/sourcefidelity-review/VERSION"
  fi
}

newer_than() {
  "$1" - "$2" "$3" <<'PY'
import re, sys
def ver(value):
    match = re.search(r"(\d+)\.(\d+)\.(\d+)", value or "")
    return tuple(int(part) for part in match.groups()) if match else (0, 0, 0)
sys.exit(0 if ver(sys.argv[2]) > ver(sys.argv[1]) else 1)
PY
}

sha256_of() {
  if command -v shasum >/dev/null 2>&1; then shasum -a 256 "$1" | awk '{print $1}';
  elif command -v sha256sum >/dev/null 2>&1; then sha256sum "$1" | awk '{print $1}';
  else printf ''; fi
}

echo "SourceFidelity skills installer"
echo "  target: $TARGET"
if [ "$DRY_RUN" = "1" ]; then echo "  mode:   dry run (nothing is written)"; fi
echo

if [ -n "$PIN_VERSION" ]; then
  WANT="${PIN_VERSION#v}"
  HAVE="$(cat "$HERE/VERSION" 2>/dev/null || echo unknown)"
  if [ "$WANT" != "$HAVE" ]; then
    echo "this checkout is $HAVE, not $WANT; check out tag v$WANT first" >&2
    exit 1
  fi
  CHANNEL="tag:$PIN_VERSION"
fi

if [ "$CHECK" = "1" ]; then
  PYBIN="$(target_python)"
  CHECKER="$TARGET/sourcefidelity-review/scripts/update_check.py"
  if [ -z "$PYBIN" ] || [ ! -f "$CHECKER" ]; then
    echo "no installed skill found at $TARGET; run the installer first" >&2
    exit 1
  fi
  RESULT="$("$PYBIN" -I "$CHECKER" --check --json 2>/dev/null || true)"
  if [ -z "$RESULT" ]; then
    echo "Could not check for updates (offline or latest.json unavailable)." >&2
    exit 1
  fi
  NOTICE="$(printf '%s' "$RESULT" | "$PYBIN" -c 'import json,sys; print(json.load(sys.stdin).get("notice") or "")')"
  FAILED="$(printf '%s' "$RESULT" | "$PYBIN" -c 'import json,sys; print("yes" if json.load(sys.stdin).get("fetch_failed") else "no")')"
  if [ -n "$NOTICE" ]; then
    echo "$NOTICE"
  elif [ "$FAILED" = "yes" ]; then
    echo "Could not check for updates (offline or latest.json unavailable)." >&2
    exit 1
  else
    echo "SourceFidelity is up to date."
  fi
  exit 0
fi

if [ "$UPDATE" = "1" ]; then
  PYBIN="$(target_python)"
  if [ -z "$PYBIN" ]; then
    echo "Python 3.12 is required for --update" >&2
    exit 1
  fi
  TMP="$(mktemp -d)"
  trap 'rm -rf "$TMP"' EXIT
  if ! curl -fsSL --max-time 15 "$LATEST_URL" -o "$TMP/latest.json"; then
    echo "could not read $LATEST_URL" >&2
    exit 1
  fi
  VERSION="$("$PYBIN" -c 'import json,sys; print(json.load(open(sys.argv[1])).get("version",""))' "$TMP/latest.json")"
  URL="$("$PYBIN" -c 'import json,sys; print(json.load(open(sys.argv[1])).get("url",""))' "$TMP/latest.json")"
  SHA="$("$PYBIN" -c 'import json,sys; print(json.load(open(sys.argv[1])).get("sha256",""))' "$TMP/latest.json")"
  HAVE="$(installed_version)"
  if [ -z "$VERSION" ]; then
    echo "latest.json is missing version" >&2
    exit 1
  fi
  if [ -n "$HAVE" ] && ! newer_than "$PYBIN" "$HAVE" "$VERSION"; then
    echo "SourceFidelity $HAVE is up to date (latest $VERSION)."
    exit 0
  fi
  if [ -z "$URL" ]; then
    echo "latest.json is missing url for $VERSION" >&2
    exit 1
  fi
  echo "downloading SourceFidelity $VERSION"
  if ! curl -fsSL --max-time 120 "$URL" -o "$TMP/pkg.tar.gz"; then
    echo "could not download $URL" >&2
    exit 1
  fi
  if [ -n "$SHA" ]; then
    GOT="$(sha256_of "$TMP/pkg.tar.gz")"
    if [ "$GOT" != "$SHA" ]; then
      echo "checksum mismatch for $URL" >&2
      exit 1
    fi
  elif [ "$ALLOW_UNVERIFIED" != "1" ]; then
    echo "latest.json has no sha256; refusing an unverified update" >&2
    echo "publish a checksum, or pass --allow-unverified deliberately" >&2
    exit 1
  fi
  mkdir -p "$TMP/src"
  if ! tar -xzf "$TMP/pkg.tar.gz" -C "$TMP/src" --strip-components=1; then
    echo "could not extract the release archive" >&2
    exit 1
  fi
  bash "$TMP/src/install.sh" --target "$TARGET" --no-venv --channel stable
  exit $?
fi

for skill in "${SKILLS[@]}"; do
  if [ ! -d "$HERE/$skill" ]; then
    echo "missing skill folder: $HERE/$skill" >&2
    exit 1
  fi
  if [ "$DRY_RUN" = "1" ]; then
    if [ "$LINK" = "1" ]; then
      echo "would link: $skill -> $HERE/$skill"
    else
      echo "would install: $skill -> $TARGET/$skill"
    fi
  elif [ "$LINK" = "1" ]; then
    if [ -L "$TARGET/$skill" ] && [ "$(readlink "$TARGET/$skill")" = "$HERE/$skill" ]; then
      echo "already linked: $skill"
      continue
    fi
    if [ -e "$TARGET/$skill" ]; then
      if [ "$FORCE" != "1" ]; then
        echo "$TARGET/$skill exists; pass --force to replace it with a link (its venv is removed)" >&2
        exit 1
      fi
      rm -rf "$TARGET/$skill"
    fi
    mkdir -p "$TARGET"
    ln -s "$HERE/$skill" "$TARGET/$skill"
    echo "linked: $skill -> $HERE/$skill"
  else
    mkdir -p "$TARGET/$skill"
    cp -R "$HERE/$skill/." "$TARGET/$skill/"
    echo "installed: $skill"
  fi
done

if [ "$DRY_RUN" = "0" ]; then
  mkdir -p "$TARGET/.sourcefidelity"
  VERSION="$(cat "$HERE/VERSION" 2>/dev/null || echo unknown)"
  COMMIT="$(git -C "$HERE" rev-parse --short HEAD 2>/dev/null || true)"
  STAMP="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
  if [ -n "$PYTHON" ]; then
    "$PYTHON" - "$TARGET/.sourcefidelity/installed.json" "$VERSION" "$COMMIT" "$CHANNEL" "$HERE" "$STAMP" <<'PY'
import json, sys
path, version, commit, channel, source, stamp = sys.argv[1:7]
with open(path, "w", encoding="utf-8", newline="\n") as handle:
    json.dump({"version": version, "commit": commit, "channel": channel,
               "source": source, "installed_at": stamp}, handle, indent=1)
    handle.write("\n")
PY
  else
    printf '{\n "version": "%s",\n "commit": "%s",\n "channel": "%s",\n "source": "%s",\n "installed_at": "%s"\n}\n' \
      "$VERSION" "$COMMIT" "$CHANNEL" "$HERE" "$STAMP" > "$TARGET/.sourcefidelity/installed.json"
  fi
  echo "recorded: v$VERSION ($CHANNEL)"
fi

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

if [ "$DEV" = "1" ] && [ "$LINK" = "1" ]; then
  echo "Linked development install: edits in $HERE are live."
  echo "The shared venv, if needed, is $HERE/sourcefidelity-review/.venv."
elif [ "$DEV" = "1" ]; then
  echo "Development install: re-run this command after editing; no release is needed."
fi

if [ -n "${SHARED:-}" ]; then
  echo "Optional: store a contact email and free API keys with"
  echo "  $SHARED/bin/python -I scripts/user_settings.py --prompt"
fi
