#!/usr/bin/env bash
# Sync the repository into the installed Hermes plugin directory, refusing to
# destroy uncommitted work there.
#
# Why this exists: the installed plugin is a full git clone, so it can hold
# commits and edits the repository does not. A plain `cp` overwrites them with
# no diff, no error and no backup. That nearly cost the multi-account model
# flow once already.
#
# Refuses unless the installed tree is clean, unless a prior commit there is
# contained in origin/main, or unless --force is given. Never writes secrets.
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${HERMES_HOME:-$HOME/.hermes}/plugins/model-providers/antigravity"
FORCE=0
DRY_RUN=0

usage() {
  cat <<EOF
Usage: $(basename "$0") [--dest DIR] [--force] [--dry-run] [--help]

  --dest DIR   installed plugin directory (default: $DEST)
  --force      overwrite even if the destination has uncommitted work
  --dry-run    report what would change, write nothing
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --dest) DEST="$2"; shift 2 ;;
    --force) FORCE=1; shift ;;
    --dry-run) DRY_RUN=1; shift ;;
    --help|-h) usage; exit 0 ;;
    *) echo "unknown argument: $1" >&2; usage >&2; exit 2 ;;
  esac
done

cd "$REPO_ROOT"
SRC_COMMIT="$(git rev-parse --short HEAD 2>/dev/null || echo unknown)"
echo "source:  $REPO_ROOT @ $SRC_COMMIT"
echo "target:  $DEST"

if [ ! -d "$DEST" ]; then
  echo "target does not exist; install with:"
  echo "  git clone https://github.com/zeyxx/hermes-antigravity $DEST"
  exit 1
fi

# --- the gate -------------------------------------------------------------
if [ -d "$DEST/.git" ]; then
  dirty="$(git -C "$DEST" status --porcelain 2>/dev/null || true)"
  if [ -n "$dirty" ]; then
    echo
    echo "REFUSING: the installed plugin has uncommitted changes:"
    echo "$dirty" | sed 's/^/  /'
    echo
    echo "Those edits exist nowhere else. Choose one:"
    echo "  - commit them in $DEST, then re-run"
    echo "  - stash them:  git -C $DEST stash"
    echo "  - discard them (only if the repository already contains them):"
    echo "      $(basename "$0") --force"
    exit 1
  fi

  # Compare against the installed clone's OWN upstream, not origin/main: it may sit
  # on a feature branch, and its commits can already exist upstream.
  head_branch="$(git -C "$DEST" rev-parse --abbrev-ref HEAD 2>/dev/null || echo unknown)"
  upstream_ref=""
  if [ "$head_branch" != "HEAD" ]; then
    if git -C "$DEST" rev-parse --verify -q "origin/$head_branch" >/dev/null 2>&1; then
      upstream_ref="origin/$head_branch"
    elif git -C "$DEST" rev-parse --verify -q origin/main >/dev/null 2>&1; then
      upstream_ref="origin/main"
    fi
  fi
  ahead=""
  if [ -n "$upstream_ref" ]; then
    ahead="$(git -C "$DEST" log --oneline "$upstream_ref..HEAD" 2>/dev/null || true)"
    if [ -n "$ahead" ]; then
      echo
      echo "REFUSING: the installed plugin ($head_branch) has $(echo "$ahead" | wc -l | tr -d ' ') commit(s) not in $upstream_ref:"
      echo "$ahead" | sed 's/^/  /'
      echo
      echo "Port them into the repository first, or discard with --force."
      exit 1
    fi
  else
    echo "note: no upstream branch found for $head_branch; skipping the commit check"
  fi

  if [ "$FORCE" -eq 0 ]; then
    sha_before="$(git -C "$DEST" rev-parse --short HEAD 2>/dev/null || echo none)"
    if [ "$sha_before" != "$SRC_COMMIT" ] && [ "$sha_before" != none ]; then
      echo
      echo "REFUSING: installed plugin is at $sha_before, repository is at $SRC_COMMIT."
      echo "That is expected after a pull. Re-run with --force to update it, or --dry-run to inspect."
      exit 1
    fi
  fi
else
  echo "note: target is not a git clone; only --dry-run can verify its contents"
fi

# --- what would change ---------------------------------------------------
FILES="__init__.py models.py client.py accounts.py auth.py translator.py plugin.yaml pyproject.toml README.md README.fr.md UPSTREAM_DRIFT.md LICENSE"
changed=0
for f in $FILES; do
  src="$REPO_ROOT/$f"
  [ -f "$src" ] || continue
  dst="$DEST/$f"
  if [ ! -f "$dst" ]; then
    echo "  + $f (new)"; changed=$((changed+1)); continue
  fi
  if ! cmp -s "$src" "$dst"; then
    n="$(diff "$dst" "$src" 2>/dev/null | grep -c '^[<>]' || echo 0)"
    echo "  ~ $f ($n lines differ)"; changed=$((changed+1))
  fi
done
echo "$changed file(s) would change"

if [ "$DRY_RUN" -eq 1 ]; then
  echo "dry run: nothing written."
  exit 0
fi
if [ "$changed" -eq 0 ]; then
  echo "already in sync."
  exit 0
fi

# --- write ---------------------------------------------------------------
for f in $FILES; do
  [ -f "$REPO_ROOT/$f" ] && cp "$REPO_ROOT/$f" "$DEST/$f"
done
find "$DEST" -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

if [ -d "$DEST/.git" ]; then
  git -C "$DEST" rev-parse --short HEAD >/dev/null 2>&1 && \
  git -C "$DEST" checkout -- "$FILES" 2>/dev/null || true
fi

echo "synced $changed file(s). Verify with: hermes doctor"
