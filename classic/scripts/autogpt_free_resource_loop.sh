#!/usr/bin/env bash
set -euo pipefail

# AutoGPT free-resource loop:
# - tries multiple OpenAI-compatible free providers/models
# - updates classic/.env for each candidate
# - runs AutoGPT with timeout and retries forever by default

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT_DIR/.env"
BACKUP_FILE="$ROOT_DIR/.env.backup.free-loop"
LOG_DIR="${AUTOGPT_FREE_LOOP_LOG_DIR:-/tmp}"
TASK_FILE="${AUTOGPT_TASK_FILE:-}"

AUTOGPT_TIMEOUT_SECONDS="${AUTOGPT_TIMEOUT_SECONDS:-900}"
LOOP_DELAY_SECONDS="${LOOP_DELAY_SECONDS:-45}"
MAX_CYCLES="${MAX_CYCLES:-0}" # 0 means infinite

if [[ ! -f "$ENV_FILE" ]]; then
  echo "Missing env file: $ENV_FILE"
  exit 1
fi

if ! command -v poetry >/dev/null 2>&1; then
  echo "poetry is required"
  exit 1
fi

if [[ -n "$TASK_FILE" && ! -f "$TASK_FILE" ]]; then
  echo "Task file does not exist: $TASK_FILE"
  exit 1
fi

if [[ -n "$TASK_FILE" ]]; then
  TASK_CONTENT="$(cat "$TASK_FILE")"
else
  TASK_CONTENT="Acceseaza OLX Romania (olx.ro) si extrage preturile pentru Samsung Galaxy S24 Ultra second hand. Daca primesti 403/WAF, scrie si testeaza cod Python cu execute_shell pana gasesti o metoda care functioneaza. Returneaza: titlu, pret RON, link pentru fiecare anunt. Calculeaza: minim, maxim, medie, mediana."
fi

cp "$ENV_FILE" "$BACKUP_FILE"
cleanup() {
  if [[ -f "$BACKUP_FILE" ]]; then
    cp "$BACKUP_FILE" "$ENV_FILE"
  fi
}
trap cleanup EXIT

get_env_value() {
  local key="$1"
  local value=""
  value=$(grep -E "^${key}=" "$ENV_FILE" 2>/dev/null | tail -n1 | cut -d= -f2- || true)
  # strip optional surrounding quotes
  value="${value%\"}"
  value="${value#\"}"
  value="${value%\'}"
  value="${value#\'}"
  printf '%s' "$value"
}

set_env_value() {
  local key="$1"
  local value="$2"
  if grep -qE "^${key}=" "$ENV_FILE"; then
    sed -i "s|^${key}=.*|${key}=${value}|" "$ENV_FILE"
  else
    printf '%s=%s\n' "$key" "$value" >> "$ENV_FILE"
  fi
}

# name|base_url|model|api_key_var
CANDIDATES=(
  "current_env|$(get_env_value "OPENAI_API_BASE_URL")|$(get_env_value "SMART_LLM")|OPENAI_API_KEY"
  "gemini25|https://generativelanguage.googleapis.com/v1beta/openai/|gemini-2.5-flash|GEMINI_API_KEY"
  "openrouter_free|https://openrouter.ai/api/v1|openai/gpt-oss-20b:free|OPENROUTER_API_KEY"
  "groq_qwen|https://api.groq.com/openai/v1|qwen/qwen3-32b|GROQ_API_KEY"
  "groq_llama|https://api.groq.com/openai/v1|llama-3.3-70b-versatile|GROQ_API_KEY"
  "together_llama|https://api.together.xyz/v1|meta-llama/Llama-3.3-70B-Instruct-Turbo|TOGETHER_API_KEY"
)

resolve_key_value() {
  local key_var="$1"
  local from_env="${!key_var:-}"
  if [[ -n "$from_env" ]]; then
    printf '%s' "$from_env"
    return 0
  fi

  local from_file
  from_file="$(get_env_value "$key_var")"
  if [[ -n "$from_file" ]]; then
    printf '%s' "$from_file"
    return 0
  fi

  # Fallback only for direct OpenAI-compatible configured provider entries.
  if [[ "$key_var" == "GEMINI_API_KEY" || "$key_var" == "OPENAI_API_KEY" ]]; then
    printf '%s' "$(get_env_value "OPENAI_API_KEY")"
    return 0
  fi

  printf '%s' ""
}

probe_model() {
  local base_url="$1"
  local model="$2"
  local api_key="$3"

  local response_file
  response_file=$(mktemp)
  local code
  code=$(curl -s -o "$response_file" -w "%{http_code}" -X POST \
    "${base_url%/}/chat/completions" \
    -H "Authorization: Bearer $api_key" \
    -H "Content-Type: application/json" \
    -d "{\"model\":\"$model\",\"messages\":[{\"role\":\"user\",\"content\":\"ping\"}],\"max_tokens\":8}")

  if [[ "$code" == "200" ]]; then
    rm -f "$response_file"
    return 0
  fi

  local reason
  reason=$(grep -oE '"message"\s*:\s*"[^"]+' "$response_file" | head -n1 | sed 's/.*"message"\s*:\s*"//')
  if [[ -z "$reason" ]]; then
    reason=$(head -c 160 "$response_file" | tr '\n' ' ')
  fi
  echo "probe_http=$code reason=${reason:-unknown}"
  rm -f "$response_file"
  return 1
}

run_candidate() {
  local name="$1"
  local base_url="$2"
  local model="$3"
  local key_var="$4"

  if [[ -z "$base_url" || -z "$model" ]]; then
    echo "[$name] skipped: missing base_url/model"
    return 2
  fi

  local key
  key="$(resolve_key_value "$key_var")"
  if [[ -z "$key" ]]; then
    echo "[$name] skipped: missing key $key_var"
    return 2
  fi

  echo "[$name] probing model availability..."
  if ! probe_model "$base_url" "$model" "$key"; then
    echo "[$name] probe failed for model=$model"
    return 3
  fi

  echo "[$name] applying env and running AutoGPT"
  set_env_value "OPENAI_API_BASE_URL" "$base_url"
  set_env_value "OPENAI_API_KEY" "$key"
  set_env_value "SMART_LLM" "$model"
  set_env_value "FAST_LLM" "$model"
  set_env_value "NONINTERACTIVE_MODE" "true"
  set_env_value "EXECUTE_LOCAL_COMMANDS" "true"

  local ts
  ts=$(date +%Y%m%d_%H%M%S)
  local log_file="$LOG_DIR/autogpt_free_loop_${name}_${ts}.log"

  set +e
  cd "$ROOT_DIR"
  printf '%s\n' "$TASK_CONTENT" | timeout "${AUTOGPT_TIMEOUT_SECONDS}s" poetry run autogpt run --skip-news --skip-reprompt >"$log_file" 2>&1
  local status=$?
  set -e

  if grep -qiE "\bfinish\b|task complete|successfully completed|FINAL ANSWER" "$log_file"; then
    echo "[$name] success signal found in log: $log_file"
    return 0
  fi

  if [[ "$status" -eq 0 ]]; then
    echo "[$name] run finished without explicit success marker: $log_file"
    return 1
  fi

  echo "[$name] run failed with status $status: $log_file"
  return 4
}

cycle=0
while true; do
  cycle=$((cycle + 1))
  if [[ "$MAX_CYCLES" -gt 0 && "$cycle" -gt "$MAX_CYCLES" ]]; then
    echo "Reached MAX_CYCLES=$MAX_CYCLES"
    exit 1
  fi

  echo "=== Free resource cycle $cycle ==="
  for candidate in "${CANDIDATES[@]}"; do
    IFS='|' read -r name base_url model key_var <<< "$candidate"
    if run_candidate "$name" "$base_url" "$model" "$key_var"; then
      echo "Loop done: working candidate = $name"
      exit 0
    fi
  done

  echo "No candidate worked in this cycle. Retrying in ${LOOP_DELAY_SECONDS}s..."
  sleep "$LOOP_DELAY_SECONDS"
done
