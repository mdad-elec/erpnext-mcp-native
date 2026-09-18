#!/usr/bin/env bash
# Publish-safety sweep. Two layers:
#   1. Generic checks (always on, safe to ship): credential-shaped strings,
#      hardcoded secret assignments, private IPs, key/cert files, docs URLs
#      outside the public allowlist.
#   2. Maintainer blocklist (optional): extra fixed-string patterns loaded
#      from scripts/scan.internal (git-ignored). Present in maintainer
#      checkouts, absent in public clones and CI. The run states which mode
#      it used, so a silent pass is impossible.
# Exits 1 on any hit.
set -uo pipefail
cd "$(dirname "$0")/.."

fail=0
hit() { echo "SCAN HIT [$1]:"; echo "$2"; fail=1; }

excludes=(--exclude-dir=.git --exclude-dir=__pycache__ --exclude=scan.internal)
kinds=(-rIn --include='*.py' --include='*.md' --include='*.toml' --include='*.yml' --include='*.yaml' --include='*.sh' --include='*.json')

# 1a. credential-shaped key:secret hex pairs
out=$(grep "${kinds[@]}" "${excludes[@]}" -E '[a-f0-9]{12,}:[a-f0-9]{12,}' . || true)
[ -n "$out" ] && hit "credential-pair" "$out"

# 1b. hardcoded secret literal assignments (variable use is fine)
out=$(grep "${kinds[@]}" "${excludes[@]}" -E "(api_secret|client_secret|api_key)[\"']?\s*(=|:)\s*[\"'][A-Za-z0-9]{8,}" . || true)
[ -n "$out" ] && hit "hardcoded-secret" "$out"

# 1c. private-network IPs
out=$(grep "${kinds[@]}" "${excludes[@]}" -E '192\.168\.[0-9]+\.[0-9]+|10\.[0-9]+\.[0-9]+\.[0-9]+|172\.(1[6-9]|2[0-9]|3[01])\.[0-9]+\.[0-9]+' . || true)
[ -n "$out" ] && hit "private-ip" "$out"

# 1d. key/cert material files
out=$(find . -path ./.git -prune -o \( -name '*.pem' -o -name '*.key' -o -name 'id_rsa*' \) -print | head -20)
[ -n "$out" ] && hit "key-file" "$out"

# 1e. docs URLs outside the public allowlist
out=$(grep -rIhoE --include='*.md' 'https?://[A-Za-z0-9.-]+[:/]' . \
      | sed -E 's#https?://##; s#[:/]$##; s#^www\.##' | sort -u \
      | grep -Ev '^(example\.(com|org)|[A-Za-z0-9-]+\.example\.(com|org)|github\.com|localhost|127\.0\.0\.1|claude\.ai|claude\.com|chatgpt\.com|openai\.com|[A-Za-z0-9.-]*frappe\.(io|cloud))$' || true)
[ -n "$out" ] && hit "non-allowlisted-url" "$out"

# 2. maintainer blocklist (internal identifiers, never shipped)
if [ -f scripts/scan.internal ]; then
  mode="full (generic + internal blocklist)"
  while IFS= read -r p; do
    [ -z "$p" ] && continue
    case "$p" in \#*) continue ;; esac
    out=$(grep "${kinds[@]}" "${excludes[@]}" -F "$p" . || true)
    [ -n "$out" ] && hit "internal:$p" "$out"
  done < scripts/scan.internal
else
  mode="public (generic only — scripts/scan.internal absent)"
fi

if [ $fail -eq 0 ]; then echo "scan.sh: clean (mode: $mode)"; fi
exit $fail
