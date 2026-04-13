#!/usr/bin/env bash
set -euo pipefail

# Full self-loop orchestration:
# 1) keep trying to allocate Ampere capacity in OCI Milan
# 2) wait for SSH readiness
# 3) bootstrap and run AutoGPT on the new instance
# 4) if any step fails, restart from capacity search

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SETUP_SCRIPT="$ROOT_DIR/scripts/setup_oci_ampere.sh"
DEPLOY_SCRIPT="$ROOT_DIR/scripts/deploy_autogpt_to_ampere.sh"

if [[ ! -x "$SETUP_SCRIPT" ]]; then
  echo "Missing executable setup script: $SETUP_SCRIPT"
  exit 1
fi
if [[ ! -x "$DEPLOY_SCRIPT" ]]; then
  echo "Missing executable deploy script: $DEPLOY_SCRIPT"
  exit 1
fi

required_vars=(
  OCI_USER_OCID
  OCI_FINGERPRINT
  OCI_TENANCY_OCID
  OCI_REGION
  OCI_SSH_PUBLIC_KEY_FILE
)

missing=0
for v in "${required_vars[@]}"; do
  if [[ -z "${!v:-}" ]]; then
    echo "Missing environment variable: ${v}"
    missing=1
  fi
done
if [[ "$missing" -ne 0 ]]; then
  exit 1
fi

if [[ -z "${OCI_KEY_FILE:-}" && -z "${OCI_PRIVATE_KEY_CONTENT:-}" && -z "${OCI_PRIVATE_KEY_B64:-}" ]]; then
  echo "Missing OCI private key input. Set OCI_KEY_FILE or OCI_PRIVATE_KEY_CONTENT or OCI_PRIVATE_KEY_B64"
  exit 1
fi

# Optional orchestration knobs
REMOTE_SSH_USER="${REMOTE_SSH_USER:-ubuntu}"
SSH_CONNECT_TIMEOUT="${SSH_CONNECT_TIMEOUT:-10}"
SSH_MAX_RETRIES="${SSH_MAX_RETRIES:-60}"
MAIN_LOOP_DELAY_SECONDS="${MAIN_LOOP_DELAY_SECONDS:-25}"
AUTOGPT_TASK="${AUTOGPT_TASK:-Acceseaza OLX Romania (olx.ro) si extrage preturile pentru Samsung Galaxy S24 Ultra second hand. Daca primesti 403/WAF, scrie si testeaza cod Python cu execute_shell pana gasesti o metoda care functioneaza. Returneaza: titlu, pret RON, link pentru fiecare anunt. Calculeaza: minim, maxim, medie, mediana.}"

if [[ ! -f "$ROOT_DIR/.env" ]]; then
  echo "Missing local env file: $ROOT_DIR/.env"
  echo "Create it first with your LLM keys and AutoGPT settings."
  exit 1
fi

echo "Configuring OCI CLI profile..."
bash "$SETUP_SCRIPT"

echo "Starting self-loop orchestrator..."
attempt=0
while true; do
  attempt=$((attempt + 1))
  echo
  echo "=== Orchestrator cycle $attempt ==="

  # Step 1: launch an Ampere instance (deploy script has its own retry loop)
  set +e
  deploy_output=$(bash "$DEPLOY_SCRIPT" 2>&1)
  deploy_status=$?
  set -e

  if [[ "$deploy_status" -ne 0 ]]; then
    echo "Capacity/deploy step failed in cycle $attempt."
    echo "$deploy_output" | tail -n 20
    echo "Retrying full cycle in ${MAIN_LOOP_DELAY_SECONDS}s..."
    sleep "$MAIN_LOOP_DELAY_SECONDS"
    continue
  fi

  public_ip=$(echo "$deploy_output" | awk -F= '/^PUBLIC_IP=/{print $2}' | tail -n 1)
  instance_id=$(echo "$deploy_output" | awk -F= '/^INSTANCE_ID=/{print $2}' | tail -n 1)

  if [[ -z "$public_ip" || -z "$instance_id" ]]; then
    echo "Could not parse deployment output."
    echo "$deploy_output" | tail -n 20
    sleep "$MAIN_LOOP_DELAY_SECONDS"
    continue
  fi

  echo "Target instance: $instance_id"
  echo "Target IP: $public_ip"

  # Step 2: wait for SSH to become available
  ssh_ready=0
  for ((i=1; i<=SSH_MAX_RETRIES; i++)); do
    if ssh -o StrictHostKeyChecking=no -o ConnectTimeout="$SSH_CONNECT_TIMEOUT" "$REMOTE_SSH_USER@$public_ip" "echo ok" >/dev/null 2>&1; then
      ssh_ready=1
      echo "SSH ready after $i checks."
      break
    fi
    echo "SSH not ready yet ($i/$SSH_MAX_RETRIES)."
    sleep 10
  done

  if [[ "$ssh_ready" -ne 1 ]]; then
    echo "SSH never became ready. Continuing loop..."
    sleep "$MAIN_LOOP_DELAY_SECONDS"
    continue
  fi

  # Step 3: provision and run AutoGPT remotely
  echo "Uploading local .env to remote classic/.env..."
  ssh -o StrictHostKeyChecking=no "$REMOTE_SSH_USER@$public_ip" "mkdir -p ~/autogpt_runner"

  set +e
  ssh -o StrictHostKeyChecking=no "$REMOTE_SSH_USER@$public_ip" "bash -s" <<'REMOTE_BOOTSTRAP'
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive
sudo apt-get update
sudo apt-get install -y git python3-pip

if ! command -v poetry >/dev/null 2>&1; then
  python3 -m pip install --user poetry
  export PATH="$HOME/.local/bin:$PATH"
fi

if [[ ! -d "$HOME/AutoGPT" ]]; then
  git clone https://github.com/Significant-Gravitas/AutoGPT.git "$HOME/AutoGPT"
fi

cd "$HOME/AutoGPT/classic"
export PATH="$HOME/.local/bin:$PATH"
poetry install
mkdir -p "$HOME/AutoGPT/classic/.autogpt"
REMOTE_BOOTSTRAP
  bootstrap_status=$?
  set -e

  if [[ "$bootstrap_status" -ne 0 ]]; then
    echo "Remote bootstrap failed. Retrying full loop..."
    sleep "$MAIN_LOOP_DELAY_SECONDS"
    continue
  fi

  scp -o StrictHostKeyChecking=no "$ROOT_DIR/.env" "$REMOTE_SSH_USER@$public_ip:~/AutoGPT/classic/.env" >/dev/null

  set +e
  ssh -o StrictHostKeyChecking=no "$REMOTE_SSH_USER@$public_ip" "bash -s" <<REMOTE_RUN
set -euo pipefail
cd ~/AutoGPT/classic
export PATH="\$HOME/.local/bin:\$PATH"
if command -v tmux >/dev/null 2>&1; then
  tmux new-session -d -s autogpt "printf '%s\\n' \"$AUTOGPT_TASK\" | poetry run autogpt run --skip-news --skip-reprompt | tee /tmp/autogpt_remote.log"
else
  nohup bash -lc "printf '%s\\n' \"$AUTOGPT_TASK\" | poetry run autogpt run --skip-news --skip-reprompt | tee /tmp/autogpt_remote.log" >/tmp/autogpt_nohup.log 2>&1 &
fi
echo "AUTOGPT_REMOTE_STARTED=1"
REMOTE_RUN
  run_status=$?
  set -e

  if [[ "$run_status" -eq 0 ]]; then
    echo "AutoGPT started successfully on OCI instance."
    echo "SSH: ssh $REMOTE_SSH_USER@$public_ip"
    echo "Log: /tmp/autogpt_remote.log"
    exit 0
  fi

  echo "Remote run step failed. Retrying full loop in ${MAIN_LOOP_DELAY_SECONDS}s..."
  sleep "$MAIN_LOOP_DELAY_SECONDS"
done
