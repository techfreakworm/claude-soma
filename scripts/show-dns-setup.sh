#!/usr/bin/env bash
# scripts/show-dns-setup.sh — print DNS A/AAAA records the operator must add
# at their DNS provider so Caddy can obtain TLS certs and serve the
# dashboard (soma.<domain>) + files relay (files.<domain>).
#
# Idempotent + non-destructive. Safe to re-run anytime.
#
# Flags:
#   --check    after printing, poll dig +short to report whether DNS already
#              resolves to the VPS IP (does NOT block on propagation)
#
# Domain detection (in order):
#   1. SOMA_DOMAIN env var (highest precedence)
#   2. /etc/caddy/Caddyfile parse — extract site blocks
#   3. Repo Caddyfile parse — fallback if not yet installed
#   4. Bail with manual-config instructions if all fail

set -uo pipefail

# Public IP fetch — try multiple services, accept the first that works
fetch_public_ip() {
    local family="$1"  # "4" or "6"
    local flag="-s${family}"
    for svc in https://api.ipify.org https://ifconfig.me/ip https://icanhazip.com; do
        local ip
        ip="$(curl "${flag}" --max-time 5 -s "${svc}" 2>/dev/null | tr -d '[:space:]')"
        if [[ -n "${ip}" ]]; then
            echo "${ip}"
            return 0
        fi
    done
    return 1
}

# Domain detection
detect_domain() {
    # 1. env var override
    if [[ -n "${SOMA_DOMAIN:-}" ]]; then
        echo "${SOMA_DOMAIN}"
        return 0
    fi
    # 2. live /etc/caddy/Caddyfile
    local candidates=()
    for cf in /etc/caddy/Caddyfile /etc/caddy/conf.d/*.caddyfile; do
        [[ -r "${cf}" ]] || continue
        # Match lines like "soma.example.com {" or "files.example.com {" at start
        local hosts
        hosts="$(sudo grep -oE '^[a-z0-9.-]+[[:space:]]*\{' "${cf}" 2>/dev/null | sed 's/[[:space:]]*{//' | sort -u)"
        while read -r h; do
            [[ -n "${h}" && "${h}" == *.* ]] && candidates+=("${h}")
        done <<<"${hosts}"
    done
    # 3. repo Caddyfile fallback
    if [[ ${#candidates[@]} -eq 0 ]]; then
        local repo_caddyfile
        repo_caddyfile="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/Caddyfile"
        if [[ -r "${repo_caddyfile}" ]]; then
            local hosts
            hosts="$(grep -oE '^[a-z0-9.-]+[[:space:]]*\{' "${repo_caddyfile}" 2>/dev/null | sed 's/[[:space:]]*{//' | sort -u)"
            while read -r h; do
                [[ -n "${h}" && "${h}" == *.* ]] && candidates+=("${h}")
            done <<<"${hosts}"
        fi
    fi
    # Reduce candidates to the BASE domain (strip soma./files. prefix)
    local base=""
    for c in "${candidates[@]}"; do
        case "${c}" in
            soma.*)  base="${c#soma.}"  ;;
            files.*) base="${c#files.}" ;;
        esac
        [[ -n "${base}" ]] && break
    done
    if [[ -n "${base}" ]]; then
        echo "${base}"
        return 0
    fi
    return 1
}

# Main
CHECK_PROPAGATION=0
for arg in "$@"; do
    case "${arg}" in
        --check) CHECK_PROPAGATION=1 ;;
    esac
done

echo "=============================================================="
echo "DNS SETUP REQUIRED"
echo "=============================================================="
echo

IPV4="$(fetch_public_ip 4 || echo "")"
IPV6="$(fetch_public_ip 6 || echo "")"

if [[ -z "${IPV4}" ]]; then
    echo "  Could not auto-detect public IPv4."
    echo "  Find your VPS public IP in your cloud provider console + add the"
    echo "  records below manually."
    echo
    IPV4="<YOUR_VPS_PUBLIC_IPV4>"
fi

DOMAIN="$(detect_domain || echo "")"
if [[ -z "${DOMAIN}" ]]; then
    echo "  Could not auto-detect the configured domain from Caddyfile."
    echo "  Substitute your own domain in the records below."
    echo
    DOMAIN="<your-domain>"
fi

# Print the table
printf "  %-7s  %-22s  %s\n" "Type" "Name (Host)" "Value (points to)"
printf "  %-7s  %-22s  %s\n" "----" "----------------------" "------------------"
printf "  %-7s  %-22s  %s\n" "A" "soma.${DOMAIN}" "${IPV4}"
printf "  %-7s  %-22s  %s\n" "A" "files.${DOMAIN}" "${IPV4}"
if [[ -n "${IPV6}" ]]; then
    printf "  %-7s  %-22s  %s\n" "AAAA" "soma.${DOMAIN}" "${IPV6}"
    printf "  %-7s  %-22s  %s\n" "AAAA" "files.${DOMAIN}" "${IPV6}"
fi
echo
echo "  Or a single wildcard:"
printf "  %-7s  %-22s  %s\n" "A" "*.${DOMAIN}" "${IPV4}"
echo
echo "  After adding these, allow a few minutes for propagation, then Caddy"
echo "  will automatically obtain TLS certs on next request."
echo
echo "  Verify with:  dig +short soma.${DOMAIN}   # should return ${IPV4}"
echo

if [[ "${CHECK_PROPAGATION}" -eq 1 && "${IPV4}" != "<YOUR_VPS_PUBLIC_IPV4>" && "${DOMAIN}" != "<your-domain>" ]]; then
    echo "=============================================================="
    echo "Checking DNS propagation..."
    echo "=============================================================="
    for host in "soma.${DOMAIN}" "files.${DOMAIN}"; do
        resolved="$(dig +short +time=3 +tries=2 "${host}" 2>/dev/null | head -1)"
        if [[ -z "${resolved}" ]]; then
            printf "  [NOT YET]  %s does not resolve yet (DNS not propagated)\n" "${host}"
        elif [[ "${resolved}" == "${IPV4}" ]]; then
            printf "  [READY]    %s -> %s (matches VPS)\n" "${host}" "${resolved}"
        else
            printf "  [MISMATCH] %s -> %s (expected %s)\n" "${host}" "${resolved}" "${IPV4}"
        fi
    done
    echo
    echo "  Run again later:  bash scripts/show-dns-setup.sh --check"
fi

echo "=============================================================="
echo
echo "=============================================================="
echo "CLOUD-PROVIDER FIREWALL (BUG #8) — also required"
echo "=============================================================="
cat <<'CLOUD_FIREWALL'

Your VPS's cloud provider has its OWN firewall (independent of any on-box
ufw rules). You MUST open inbound TCP ports 80 + 443 there too, or Caddy
cannot complete the ACME TLS challenge and your sites will be unreachable
even though every local service is healthy. This is a silent, confusing
failure if missed.

Quick steps per provider:

  Oracle Cloud (OCI) — VCN → Security Lists (or NSG) → Add Ingress Rule:
    Source: 0.0.0.0/0,  IP Protocol: TCP,  Destination Port: 80, 443

  AWS — EC2 Console → Security Groups → inbound rules → add:
    Type: HTTP   (port 80,  source 0.0.0.0/0)
    Type: HTTPS  (port 443, source 0.0.0.0/0)

  GCP — VPC network → Firewall → Create Rule:
    Targets: All instances in the network (or specific tag)
    Source IP ranges: 0.0.0.0/0
    Protocols + ports: tcp:80,443

  DigitalOcean — Networking → Firewalls → Inbound Rules:
    HTTP (80) + HTTPS (443) from any source

Without both DNS (above) AND cloud-provider firewall ingress, Caddy's
automatic TLS will fail with "no http server is listening on port 80"
or similar ACME challenge errors. The ACME TLS challenge requires inbound
port 80 to be open at the cloud-provider layer.

CLOUD_FIREWALL
echo "=============================================================="
