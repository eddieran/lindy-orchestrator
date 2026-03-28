#!/bin/bash
# preflight-github-transport.sh — validate GitHub transport before unattended work
#
# Runs a sequence of connectivity checks and exits non-zero if no viable
# GitHub transport path exists. Designed to be called from workspace bootstrap
# hooks (WORKFLOW.md hooks.after_create) so that agents discover transport
# problems before spending turns on implementation work.
#
# Usage:
#   ./scripts/preflight-github-transport.sh          # human-readable output
#   ./scripts/preflight-github-transport.sh --json   # machine-readable JSON
#
# Exit codes:
#   0 — at least one transport path works (HTTPS or SSH)
#   1 — no working transport; details in output

set -euo pipefail

readonly TIMEOUT_S=5

# ── helpers ──────────────────────────────────────────────────────────────────

json_escape() {
    local v="${1:-}"
    v="${v//\\/\\\\}"
    v="${v//\"/\\\"}"
    v="${v//$'\n'/\\n}"
    v="${v//$'\r'/}"
    printf '%s' "$v"
}

check_pass() { printf '  %-40s \033[32mPASS\033[0m\n' "$1"; }
check_fail() { printf '  %-40s \033[31mFAIL\033[0m  %s\n' "$1" "$2"; }
check_warn() { printf '  %-40s \033[33mWARN\033[0m  %s\n' "$1" "$2"; }
check_skip() { printf '  %-40s \033[90mSKIP\033[0m  %s\n' "$1" "$2"; }

# ── individual probes ────────────────────────────────────────────────────────

probe_proxy_env() {
    # Returns: "none" | "reachable" | "unreachable"
    local proxy_val="${http_proxy:-${HTTP_PROXY:-${https_proxy:-${HTTPS_PROXY:-}}}}"
    if [[ -z "$proxy_val" ]]; then
        echo "none"
        return
    fi
    if curl -s --connect-timeout "$TIMEOUT_S" --proxy "$proxy_val" \
         https://github.com -o /dev/null -w '' 2>/dev/null; then
        echo "reachable"
    else
        echo "unreachable"
    fi
}

probe_dns() {
    # Returns: "ok" | error message
    if host github.com >/dev/null 2>&1; then
        echo "ok"
    elif nslookup github.com >/dev/null 2>&1; then
        echo "ok"
    else
        echo "Could not resolve host: github.com"
    fi
}

probe_https() {
    # Returns: "ok" | error message
    local out
    if out="$(env -u http_proxy -u https_proxy -u HTTP_PROXY -u HTTPS_PROXY \
         curl -s --connect-timeout "$TIMEOUT_S" -o /dev/null -w "%{http_code}" \
         https://github.com 2>&1)"; then
        if [[ "$out" =~ ^[23] ]]; then
            echo "ok"
        else
            echo "HTTP $out"
        fi
    else
        echo "$out"
    fi
}

probe_ssh() {
    # Returns: "ok" | error message
    local out
    if out="$(ssh -o ConnectTimeout="$TIMEOUT_S" -o StrictHostKeyChecking=accept-new \
         -T git@github.com 2>&1)"; then
        echo "ok"
    else
        # ssh -T exits 1 even on success ("does not provide shell access")
        if [[ "$out" == *"successfully authenticated"* ]]; then
            echo "ok"
        else
            # Trim to first line for brevity
            echo "${out%%$'\n'*}"
        fi
    fi
}

probe_git_fetch() {
    # Returns: "ok" | error message
    local out
    if out="$(git ls-remote --heads origin main 2>&1 | head -1)"; then
        if [[ -n "$out" ]]; then
            echo "ok"
        else
            echo "empty response from git ls-remote"
        fi
    else
        echo "${out%%$'\n'*}"
    fi
}

probe_gh_auth() {
    # Returns: "ok" | error message
    if ! command -v gh >/dev/null 2>&1; then
        echo "gh not installed"
        return
    fi
    local out
    if out="$(gh auth status 2>&1)"; then
        echo "ok"
    else
        echo "${out%%$'\n'*}"
    fi
}

# ── main ─────────────────────────────────────────────────────────────────────

main() {
    local output_json=false
    [[ "${1:-}" == "--json" ]] && output_json=true

    local proxy_status dns_status https_status ssh_status git_fetch_status gh_auth_status
    proxy_status="$(probe_proxy_env)"
    dns_status="$(probe_dns)"
    https_status="$(probe_https)"
    ssh_status="$(probe_ssh)"
    git_fetch_status="$(probe_git_fetch)"
    gh_auth_status="$(probe_gh_auth)"

    # Determine overall verdict
    local transport_ok=false
    [[ "$https_status" == "ok" ]] && transport_ok=true
    [[ "$ssh_status" == "ok" ]] && transport_ok=true

    local verdict="FAIL"
    $transport_ok && verdict="PASS"

    if $output_json; then
        cat <<EOF
{
  "verdict": "$verdict",
  "proxy": {
    "configured": $([ "$proxy_status" != "none" ] && echo true || echo false),
    "status": "$(json_escape "$proxy_status")",
    "http_proxy": "$(json_escape "${http_proxy:-}")",
    "https_proxy": "$(json_escape "${https_proxy:-}")"
  },
  "dns": "$(json_escape "$dns_status")",
  "https": "$(json_escape "$https_status")",
  "ssh": "$(json_escape "$ssh_status")",
  "git_fetch": "$(json_escape "$git_fetch_status")",
  "gh_auth": "$(json_escape "$gh_auth_status")"
}
EOF
        $transport_ok && return 0 || return 1
    fi

    echo "GitHub Transport Preflight Check"
    echo "================================"
    echo ""

    # Proxy
    case "$proxy_status" in
        none)      check_skip "proxy" "no proxy configured" ;;
        reachable) check_pass "proxy ($http_proxy)" ;;
        unreachable)
            check_fail "proxy ($http_proxy)" "proxy set but unreachable"
            echo "         → unset http_proxy/https_proxy or ensure proxy is running"
            ;;
    esac

    # DNS
    if [[ "$dns_status" == "ok" ]]; then
        check_pass "DNS (github.com)"
    else
        check_fail "DNS (github.com)" "$dns_status"
    fi

    # HTTPS
    if [[ "$https_status" == "ok" ]]; then
        check_pass "HTTPS (github.com)"
    else
        check_fail "HTTPS (github.com)" "$https_status"
    fi

    # SSH
    if [[ "$ssh_status" == "ok" ]]; then
        check_pass "SSH (git@github.com)"
    else
        check_warn "SSH (git@github.com)" "$ssh_status"
    fi

    # git fetch
    if [[ "$git_fetch_status" == "ok" ]]; then
        check_pass "git ls-remote origin"
    else
        check_fail "git ls-remote origin" "$git_fetch_status"
    fi

    # gh auth
    if [[ "$gh_auth_status" == "ok" ]]; then
        check_pass "gh auth status"
    else
        check_warn "gh auth status" "$gh_auth_status"
    fi

    echo ""
    if $transport_ok; then
        echo "Verdict: PASS — at least one GitHub transport path is available."
        return 0
    else
        echo "Verdict: FAIL — no working GitHub transport path found."
        echo ""
        echo "Troubleshooting:"
        echo "  1. If proxy is set but unreachable, unset http_proxy/https_proxy"
        echo "  2. If DNS fails, check network connectivity and /etc/resolv.conf"
        echo "  3. If HTTPS fails, check firewall rules for github.com:443"
        echo "  4. If SSH fails, check firewall rules for github.com:22 or ssh.github.com:443"
        echo "  5. Ensure .codex/sandbox-allowlist.yml includes GitHub domains"
        return 1
    fi
}

main "$@"
