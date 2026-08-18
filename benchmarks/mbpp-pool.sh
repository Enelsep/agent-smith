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

# The pool is whatever task dumps sit under $OUT/tasks. The 257 of the MBPP test
# split are committed there, so a re-run needs nothing else; any other set is
# passed in as IDS= and dumped below.
IDS=${IDS:-$(ls "$OUT/tasks" 2>/dev/null | sed 's/\.json$//' | sort -n | tr '\n' ' ')}
if [ -z "${IDS// /}" ]; then
  echo "no task dumps under $OUT/tasks, and no IDS= given" >&2
  exit 1
fi

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
