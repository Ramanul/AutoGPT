# AutoGPT on Oracle Cloud Ampere (24 GB)

This guide links your local AutoGPT setup to Oracle Cloud Infrastructure (OCI) resources by:

1. Configuring OCI CLI credentials
2. Launching an Ampere instance sized for 24 GB RAM
3. Running AutoGPT on that instance

## 1) Configure OCI credentials locally

Use your OCI account values as environment variables:

```bash
export OCI_USER_OCID='ocid1.user.oc1..aaaaaaaa6wag62wujh3qh2nho7mlc4urwbnjuq22dbyer4zh6nucrj4tlkea'
export OCI_FINGERPRINT='81:2d:42:60:e9:ab:5c:ea:a5:ad:be:62:0c:e3:15:a8'
export OCI_TENANCY_OCID='ocid1.tenancy.oc1..aaaaaaaa4xl4qa7doifixfm5gls23i6lnqtseygnmvmu5gwrg4lbmyltrucq'
export OCI_REGION='eu-milan-1'
export OCI_KEY_FILE="$HOME/.oci/oci_api_key.pem"
```

Then run:

```bash
bash classic/scripts/setup_oci_ampere.sh
```

## 2) Launch Ampere 24 GB instance

Set required launch variables:

```bash
export OCI_SSH_PUBLIC_KEY_FILE="$HOME/.ssh/id_ed25519.pub"
```

Optional (manual override if auto-discovery is not correct):

```bash
export OCI_COMPARTMENT_ID='ocid1.compartment.oc1..replace-me'
export OCI_SUBNET_ID='ocid1.subnet.oc1.eu-milan-1.replace-me'
export OCI_IMAGE_ID='ocid1.image.oc1.eu-milan-1.replace-me'
```

Optional sizing variables (defaults shown):

```bash
export OCI_INSTANCE_DISPLAY_NAME='autogpt-ampere-24gb'
export OCI_OCPUS='4'
export OCI_MEMORY_GB='24'
```

Launch:

```bash
bash classic/scripts/deploy_autogpt_to_ampere.sh
```

If Milan has no free capacity often, use the self-loop orchestrator instead.
It keeps trying automatically until it gets capacity and starts AutoGPT on the VM:

```bash
bash classic/scripts/autogpt_oci_self_loop.sh
```

## 3) Run AutoGPT on the new VM

SSH into the instance (output printed by the launch script), then:

```bash
sudo apt-get update
sudo apt-get install -y git python3-pip pipx
pipx ensurepath
git clone https://github.com/Significant-Gravitas/AutoGPT.git
cd AutoGPT/classic
python3 -m pip install poetry
poetry install
```

Add your LLM/API variables to `classic/.env`, then run:

```bash
poetry run autogpt run --skip-news --skip-reprompt
```

With the self-loop script, this remote run is automated. The script uploads local
`classic/.env` to the VM and starts AutoGPT in `tmux` (or `nohup` fallback).

## Security notes

- Never commit private keys or `.env` secrets.
- Keep `~/.oci/config` and the private key at file mode 600.
- Rotate any key that was shared in chat or logs.
