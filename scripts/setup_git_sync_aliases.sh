#!/usr/bin/env bash
set -euo pipefail

# Sets local git aliases for safe upstream sync workflow.
# Run inside your codex-lb clone.

if ! git rev-parse --git-dir >/dev/null 2>&1; then
  echo "[ERROR] Not inside a git repository" >&2
  exit 1
fi

echo "[INFO] Configuring local git aliases..."

git config --local alias.sync-check '!git remote -v && echo && git status -sb'
git config --local alias.sync-fetch '!git checkout main && git pull --ff-only origin main && git fetch upstream'
git config --local alias.sync-branch '!f(){ b="codex/sync-upstream-$(date +%Y%m%d)"; git checkout -b "$b" && echo "$b"; }; f'
git config --local alias.sync-merge-upstream 'merge upstream/main'
git config --local alias.sync-main '!f(){ b=${1:?usage: git sync-main <sync-branch>}; git checkout main && git merge --no-ff "$b"; }; f'
git config --local alias.sync-publish 'push origin HEAD'
git config --local alias.sync-log 'log --graph --decorate --oneline -n 25'

echo "[OK] Aliases configured (local repo only):"
git config --local --get-regexp '^alias\.sync-' | sed 's/^/  /'
