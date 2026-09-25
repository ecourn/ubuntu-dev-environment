#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
SCRIPT="$ROOT/bin/ssh-workflow.sh"
fail() { printf 'FAIL: %s\n' "$*" >&2; exit 1; }
expect_fail() { local output; if output="$("$@" 2>&1)"; then fail "unexpected success: $*"; fi; printf '%s\n' "$output"; }
[[ -f "$SCRIPT" ]] || fail 'workflow script missing'
bash -n "$SCRIPT"
for phase in prepare finalize status rollback; do "$SCRIPT" --help | grep -q "$phase" || fail "help missing $phase"; done
expect_fail "$SCRIPT" prepare --private-key /tmp/priv | grep -qi 'unknown\|inconnue' || fail 'private key option accepted'
expect_fail "$SCRIPT" finalize | grep -qi 'SSH_CONNECTION\|session' || fail 'finalize accepted no new-port session'
expect_fail "$SCRIPT" prepare --port 0 --user nobody --public-key /nonexistent | grep -qi 'port' || fail 'invalid port accepted'
expect_fail "$SCRIPT" prepare --port 00022 --user nobody --public-key /nonexistent | grep -qi 'port' || fail 'noncanonical port may evade Docker conflict check'
# Static security invariants: no agent, private-key path, broad firewall defaults, or localhost proof.
if grep -Eq 'ssh-add|ssh-agent|ssh-keyscan|StrictHostKeyChecking=no|ufw default|ufw --force enable|127\.0\.0\.1.*ssh |rm -rf' "$SCRIPT"; then fail 'unsafe workflow primitive'; fi
grep -q 'verify_effective transition' "$SCRIPT" || fail 'prepare must verify effective sshd config'
grep -q 'verify_effective final' "$SCRIPT" || fail 'finalize must verify effective sshd config'
grep -q "'authenticationmethods publickey'" "$SCRIPT" || fail 'public-key-only authentication not verified'
grep -q "'gssapiauthentication no'" "$SCRIPT" || fail 'GSSAPI authentication not disabled'
grep -q 'unexpected SSH port' "$SCRIPT" || fail 'extra effective SSH port not rejected'
grep -q 'tail -c 1' "$SCRIPT" || fail 'authorized_keys append must handle missing newline'
# Regression guards for the migration's fail-closed gates.
grep -q 'verify_session_socket' "$SCRIPT" || fail 'kernel-backed session validation missing'
grep -q 'verify_listener' "$SCRIPT" || fail 'post-reload listeners not checked'
grep -q 'match.*context\|verify_match_contexts' "$SCRIPT" || fail 'Match contexts not checked'
grep -q 'console-override' "$SCRIPT" || fail 'explicit console override missing'
grep -q 'mktemp.*sshdir\|mktemp.*auth' "$SCRIPT" || fail 'atomic authorized_keys staging missing'
grep -q 'locale -a' "$ROOT/bin/locale.sh" || fail 'generated locale not checked'
# Isolated state-path integration: status must not mutate or adopt hostile state.
tmp=$(mktemp -d)
trap 'sudo -n rm -rf -- "$tmp"' EXIT
python3 - "$SCRIPT" "$tmp" <<'PY'
from pathlib import Path
import sys
text = Path(sys.argv[1]).read_text()
text = text.replace('/var/lib/hermes-ssh-workflow', sys.argv[2] + '/state')
text = text.replace('/etc/ssh/sshd_config.d/00-hermes-workflow.conf', sys.argv[2] + '/dropin')
Path(sys.argv[2], 'workflow').write_text(text)
PY
[[ $(sudo -n bash "$tmp/workflow" status) == phase=absent ]] || fail 'status on absent state'
[[ ! -e "$tmp/state" ]] || fail 'status created state'
sudo -n install -d -m 700 "$tmp/state"
sudo -n install -m 600 /dev/null "$tmp/state/lock"
sudo -n mkdir -m 700 "$tmp/state/rolled-back-prior"
[[ $(sudo -n bash "$tmp/workflow" status) == phase=absent ]] || fail 'rolled-back archive and lock reported as incomplete migration'
printf 'do not delete\n' | sudo -n tee "$tmp/state/authorized_keys.backup" >/dev/null
[[ $(sudo -n bash "$tmp/workflow" status) == phase=incomplete ]] || fail 'orphaned backup falsely reported absent'
expect_fail sudo -n bash "$tmp/workflow" rollback | grep -qi 'incomplete\|manual' || fail 'orphaned backup silently rolled back'
[[ $(sudo -n cat "$tmp/state/authorized_keys.backup") == 'do not delete' ]] || fail 'orphaned evidence deleted'
sudo -n rm "$tmp/state/authorized_keys.backup" "$tmp/state/lock"
sudo -n rmdir "$tmp/state/rolled-back-prior"
sudo -n rmdir "$tmp/state"
sudo -n install -d -m 700 "$tmp/state"
printf 'phase=preparing\nport=62238\nold_port=22\nuser=admin\nservice=ssh.service\n' | sudo -n tee "$tmp/state/meta" >/dev/null
sudo -n chmod 600 "$tmp/state/meta"
[[ $(sudo -n bash "$tmp/workflow" status) == *phase=preparing* ]] || fail 'root status lost interrupted transaction'
[[ $(sudo -n stat -c %a "$tmp/state/meta") == 600 ]] || fail 'status mutated metadata'
sudo -n rm "$tmp/state/meta"
sudo -n rmdir "$tmp/state"
mkdir -m 0777 "$tmp/state"
chmod 777 "$tmp/state"
expect_fail sudo -n bash "$tmp/workflow" status | grep -q 'unsafe state directory' || fail 'status accepted hostile state directory'
[[ $(stat -c %a "$tmp/state") == 777 ]] || fail 'status changed hostile state permissions'
chmod 700 "$tmp/state"
printf 'phase=prepared\n' >"$tmp/state/meta"
if [[ $EUID == 0 ]]; then
    chmod 755 "$tmp"
    expect_fail runuser -u nobody -- bash "$tmp/workflow" status | grep -qi 'root required' || fail 'nonroot status falsely reported absent state'
else
    expect_fail bash "$tmp/workflow" status | grep -qi 'root required' || fail 'nonroot status falsely reported absent state'
fi
# Exercise the real listener/session functions against controlled ss output.
# shellcheck disable=SC1090 # Runtime extraction of the function under test.
source <(awk '/^verify_session_socket\(\) \{/,/^\}/' "$SCRIPT")
# shellcheck disable=SC1090
source <(awk '/^verify_listener\(\) \{/,/^\}/' "$SCRIPT")
# shellcheck disable=SC1090
source <(awk '/^verify_effective\(\) \{/,/^\}/' "$SCRIPT")
# shellcheck disable=SC1090
source <(awk '/^verify_match_contexts\(\) \{/,/^\}/' "$SCRIPT")
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
SERVER=198.51.100.2 CLIENT=192.0.2.3 SESSION_PORT=62238 CLIENT_PORT=54321
ss() {
    case ${MOCK_SS:-good} in
        good) if [[ $1 == -Htnp ]]; then printf '0 0 198.51.100.2:62238 192.0.2.3:54321 users:(("sshd",pid=2468,fd=3))\n'; else printf 'LISTEN 0 128 0.0.0.0:62238 0.0.0.0:* users:(("sshd",pid=2,fd=3))\n'; fi;;
        unrelated) printf '0 0 198.51.100.2:62238 192.0.2.3:54321 users:(("sshd",pid=9999,fd=3))\n';;
        noowner) printf '0      0      198.51.100.2:62238 192.0.2.3:54321\n';;
        ipv6) printf '0 0 [2001:db8::2]:62238 [2001:db8::3]:54321 users:(("sshd-session",pid=2468,fd=3))\n';;
        empty) :;;
        fail) return 1;;
    esac
}
SSHD_ANCESTORS=(2468)
export SERVER CLIENT SESSION_PORT CLIENT_PORT
verify_session_socket || fail 'established session tuple rejected'
MOCK_SS=unrelated
expect_fail verify_session_socket | grep -q 'does not match' || fail 'forged tuple on unrelated sshd socket accepted'
MOCK_SS=noowner
expect_fail verify_session_socket | grep -q 'does not match' || fail 'real ss queue-format row without process attribution accepted'
MOCK_SS=ipv6 SERVER=2001:db8::2 CLIENT=2001:db8::3
verify_session_socket || fail 'IPv6 sshd-session socket rejected'
SERVER=198.51.100.2 CLIENT=192.0.2.3
MOCK_SS=good
CLIENT_PORT=54322
expect_fail verify_session_socket | grep -q 'does not match' || fail 'forged session tuple accepted'
MOCK_SS=fail
expect_fail verify_session_socket | grep -q 'cannot inventory' || fail 'ss failure accepted for session'
expect_fail verify_listener 62238 present | grep -q 'cannot inspect' || fail 'ss failure accepted for listener'
MOCK_SS=good
verify_listener 62238 present || fail 'sshd listener rejected'
expect_fail verify_listener 62238 absent | grep -q 'still present' || fail 'old listener accepted'
# shellcheck disable=SC2016 # Match the literal script source.
retry_block=$(sed -n '/if \[\[ -e "\$meta" \]\]; then/,/die '\''existing workflow state/p' "$SCRIPT")
grep -q 'verify_effective transition' <<<"$retry_block" || fail 'prepared retry omits effective policy verification'
user=admin port=62238 old_port=22
export user port old_port
sshd() {
    if [[ $1 == -t ]]; then return 0; fi
    printf '%s\n' 'authenticationmethods publickey' 'pubkeyauthentication yes' 'passwordauthentication yes' 'kbdinteractiveauthentication no' 'permitrootlogin no' 'gssapiauthentication no' 'hostbasedauthentication no' 'permitemptypasswords no' 'strictmodes yes' 'authorizedkeysfile .ssh/authorized_keys' 'authorizedkeyscommand none' 'port 62238' 'port 22'
}
expect_fail verify_effective transition | grep -q 'unsafe Match context.*passwordauthentication no' || fail 'prepared retry accepted SSH policy drift'
# Source the actual preflight guards and supply only inert command functions.
# shellcheck disable=SC1090
source <(awk '/^check_ssh_sockets\(\) \{/,/^\}/' "$SCRIPT")
# shellcheck disable=SC2317 # Invoked by the extracted function.
systemctl() {
    case "$1" in
        list-unit-files) printf 'ssh.socket disabled disabled\n';;
        list-sockets) :;;
        is-active|is-enabled) return 1;;
    esac
}
expect_fail check_ssh_sockets | grep -q 'ssh.socket' || fail 'disabled installed socket accepted'
systemctl() {
    case "$1" in
        list-unit-files|list-sockets) :;;
        is-active|is-enabled) [[ ${SOCKET_ACTIVE:-0} == 1 && $3 == ssh.socket ]];;
    esac
}
SOCKET_ACTIVE=1
expect_fail check_ssh_sockets | grep -q 'ssh.socket' || fail 'active socket accepted on finalize recheck'
SOCKET_ACTIVE=0
check_ssh_sockets || fail 'socket-free host rejected'
# shellcheck disable=SC1090
source <(awk '/^check_docker_ports\(\) \{/,/^\}/' "$SCRIPT")
command() { if [[ $1 == -v && $2 == docker ]]; then return 0; fi; builtin command "$@"; }
# shellcheck disable=SC2317 # Invoked by the extracted function.
docker() {
    case "$1" in
        ps) printf 'abcdef\n';;
        inspect) printf '{"8080/tcp":[{"HostIp":"0.0.0.0","HostPort":"62238"}]}\n';;
    esac
}
expect_fail check_docker_ports | grep -qi 'Docker.*62238\|62238.*Docker' || fail 'Docker DNAT port accepted without ss listener'
port=62239
check_docker_ports || fail 'unmapped Docker target rejected'
port=62238
docker() { return 1; }
expect_fail check_docker_ports | grep -qi 'Docker' || fail 'Docker inventory error accepted'
# Transaction must become recoverable before any key/path mutation.
# A prepared retry must not silently ignore firewall intent or a deleted rule.
# shellcheck disable=SC1090
source <(awk '/^verify_ufw_retry\(\) \{/,/^\}/' "$SCRIPT")
# shellcheck disable=SC1090
source <(awk '/^ufw_owned_rules\(\) \{/,/^\}/' "$SCRIPT")
STATE="$tmp"; port=62238
install -m 600 /dev/null "$STATE/ufw-added"
printf '62238/tcp ALLOW IN Anywhere # hermes-ssh-workflow\n' >"$STATE/ufw-added"
# This fixture is unprivileged; only the ownership query is substituted.
stat() { if [[ ${*: -1} == "$STATE/ufw-added" ]]; then printf '0:600\n'; else command stat "$@"; fi; }
ufw() { printf 'Status: active\n[ 1] 62238/tcp ALLOW IN Anywhere # hermes-ssh-workflow\n'; }
expect_fail verify_ufw_retry 0 | grep -qi 'UFW.*mismatch' || fail 'retry without --ufw ignored firewall intent'
verify_ufw_retry 1 || fail 'retry rejected intact owned firewall rule'
ufw() { printf 'Status: active\n'; }
expect_fail verify_ufw_retry 1 | grep -qi 'UFW.*rule' || fail 'retry accepted deleted firewall rule'
rm "$STATE/ufw-added"
expect_fail verify_ufw_retry 1 | grep -qi 'UFW.*mismatch' || fail 'retry added --ufw without prepared firewall intent'
 # A file with ACL/xattrs must be refused before the first state write.
# shellcheck disable=SC1090
source <(awk '/^check_key_metadata\(\) \{/,/^\}/' "$SCRIPT")
auth="$tmp/authorized_keys"; sshdir="$tmp"
printf 'old key\n' >"$auth"
python3 - "$auth" <<'PYX'
import os, sys
os.setxattr(sys.argv[1], 'user.hermes_test', b'keep')
PYX
expect_fail check_key_metadata | grep -qi 'metadata\|xattr\|ACL' || fail 'extended key metadata silently discarded'
python3 - "$auth" <<'PYX'
import os, sys
os.removexattr(sys.argv[1], 'user.hermes_test')
PYX
check_key_metadata || fail 'plain authorized_keys rejected'
python3 - "$SCRIPT" <<'PY'
from pathlib import Path
import sys
s = Path(sys.argv[1]).read_text()
prepare = s.split('case "$phase" in\nprepare)\n', 1)[1].split('\nfinalize)\n', 1)[0]
finalize = s.split('\nfinalize)\n', 1)[1].split('\nrollback)\n', 1)[0]
assert prepare.index('write_meta preparing') < prepare.index('install -d -m 0700'), 'metadata follows first artifact'
assert 'check_ssh_sockets' in finalize, 'finalize missing socket recheck'
assert 'verify_effective final' in finalize.split('Already finalized', 1)[0], 'finalized retry skips effective policy'
assert 'sync_file "$STATE/authorized_keys.backup"' in prepare, 'backup not synced before mutation'
assert 'sync_dir "$sshdir"' in prepare, 'key rename not synced'
assert 'check_key_metadata' in prepare.split('write_meta preparing', 1)[0], 'metadata refusal happens after mutation'
assert 'cp -p -- "$auth" "$tmp_auth"' in prepare, 'staging loses original group'
assert 'chmod 600 "$STATE/auth-path" "$STATE"/authorized_keys.*' not in prepare, 'backup mode changed before rollback'
PY
printf 'PASS: shell syntax, CLI guards and security gates\n'
