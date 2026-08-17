# One model over the whole MBPP test pool, for BENCHMARK_REPORT.md's MBPP figure.
#
# Re-run it to continue: a task whose solution file already exists is skipped,
# so a pass stopped by a rate limit or a closed laptop resumes on the identical
# command. Delete one solution file to redo that one task.
#
# Scoring is deliberately not here. `.success` is the agent's own validator;
# the number that counts is `moulinette_eval validate mbpp`, which costs no API
# quota and can be re-run on the same files as often as needed.

cd "$(dirname "$0")/.." || exit 1
OUT=${OUT:-benchmarks/mbpp}
URL=${URL:-https://api.mistral.ai/v1}
MODEL=${MODEL:-codestral-2508}

# A model name can carry a slash; a directory name cannot.
SLUG=$(echo "$MODEL" | tr '/' '-')
mkdir -p "$OUT/tasks" "$OUT/$SLUG"

# The pool the moulinette dumps from, so the set matches what the exam draws.
IDS=${IDS:-$(cd moulinette && uv run python -c "
from moulinette.mbpp import InteractMBPP
print(' '.join(str(i) for i in InteractMBPP().list_tasks(split='test')))
" 2>/dev/null)}

for id in $IDS; do
  T="$OUT/tasks/$id.json"
  if [ ! -s "$T" ]; then
    # Resolved before the subshell: a relative OUT would otherwise resolve
    # against moulinette/, where it does not exist.
    ABS="$(cd "$(dirname "$T")" && pwd)/$(basename "$T")"
    (cd moulinette && uv run moulinette_eval dump mbpp --task_id "$id" \
      --output "$ABS") >/dev/null || exit 1
  fi

  S="$OUT/$SLUG/$id.json"
  [ -s "$S" ] && continue
  echo "=== $MODEL $id ==="
  uv run python -m agent_mbpp --task-file "$T" --output "$S" \
    --env-file .env --provider-url "$URL" --model-name "$MODEL"
done

echo "--- $MODEL: $(ls "$OUT/$SLUG" | wc -l) runs written to $OUT/$SLUG ---"
