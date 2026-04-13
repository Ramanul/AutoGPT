#!/usr/bin/env bash
set -euo pipefail

# Launch an OCI Ampere instance sized to 24 GB RAM for AutoGPT workloads.
# Includes retry loop for regions with frequent capacity shortages (like Milan).

if ! command -v oci >/dev/null 2>&1; then
  echo "OCI CLI is not installed."
  exit 1
fi

required_vars=(
  OCI_TENANCY_OCID
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
  echo
  echo "Required exports:"
  echo "  export OCI_TENANCY_OCID='ocid1.tenancy.oc1..example'"
  echo "  export OCI_SSH_PUBLIC_KEY_FILE='$HOME/.ssh/id_ed25519.pub'"
  echo
  echo "Optional exports:"
  echo "  export OCI_COMPARTMENT_ID='ocid1.compartment.oc1..example'"
  echo "  export OCI_SUBNET_ID='ocid1.subnet.oc1.eu-milan-1.example'"
  echo "  export OCI_IMAGE_ID='ocid1.image.oc1.eu-milan-1.example'"
  echo "  export OCI_INSTANCE_DISPLAY_NAME='autogpt-ampere-24gb'"
  echo "  export OCI_OCPUS_CANDIDATES='4 3 2 1'"
  echo "  export OCI_MEMORY_GB='24'"
  echo "  export OCI_RETRY_DELAY_SECONDS='20'"
  echo "  export OCI_MAX_ATTEMPTS='0'   # 0 means infinite"
  exit 1
fi

if [[ ! -f "$OCI_SSH_PUBLIC_KEY_FILE" ]]; then
  echo "SSH public key file does not exist: $OCI_SSH_PUBLIC_KEY_FILE"
  exit 1
fi

OCI_INSTANCE_DISPLAY_NAME="${OCI_INSTANCE_DISPLAY_NAME:-autogpt-ampere-24gb}"
OCI_OCPUS_CANDIDATES="${OCI_OCPUS_CANDIDATES:-4 3 2 1}"
OCI_MEMORY_GB="${OCI_MEMORY_GB:-24}"
OCI_RETRY_DELAY_SECONDS="${OCI_RETRY_DELAY_SECONDS:-20}"
OCI_MAX_ATTEMPTS="${OCI_MAX_ATTEMPTS:-0}"
OCI_COMPARTMENT_ID="${OCI_COMPARTMENT_ID:-$OCI_TENANCY_OCID}"

if [[ -z "${OCI_IMAGE_ID:-}" ]]; then
  echo "OCI_IMAGE_ID not set, auto-discovering latest Ubuntu ARM image..."
  OCI_IMAGE_ID=$(oci compute image list \
    --compartment-id "$OCI_COMPARTMENT_ID" \
    --operating-system "Canonical Ubuntu" \
    --operating-system-version "24.04" \
    --shape "VM.Standard.A1.Flex" \
    --sort-by TIMECREATED \
    --sort-order DESC \
    --query 'data[0].id' \
    --raw-output 2>/dev/null || true)

  if [[ -z "$OCI_IMAGE_ID" || "$OCI_IMAGE_ID" == "null" ]]; then
    OCI_IMAGE_ID=$(oci compute image list \
      --compartment-id "$OCI_COMPARTMENT_ID" \
      --operating-system "Canonical Ubuntu" \
      --shape "VM.Standard.A1.Flex" \
      --sort-by TIMECREATED \
      --sort-order DESC \
      --query 'data[0].id' \
      --raw-output)
  fi
fi

if [[ -z "${OCI_SUBNET_ID:-}" ]]; then
  echo "OCI_SUBNET_ID not set, auto-discovering first available subnet..."
  OCI_SUBNET_ID=$(oci network subnet list \
    --compartment-id "$OCI_COMPARTMENT_ID" \
    --sort-by TIMECREATED \
    --sort-order ASC \
    --query 'data[0].id' \
    --raw-output)
fi

if [[ -z "$OCI_IMAGE_ID" || "$OCI_IMAGE_ID" == "null" ]]; then
  echo "Could not auto-discover OCI_IMAGE_ID. Set OCI_IMAGE_ID manually."
  exit 1
fi

if [[ -z "$OCI_SUBNET_ID" || "$OCI_SUBNET_ID" == "null" ]]; then
  echo "Could not auto-discover OCI_SUBNET_ID. Set OCI_SUBNET_ID manually."
  exit 1
fi

if ! [[ "$OCI_RETRY_DELAY_SECONDS" =~ ^[0-9]+$ ]]; then
  echo "OCI_RETRY_DELAY_SECONDS must be a positive integer"
  exit 1
fi

if ! [[ "$OCI_MAX_ATTEMPTS" =~ ^[0-9]+$ ]]; then
  echo "OCI_MAX_ATTEMPTS must be a non-negative integer"
  exit 1
fi

ad_list=$(oci iam availability-domain list \
  --compartment-id "$OCI_TENANCY_OCID" \
  --query 'data[].name' \
  --raw-output)

if [[ -z "$ad_list" ]]; then
  echo "Could not discover availability domains for tenancy."
  exit 1
fi

mapfile -t availability_domains <<< "$ad_list"

attempt=0
ad_index=0

echo "Searching for free capacity in region ${OCI_REGION:-unknown}..."
echo "Compartment: $OCI_COMPARTMENT_ID"
echo "Subnet: $OCI_SUBNET_ID"
echo "Image: $OCI_IMAGE_ID"
echo "Availability domains: ${availability_domains[*]}"
echo "OCPU candidates: $OCI_OCPUS_CANDIDATES"
echo "Memory target: ${OCI_MEMORY_GB} GB"

launch_json=""
while true; do
  attempt=$((attempt + 1))
  if [[ "$OCI_MAX_ATTEMPTS" -gt 0 && "$attempt" -gt "$OCI_MAX_ATTEMPTS" ]]; then
    echo "Reached max attempts (${OCI_MAX_ATTEMPTS}) without finding capacity."
    exit 1
  fi

  ad="${availability_domains[$ad_index]}"
  ad_index=$(((ad_index + 1) % ${#availability_domains[@]}))

  for ocpus in $OCI_OCPUS_CANDIDATES; do
    echo "Attempt ${attempt}: AD=${ad}, OCPUs=${ocpus}, RAM=${OCI_MEMORY_GB}GB"

    set +e
    launch_json=$(oci compute instance launch \
      --compartment-id "$OCI_COMPARTMENT_ID" \
      --availability-domain "$ad" \
      --shape "VM.Standard.A1.Flex" \
      --shape-config "{\"ocpus\":${ocpus},\"memoryInGBs\":${OCI_MEMORY_GB}}" \
      --subnet-id "$OCI_SUBNET_ID" \
      --image-id "$OCI_IMAGE_ID" \
      --display-name "${OCI_INSTANCE_DISPLAY_NAME}-${attempt}" \
      --assign-public-ip true \
      --metadata "{\"ssh_authorized_keys\":\"$(cat "$OCI_SSH_PUBLIC_KEY_FILE" | sed 's/"/\\\\"/g')\"}" \
      --wait-for-state RUNNING 2>&1)
    status=$?
    set -e

    if [[ "$status" -eq 0 ]]; then
      echo "Launch succeeded on attempt ${attempt} in ${ad} with ${ocpus} OCPUs."
      break 2
    fi

    echo "Launch failed:"
    echo "$launch_json" | tail -n 5
  done

  echo "No capacity yet. Waiting ${OCI_RETRY_DELAY_SECONDS}s before retry..."
  sleep "$OCI_RETRY_DELAY_SECONDS"
done

instance_id=$(echo "$launch_json" | python3 -c 'import json,sys; print(json.load(sys.stdin)["data"]["id"])')
vnic_id=$(oci compute instance list-vnics --instance-id "$instance_id" --query 'data[0].id' --raw-output)
public_ip=$(oci network vnic get --vnic-id "$vnic_id" --query 'data."public-ip"' --raw-output)

echo
echo "Instance launched successfully."
echo "Instance OCID: $instance_id"
echo "Public IP: $public_ip"
echo "SSH: ssh ubuntu@$public_ip"
echo "INSTANCE_ID=$instance_id"
echo "PUBLIC_IP=$public_ip"
echo
echo "Next: copy AutoGPT and run on the instance."
