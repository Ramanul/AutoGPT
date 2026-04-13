#!/usr/bin/env bash
set -euo pipefail

# Configure OCI CLI credentials for this machine using environment variables.
# This script writes ~/.oci/config and validates the key and auth with OCI APIs.

if ! command -v oci >/dev/null 2>&1; then
  echo "OCI CLI is not installed. Trying to install with pipx..."
  if ! command -v pipx >/dev/null 2>&1; then
    python3 -m pip install --user pipx
    python3 -m pipx ensurepath >/dev/null 2>&1 || true
    export PATH="$HOME/.local/bin:$PATH"
  fi

  pipx install oci-cli || {
    echo "Failed to install OCI CLI automatically."
    echo "Install manually:"
    echo "  1) pipx install oci-cli"
    echo "  2) python3 -m pip install --user oci-cli"
    exit 1
  }
fi

required_vars=(
  OCI_USER_OCID
  OCI_FINGERPRINT
  OCI_TENANCY_OCID
  OCI_REGION
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
  echo "Export the required variables and run again. Example:"
  echo "  export OCI_USER_OCID='ocid1.user.oc1..example'"
  echo "  export OCI_FINGERPRINT='aa:bb:cc:dd:ee:ff:00:11:22:33:44:55:66:77:88:99'"
  echo "  export OCI_TENANCY_OCID='ocid1.tenancy.oc1..example'"
  echo "  export OCI_REGION='eu-milan-1'"
  echo "  export OCI_KEY_FILE='$HOME/.oci/oci_api_key.pem'"
  echo "  # OR provide private key content directly:"
  echo "  export OCI_PRIVATE_KEY_CONTENT='-----BEGIN PRIVATE KEY-----...'"
  echo "  # OR base64 encoded key:"
  echo "  export OCI_PRIVATE_KEY_B64='LS0tLS1CRUdJTiBQUklWQVRFIEtFWS0tLS0t...'"
  exit 1
fi

if [[ -z "${OCI_KEY_FILE:-}" ]]; then
  OCI_KEY_FILE="$HOME/.oci/oci_api_key.pem"
fi

if [[ ! -f "$OCI_KEY_FILE" ]]; then
  mkdir -p "$HOME/.oci"
  if [[ -n "${OCI_PRIVATE_KEY_CONTENT:-}" ]]; then
    printf '%s\n' "$OCI_PRIVATE_KEY_CONTENT" > "$OCI_KEY_FILE"
  elif [[ -n "${OCI_PRIVATE_KEY_B64:-}" ]]; then
    printf '%s' "$OCI_PRIVATE_KEY_B64" | base64 -d > "$OCI_KEY_FILE"
  else
    echo "Private key file does not exist: $OCI_KEY_FILE"
    echo "Provide OCI_KEY_FILE or OCI_PRIVATE_KEY_CONTENT or OCI_PRIVATE_KEY_B64"
    exit 1
  fi
fi

mkdir -p "$HOME/.oci"
chmod 700 "$HOME/.oci"

cat > "$HOME/.oci/config" <<EOF
[DEFAULT]
user=$OCI_USER_OCID
fingerprint=$OCI_FINGERPRINT
tenancy=$OCI_TENANCY_OCID
region=$OCI_REGION
key_file=$OCI_KEY_FILE
EOF

chmod 600 "$HOME/.oci/config"
chmod 600 "$OCI_KEY_FILE" || true

echo "Testing OCI authentication..."
oci iam region-subscription list --tenancy-id "$OCI_TENANCY_OCID" >/dev/null

echo "OCI CLI is configured successfully."
echo "Config file: $HOME/.oci/config"
