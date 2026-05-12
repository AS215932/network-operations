#!/usr/bin/env bash
set -euo pipefail

PROJECT_ID="${GEMINI_QUOTA_PROJECT_ID:-project-73d4ac43-0c8a-4ec0-ac5}"
PROJECT_NUMBER="${GEMINI_QUOTA_PROJECT_NUMBER:-338142359138}"
POOL_ID="${GOOGLE_WIF_POOL_ID:-as215932-vault}"
PROVIDER_ID="${GOOGLE_WIF_PROVIDER_ID:-vault-identity}"
SERVICE_ACCOUNT="${GOOGLE_WIF_SERVICE_ACCOUNT:-noc-agent-monitoring@${PROJECT_ID}.iam.gserviceaccount.com}"
VAULT_PUBLIC_ADDR="${VAULT_PUBLIC_ADDR:-https://vault.as215932.net}"
VAULT_PUBLIC_ADDR="${VAULT_PUBLIC_ADDR%/}"
VAULT_ISSUER="${VAULT_ISSUER:-${VAULT_PUBLIC_ADDR}}"
VAULT_ISSUER="${VAULT_ISSUER%/}"
GOOGLE_WIF_AUDIENCE="//iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_ID}/providers/${PROVIDER_ID}"

need() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "missing required command: $1" >&2
    exit 1
  }
}

need vault
need gcloud

: "${VAULT_ADDR:?Set VAULT_ADDR, usually https://vault.as215932.net}"
vault token lookup >/dev/null 2>&1 || {
  echo "Set VAULT_TOKEN to an operator token, or run vault login, before initial bootstrap" >&2
  exit 1
}

echo "Configuring Vault KV, audit, AppRole, and OIDC identity token role..."

if ! vault secrets list -format=json | grep -q '"kv/"'; then
  vault secrets enable -path=kv kv-v2
fi

vault audit enable file file_path=/var/log/vault/audit.log >/dev/null 2>&1 || true
vault auth enable approle >/dev/null 2>&1 || true

vault policy write noc-agent - <<'POLICY'
path "kv/data/noc-agent" {
  capabilities = ["read"]
}

path "identity/oidc/token/google-wif-noc-agent" {
  capabilities = ["read"]
}
POLICY

vault write identity/oidc/config issuer="${VAULT_ISSUER}"
vault write identity/oidc/key/google-wif \
  allowed_client_ids="${GOOGLE_WIF_AUDIENCE}" \
  algorithm=RS256 \
  rotation_period=24h \
  verification_ttl=24h

vault write identity/oidc/role/google-wif-noc-agent \
  key=google-wif \
  client_id="${GOOGLE_WIF_AUDIENCE}" \
  ttl=45m \
  template='{"service":"noc-agent"}'

vault write auth/approle/role/noc-agent \
  token_policies=noc-agent \
  token_ttl=1h \
  token_max_ttl=24h \
  secret_id_ttl=0

ROLE_ID="$(vault read -field=role_id auth/approle/role/noc-agent/role-id)"
SECRET_ID="$(vault write -f -field=secret_id auth/approle/role/noc-agent/secret-id)"

echo "Configuring Google Cloud service account and Workload Identity Federation..."

if ! gcloud iam service-accounts describe "${SERVICE_ACCOUNT}" --project="${PROJECT_ID}" >/dev/null 2>&1; then
  gcloud iam service-accounts create noc-agent-monitoring \
    --project="${PROJECT_ID}" \
    --display-name="NOC Agent Cloud Monitoring quota reader"
fi

gcloud projects add-iam-policy-binding "${PROJECT_ID}" \
  --member="serviceAccount:${SERVICE_ACCOUNT}" \
  --role="roles/monitoring.viewer" \
  --quiet >/dev/null

if ! gcloud iam workload-identity-pools describe "${POOL_ID}" \
  --project="${PROJECT_ID}" \
  --location=global >/dev/null 2>&1; then
  gcloud iam workload-identity-pools create "${POOL_ID}" \
    --project="${PROJECT_ID}" \
    --location=global \
    --display-name="AS215932 Vault workloads"
fi

if ! gcloud iam workload-identity-pools providers describe "${PROVIDER_ID}" \
  --project="${PROJECT_ID}" \
  --location=global \
  --workload-identity-pool="${POOL_ID}" >/dev/null 2>&1; then
  gcloud iam workload-identity-pools providers create-oidc "${PROVIDER_ID}" \
    --project="${PROJECT_ID}" \
    --location=global \
    --workload-identity-pool="${POOL_ID}" \
    --display-name="Vault identity tokens" \
    --issuer-uri="${VAULT_ISSUER}" \
    --allowed-audiences="${GOOGLE_WIF_AUDIENCE}" \
    --attribute-mapping="google.subject=assertion.sub,attribute.service=assertion.service" \
    --attribute-condition="assertion.service == 'noc-agent'"
fi

gcloud iam service-accounts add-iam-policy-binding "${SERVICE_ACCOUNT}" \
  --project="${PROJECT_ID}" \
  --role="roles/iam.workloadIdentityUser" \
  --member="principalSet://iam.googleapis.com/projects/${PROJECT_NUMBER}/locations/global/workloadIdentityPools/${POOL_ID}/attribute.service/noc-agent" \
  --quiet >/dev/null

cat <<OUT

Bootstrap complete.

Set these only for the NOC deploy that installs the Vault Agent bootstrap:

export NOC_AGENT_SECRET_BACKEND=vault
export VAULT_NOC_AGENT_ROLE_ID='${ROLE_ID}'
export VAULT_NOC_AGENT_SECRET_ID='${SECRET_ID}'

Then run:

ansible-playbook playbooks/noc.yml --tags apply -e '{"noc_apply":true}' --limit noc

OUT
