#!/usr/bin/env bash
: '=======================================================
Refresh from github

Gets the latest version of this app from github
=========================================================='

set -euo pipefail

# --- Safety & Portability Guards -------------------------------------------
# This script aims to be reusable across projects.
# Customize behaviour via environment variables (export before running) or by
# creating marker files in a development workspace to prevent accidental runs.
#
# Environment overrides:
#   ALLOW_DEV_REFRESH=1          Force execution even if a dev marker or block rule triggers.
#   BLOCK_MARKERS=".dev_workspace:.development"  Colon list of files/dirs at repo root that block execution.
#   REQUIRE_MARKERS=".deployment:.prod"  Colon list of files; at least one must exist (if list non-empty) or script aborts.
#   BLOCK_PATH_PATTERNS="pattern1:pattern2"  Colon list of substrings; if REPO_ROOT matches any -> block.
#   REQUIRE_REMOTE_HOST=github.com   If set, require 'origin' remote URL to contain this string.
#   STASH_BEFORE_REFRESH=1       (default 1) If 1, stash uncommitted changes automatically.
#   BRANCH=main                  Branch to reset to (default: main)
#
# To block refresh in a development clone, create an empty file named
# (by default) .dev_workspace in the repository root.

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd -P)"

# if BLOCK_PATH_PATTERNS is not set, default to Development
BLOCK_PATH_PATTERNS="${BLOCK_PATH_PATTERNS:-Development}"

# 1. Ensure we are inside a git working tree
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  echo "[Refresh] Error: Not inside a git working tree." >&2
  exit 3
fi

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || echo "")"
if [[ -z "$REPO_ROOT" ]]; then
  echo "[Refresh] Error: Unable to determine repo root." >&2
  exit 3
fi

# 2a. Block if any marker file exists unless override set
BLOCK_MARKERS_DEFAULT=".gitignore:.dev_workspace:.development:.local_dev"
BLOCK_MARKERS="${BLOCK_MARKERS:-$BLOCK_MARKERS_DEFAULT}"
IFS=":" read -r -a _markers <<<"$BLOCK_MARKERS"
for m in "${_markers[@]}"; do
  if [[ -n "$m" && -e "$REPO_ROOT/$m" ]]; then
    if [[ "${ALLOW_DEV_REFRESH:-}" != "1" ]]; then
      echo "[Refresh] Refusing to run: dev marker '$m' found at repo root ($REPO_ROOT)." >&2
      echo "Set ALLOW_DEV_REFRESH=1 to override (not recommended)." >&2
      exit 99
    else
      echo "[Refresh] ALLOW_DEV_REFRESH=1 set; ignoring dev marker '$m'." >&2
    fi
  fi
done

# 2b. Block based on path pattern match (optional)
if [[ -n "${BLOCK_PATH_PATTERNS:-}" ]]; then
  IFS=":" read -r -a _block_paths <<<"$BLOCK_PATH_PATTERNS"
  for p in "${_block_paths[@]}"; do
    [[ -z "$p" ]] && continue
    if [[ "$REPO_ROOT" == *"$p"* ]]; then
      if [[ "${ALLOW_DEV_REFRESH:-}" != "1" ]]; then
        echo "[Refresh] Refusing to run: repo path '$REPO_ROOT' matches blocked pattern '$p'." >&2
        exit 100
      else
        echo "[Refresh] ALLOW_DEV_REFRESH=1 set; ignoring blocked path pattern '$p'." >&2
      fi
    fi
  done
fi

# 2c. Require at least one deployment marker (if list provided)
REQUIRE_MARKERS_DEFAULT=""  # Empty by default (no requirement unless user sets)
REQUIRE_MARKERS="${REQUIRE_MARKERS:-$REQUIRE_MARKERS_DEFAULT}"
if [[ -n "$REQUIRE_MARKERS" ]]; then
  IFS=":" read -r -a _req_markers <<<"$REQUIRE_MARKERS"
  _found_req=0
  for rm in "${_req_markers[@]}"; do
    [[ -z "$rm" ]] && continue
    if [[ -e "$REPO_ROOT/$rm" ]]; then
      _found_req=1
      break
    fi
  done
  if [[ $_found_req -eq 0 ]]; then
    if [[ "${ALLOW_DEV_REFRESH:-}" != "1" ]]; then
      echo "[Refresh] Refusing to run: none of the required markers ($REQUIRE_MARKERS) found at repo root ($REPO_ROOT)." >&2
      echo "Create one of these files (e.g. 'touch .deployment') in deployment clones, or set ALLOW_DEV_REFRESH=1 to override." >&2
      exit 101
    else
      echo "[Refresh] ALLOW_DEV_REFRESH=1 set; proceeding without required markers ($REQUIRE_MARKERS)." >&2
    fi
  fi
fi

# 3. Remote origin validation (optional)
remote_url="$(git config --get remote.origin.url || true)"
if [[ -z "$remote_url" ]]; then
  echo "[Refresh] Error: No 'origin' remote configured." >&2
  exit 4
fi
if [[ -n "${REQUIRE_REMOTE_HOST:-}" && "$remote_url" != *"$REQUIRE_REMOTE_HOST"* ]]; then
  echo "[Refresh] Error: origin remote ('$remote_url') does not match REQUIRED_REMOTE_HOST='$REQUIRE_REMOTE_HOST'." >&2
  exit 5
fi

# 4. Optionally stash uncommitted changes
if [[ "${STASH_BEFORE_REFRESH:-1}" == "1" ]]; then
  if ! git diff --quiet || ! git diff --cached --quiet; then
    echo "[Refresh] Uncommitted changes detected; stashing before reset." >&2
    git stash push -u -m "pre-refresh $(date -u +%Y%m%dT%H%M%SZ)" >/dev/null 2>&1 || echo "[Refresh] Warning: git stash failed." >&2
  fi
fi

# 5. Target branch
BRANCH="${BRANCH:-main}"

# Find uv reliably (systemd often has a minimal PATH)
if command -v uv >/dev/null 2>&1; then
  UVCmd="$(command -v uv)"
elif [ -x "$HOME/.local/bin/uv" ]; then
  UVCmd="$HOME/.local/bin/uv"
else
  echo "[Refresh from Github] Error: 'uv' not found in PATH or at \$HOME/.local/bin/uv" >&2
  exit 1
fi

echo "[Refresh from Github] Starting refresh from branch '$BRANCH'"

if ! git fetch origin; then
  echo "[Refresh from Github] Error: git fetch failed." >&2
  exit 1
fi

if ! git reset --hard "origin/$BRANCH"; then
  echo "[Refresh from Github] Error: git reset failed." >&2
  exit 1
fi

# Make sure deps are synced
if ! "$UVCmd" sync; then
  echo "[Refresh from Github] uv sync failed." >&2
  exit 2
fi
