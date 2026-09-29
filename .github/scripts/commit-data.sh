#!/usr/bin/env bash
# Commit changes under the given paths and push to main.
# Usage: commit-data.sh "message" path [path...]
set -euo pipefail
message="$1"; shift
git config user.name "github-actions[bot]"
git config user.email "41898282+github-actions[bot]@users.noreply.github.com"
git add -- "$@"
if git diff --cached --quiet; then
  echo "No changes in $*."
  exit 0
fi
git commit -m "$message $(TZ=America/New_York date +%Y-%m-%d)"
ours=$(git rev-parse HEAD)

# Replays the data commit on top of main. When another run has committed the
# same town's data since this job checked out (a run for that town that
# finished first), the two are merged file by file: whatever only one of them
# changed is kept, and where both changed the same lines this job's lines win,
# since it fetched later. A JSON file that the merge leaves unreadable is
# replaced by this job's copy.
replay() {
  if git rebase origin/main; then
    return 0
  fi
  git rebase --abort
  echo "::warning::Another run committed this data since the checkout; keeping this run's changes where both changed the same lines."
  if ! git rebase -X theirs origin/main; then
    git rebase --abort
    return 1
  fi
  local changed=() file
  mapfile -t changed < <(git diff --name-only origin/main HEAD -- "$@" | grep '\.json$' || true)
  for file in "${changed[@]}"; do
    [ -f "$file" ] || continue
    if ! python3 -c 'import json, sys; json.load(open(sys.argv[1], encoding="utf-8"))' "$file" 2>/dev/null; then
      echo "::warning::$file didn't merge cleanly; keeping this run's copy."
      git show "$ours:$file" > "$file"
      git add -- "$file"
    fi
  done
  git diff --cached --quiet || git commit --amend --no-edit --quiet
}

# COMMIT_ATTEMPTS raises the tries for a workflow with many jobs pushing at
# once; the random wait keeps them from retrying together.
attempts="${COMMIT_ATTEMPTS:-3}"
for attempt in $(seq 1 "$attempts"); do
  if git fetch --quiet origin main && replay "$@" && git push origin HEAD:main; then
    exit 0
  fi
  sleep $((attempt * 10 + RANDOM % 10))
done
echo "Could not push data commit." >&2
exit 1
