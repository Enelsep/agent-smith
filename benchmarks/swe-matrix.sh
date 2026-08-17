# The model x task matrix behind BENCHMARK_REPORT.md, and the tables it feeds.
#
# Re-run it to continue: a run whose solution file already exists is skipped,
# so a pass stopped by a rate limit, a full disk or a closed laptop resumes on
# the identical command. Delete one solution file to redo that one run. The
# same property is why there is no separate reporting mode -- re-running a
# finished matrix launches nothing and prints the tables.
#
# `models.json` must catalogue every provider named below. The stop sequences
# and the per-call token cap come from it, and a provider it does not know runs
# without either -- the model then writes past <end_code> and answers its own
# observations, which is not a number worth reporting.
#
# Tasks loop on the outside on purpose: one image serves every model, is pulled
# once, and is dropped before the next task. The images are several GB each, so
# that ordering is what decides whether a three-task pass fits on the disk.

cd "$(dirname "$0")/.." || exit 1
OUT=${OUT:-benchmarks/runs}

# Overridable so a pass can be staged one task at a time, which is how the
# disk survives: an image is several GB and only one has to be resident.
# shellcheck disable=SC2206
TASKS=(${TASKS:-django__django-11066 sympy__sympy-14711 sympy__sympy-13480})
# Overridable the same way, so a campaign can hold the model axis fixed against
# an earlier one and change a single thing. One entry per line: URL then model.
if [ -n "${MODELS:-}" ]; then
  mapfile -t MODELS <<< "$MODELS"
else
MODELS=(
  "https://api.mistral.ai/v1 mistral-medium-latest"
  "https://api.mistral.ai/v1 devstral-medium-latest"
  "https://api.mistral.ai/v1 codestral-2508"
  "https://api.mistral.ai/v1 magistral-small-latest"
  "https://openrouter.ai/api/v1 qwen/qwen3-235b-a22b-2507"
)
fi

# A model name carries slashes; a directory name cannot.
slug() { echo "$1" | tr '/' '-'; }

mkdir -p "$OUT/tasks"

for id in "${TASKS[@]}"; do
  T="$OUT/tasks/$id.json"
  if [ ! -s "$T" ]; then
    # Resolved here rather than in the command below, because a substitution
    # inside that subshell runs after the `cd` and would resolve a relative OUT
    # against moulinette/ -- where it does not exist, leaving the directory
    # empty and the dump writing to the filesystem root.
    ABS="$(cd "$(dirname "$T")" && pwd)/$(basename "$T")"
    (cd moulinette && uv run moulinette_eval dump swebench --task_id "$id" \
      --output "$ABS") || exit 1
  fi

  for m in "${MODELS[@]}"; do
    read -r URL MODEL <<< "$m"
    S="$OUT/$(slug "$MODEL")/$id.json"
    if [ -s "$S" ]; then
      echo "=== $MODEL $id skipped ==="
      continue
    fi
    echo "=== $MODEL $id ==="
    mkdir -p "$(dirname "$S")"
    uv run python -m agent_swebench --task-file "$T" --output "$S" \
      --env-file .env --provider-url "$URL" --model-name "$MODEL"
  done

  # Docker caches images perfectly well; this drops them because three
  # SWE-bench images at several GB each do not fit beside one another on a
  # laptop. Set DROP_IMAGES=0 where there is room, and a re-run of the same
  # task costs no download.
  if [ "${DROP_IMAGES:-1}" = 1 ]; then
    docker rmi "$(jq -r .docker_image "$T")" >/dev/null 2>&1 && echo "=== image dropped ==="
  fi
done

printf '\n| model | solved | input tokens | seconds |\n| --- | --- | --- | --- |\n'
for m in "${MODELS[@]}"; do
  read -r _ MODEL <<< "$m"
  FILES=("$OUT/$(slug "$MODEL")"/*.json)
  [ -s "${FILES[0]}" ] || { echo "| $MODEL | 0/0 | - | - |"; continue; }
  jq -rs --arg model "$MODEL" '"| \($model) | \(map(select(.success))|length)/\(length)"
    + " | \(map(.total_input_tokens)|add) | \(map(.total_time_seconds)|add|round) |"' "${FILES[@]}"
done

printf '\n| model | task | solved | iters | reqs | in | out | s | error |\n'
printf -- '| --- | --- | --- | --- | --- | --- | --- | --- | --- |\n'
for m in "${MODELS[@]}"; do
  read -r _ MODEL <<< "$m"
  for id in "${TASKS[@]}"; do
    S="$OUT/$(slug "$MODEL")/$id.json"
    # A cell with no file is a run that never happened, which is not the same
    # thing to report as a run that solved nothing.
    [ -s "$S" ] || { echo "| $MODEL | $id | - | - | - | - | - | - | not run |"; continue; }
    jq -r --arg model "$MODEL" '"| \($model) | \(.task_id) | \(if .success then "yes" else "no" end)"
      + " | \(.iterations) | \(.total_requests) | \(.total_input_tokens)"
      + " | \(.total_output_tokens) | \(.total_time_seconds|round) | \(.error // "") |"' "$S"
  done
done
