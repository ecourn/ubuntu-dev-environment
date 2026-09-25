#!/usr/bin/env bash
# Transactional SSH transition. Run from an existing remote SSH session as root.
set -Eeuo pipefail
export LC_ALL=C
PATH=/usr/sbin:/usr/bin:/sbin:/bin
readonly STATE=/var/lib/hermes-ssh-workflow DROPIN=/etc/ssh/sshd_config.d/00-hermes-workflow.conf
phase=${1:-}; shift || true
port='' user='' pub='' ufw_opt=0 console_override=0
usage() { printf '%s\n' 'Usage: ssh-workflow.sh prepare --user USER --public-key FILE --port PORT [--ufw]' '       ssh-workflow.sh finalize [--console-override] | status | rollback' 'prepare keeps the old port; finalize requires a NEW external SSH session on the new port.' 'Console override: explicitly accept the risk only from a physical/provider console, never SSH.' 'UFW: --ufw adds a new-port rule to an already active firewall; never changes defaults or old rules.'; }
die() { printf 'ERROR: %s\n' "$*" >&2; exit 1; }
case "$phase" in -h|--help) usage; exit 0;; prepare)
    while (($#)); do case "$1" in
        --user|--public-key|--port) (($# >= 2)) || die "missing value for $1"; case "$1" in --user) user=$2;; --public-key) pub=$2;; --port) port=$2;; esac; shift 2;;
        --ufw) ufw_opt=1; shift;; *) die "unknown option: $1";; esac; done;;
    finalize) if [[ ${1:-} == --console-override && $# == 1 ]]; then console_override=1; shift; fi
        (($# == 0)) || die "unknown option: $1";;
    status|rollback) (($# == 0)) || die "unknown option: $1";;
    *) die "unknown phase: $phase";; esac
valid_port() { [[ "$1" =~ ^[1-9][0-9]{0,4}$ ]] && ((10#$1 <= 65535)); }
# Never trust client-supplied localhost proof; finalization must be invoked over the new port.
connection() {
    local client cp server sp extra
    read -r client cp server sp extra <<< "${SSH_CONNECTION:-}"
    if ! { [[ -n "$client" && -n "$server" && -z "${extra:-}" ]] && valid_port "${cp:-}" && valid_port "${sp:-}"; }; then die 'valid SSH_CONNECTION from remote SSH session required'; fi
    [[ "$client" =~ ^[0-9A-Fa-f:.]+$ && "$server" =~ ^[0-9A-Fa-f:.]+$ ]] || die 'SSH_CONNECTION must contain literal IP addresses'
    [[ "$client" != 127.* && "$client" != ::1 && "$client" != ::ffff:127.* && "$client" != "$server" ]] || die 'localhost/self connection cannot prove external SSH access'
    CLIENT=$client; SERVER=$server; CLIENT_PORT=$cp; SESSION_PORT=$sp
}
verify_session_socket() {
    local records local_addr peer_addr recvq sendq owners pid ancestor
    # SSH_CONNECTION is caller-controlled. Require its exact TCP tuple to be
    # established in the kernel, owned by an sshd ancestor of this process.
    records=$(ss -Htnp state established) || die 'cannot inventory established TCP sockets'
    while read -r recvq sendq local_addr peer_addr owners; do
        [[ $recvq =~ ^[0-9]+$ && $sendq =~ ^[0-9]+$ ]] || continue
        [[ ( $local_addr == "$SERVER:$SESSION_PORT" && $peer_addr == "$CLIENT:$CLIENT_PORT" ) ||
           ( $local_addr == "[$SERVER]:$SESSION_PORT" && $peer_addr == "[$CLIENT]:$CLIENT_PORT" ) ]] || continue
        while [[ $owners =~ \"sshd(-session)?\",pid=([0-9]+), ]]; do
            pid=${BASH_REMATCH[2]}
            for ancestor in "${SSHD_ANCESTORS[@]}"; do
                [[ $pid == "$ancestor" ]] && return 0
            done
            owners=${owners#*"${BASH_REMATCH[0]}"}
        done
    done <<< "$records"
    die 'SSH_CONNECTION does not match an established kernel TCP socket'
}
verify_session_ancestry() {
    local pid=$PPID name count=0
    SSHD_ANCESTORS=()
    while ((pid > 1 && count++ < 32)); do
        name=$(<"/proc/$pid/comm") || die 'cannot inspect session ancestry'
        if [[ $name == sshd || $name == sshd-session ]]; then SSHD_ANCESTORS+=("$pid"); fi
        pid=$(awk '/^PPid:/ {print $2}' "/proc/$pid/status") || die 'cannot inspect session ancestry'
    done
    ((${#SSHD_ANCESTORS[@]})) || die 'no sshd ancestor: SSH_CONNECTION environment is insufficient proof'
}
verify_console_ancestry() {
    local pid=$PPID name count=0
    while ((pid > 1 && count++ < 32)); do
        name=$(<"/proc/$pid/comm") || die 'cannot inspect console ancestry'
        [[ $name != sshd && $name != sshd-session ]] || die 'console override prohibited from an SSH session'
        pid=$(awk '/^PPid:/ {print $2}' "/proc/$pid/status") || die 'cannot inspect console ancestry'
    done
}
if [[ "$phase" == prepare ]]; then valid_port "$port" || die 'invalid target port'; fi
((EUID == 0)) || die 'root required (use sudo; preserve SSH_CONNECTION for prepare/finalize)'
if [[ "$phase" == finalize && $console_override == 0 ]]; then connection; verify_session_ancestry; verify_session_socket; fi
[[ ! -L "$STATE" && ! -L "$DROPIN" ]] || die 'symlink at managed path'
[[ ! -L "$STATE/meta" ]] || die 'symlink state metadata'
if [[ -e "$STATE" ]]; then
    [[ -d "$STATE" && $(stat -c %u:%a "$STATE") == 0:700 ]] || die 'unsafe state directory ownership or mode'
fi
if [[ "$phase" == status ]]; then
    if [[ -f "$STATE/meta" ]]; then
        awk -F= '$1=="phase" || $1=="port" || $1=="old_port" || $1=="user" || $1=="service" {print}' "$STATE/meta"
    elif [[ -d "$STATE" ]] && [[ -n $(find "$STATE" -mindepth 1 -maxdepth 1 ! -name lock ! -name 'rolled-back-*' -print -quit) ]]; then
        printf '%s\n' 'phase=incomplete'
    else printf '%s\n' 'phase=absent'; fi
    exit 0
fi
if [[ ! -e "$STATE" ]]; then mkdir -m 0700 -- "$STATE" || die 'cannot create state directory'; fi
[[ $(stat -c %u:%a "$STATE") == 0:700 ]] || die 'unsafe state directory ownership or mode'
[[ ! -L "$STATE/lock" && ( ! -e "$STATE/lock" || -f "$STATE/lock" ) ]] || die 'unsafe state lock'
if [[ -e "$STATE/lock" ]]; then [[ $(stat -c %u:%a "$STATE/lock") == 0:600 ]] || die 'unsafe state lock ownership or mode'; fi
umask 077
exec 9>"$STATE/lock"; flock -x 9 || die 'state lock unavailable'
meta="$STATE/meta"
get_meta() { local key=$1; sed -n "s/^${key}=//p" "$meta" | head -n 1; }
sync_file() { python3 -c 'import os,sys; f=os.open(sys.argv[1],os.O_RDONLY|os.O_NOFOLLOW); os.fsync(f); os.close(f)' "$1" || die "cannot sync $1"; }
sync_dir() { python3 -c 'import os,sys; f=os.open(sys.argv[1],os.O_RDONLY|os.O_DIRECTORY|os.O_NOFOLLOW); os.fsync(f); os.close(f)' "$1" || die "cannot sync directory $1"; }
sync_dir "$(dirname "$STATE")"
write_meta() {
    local tmp
    tmp=$(mktemp "$STATE/.meta.XXXXXXXX")
    chmod 600 "$tmp"
    printf 'phase=%s\nport=%s\nold_port=%s\nuser=%s\nservice=%s\n' "$1" "$port" "$old_port" "$user" "$service" >"$tmp"
    sync_file "$tmp"
    mv -fT -- "$tmp" "$meta"
    sync_dir "$STATE"
}
[[ ! -L "$meta" ]] || die 'symlink state metadata'
if [[ "$phase" != prepare ]]; then
    [[ -f "$meta" ]] || die 'incomplete or absent transition state; preserve artifacts for manual review'
    [[ $(stat -c %u:%a "$meta") == 0:600 ]] || die 'unsafe transition metadata'
    port=$(get_meta port); old_port=$(get_meta old_port); user=$(get_meta user); service=$(get_meta service)
    if ! { valid_port "$port" && valid_port "$old_port" && [[ "$user" =~ ^[a-z_][a-z0-9_-]*$ && "$service" =~ ^(ssh|sshd)\.service$ ]]; }; then die 'invalid state metadata'; fi
    [[ $(wc -l <"$meta") == 5 ]] || die 'unexpected state metadata'
fi
atomic_config() {
    local source=$1 tmp
    tmp=$(mktemp /etc/ssh/sshd_config.d/.hermes-workflow.XXXXXXXX)
    install -m 0644 -o root -g root "$source" "$tmp"
    sync_file "$tmp"
    mv -fT -- "$tmp" "$DROPIN"
    sync_dir /etc/ssh/sshd_config.d
}
check_ssh_sockets() {
    local unit units sockets
    units=$(systemctl list-unit-files --type=socket --all --no-legend --no-pager) || die 'cannot inventory installed systemd socket units'
    if grep -Eq '^(ssh|sshd)(@[^[:space:]]+)?\.socket[[:space:]]' <<<"$units"; then die 'ssh.socket/sshd.socket unit installed (even if disabled); manual review required'; fi
    for unit in ssh.socket sshd.socket; do
        if systemctl is-active --quiet "$unit" || systemctl is-enabled --quiet "$unit"; then die "$unit active/enabled: socket activation must be handled manually"; fi
    done
    sockets=$(systemctl list-sockets --all --no-legend --no-pager) || die 'cannot inventory systemd sockets'
    if grep -Eq '(ssh|sshd)(\.service|\.socket)' <<<"$sockets"; then die 'SSH-related systemd socket discovered; inspect before continuing'; fi
}
check_docker_ports() {
    local ids id mappings result
    if command -v docker >/dev/null 2>&1; then
        ids=$(docker ps --format '{{.ID}}') || die 'cannot inventory Docker containers'
        while IFS= read -r id; do
            [[ -n $id ]] || continue
            mappings=$(docker inspect --format '{{json .NetworkSettings.Ports}}' "$id") || die 'cannot inventory Docker published ports'
            result=0
            printf '%s\n' "$mappings" | python3 -c '
import json, sys
try:
    mappings = json.load(sys.stdin)
    assert isinstance(mappings, dict)
    conflict = any(k.endswith("/tcp") and isinstance(bindings, list) and any(
        isinstance(b, dict) and b.get("HostPort") == sys.argv[1] for b in bindings
    ) for k, bindings in mappings.items())
except (ValueError, TypeError, AssertionError):
    sys.exit(2)
sys.exit(1 if conflict else 0)
' "$port" || result=$?
            case $result in 0) :;; 1) die "Docker container $id publishes target TCP port $port (DNAT may bypass UFW)";; *) die 'cannot parse Docker published ports';; esac
        done <<< "$ids"
        printf 'WARNING: Docker published ports may bypass UFW via DNAT; review Docker and upstream firewall rules.\n' >&2
    fi
}
reload_ssh() { systemctl reload "$service" || die 'SSH reload failed; run rollback from existing session'; }
verify_listener() {
    local output p=$1 expected=$2
    output=$(ss -Hlnpt "sport = :$p") || die "cannot inspect listener on port $p"
    if [[ $expected == present ]]; then
        grep -q 'sshd' <<<"$output" || die "sshd listener missing on port $p"
    else
        [[ -z $output ]] || die "old listener still present on port $p"
    fi
}
verify_match_contexts() {
    local stage=$1 context effective setting seen_port
    sshd -t || die 'invalid sshd configuration'
    for context in "user=$user,addr=$CLIENT,laddr=$SERVER,lport=$port" \
                   "user=root,addr=$CLIENT,laddr=$SERVER,lport=$port" \
                   "user=$user,addr=$CLIENT,laddr=$SERVER,lport=$old_port"; do
        effective=$(sshd -T -C "$context") || die "cannot inspect Match context $context"
        for setting in 'authenticationmethods publickey' 'pubkeyauthentication yes' 'passwordauthentication no' 'kbdinteractiveauthentication no' 'permitrootlogin no' 'gssapiauthentication no' 'hostbasedauthentication no' 'permitemptypasswords no'; do
            grep -Fqx "$setting" <<<"$effective" || die "unsafe Match context $context: $setting"
        done
        if [[ $context == user="$user",* ]]; then
            grep -Fqx 'strictmodes yes' <<<"$effective" || die 'StrictModes disabled'
            grep -Eq '^authorizedkeysfile (\.ssh/authorized_keys|%h/\.ssh/authorized_keys)( \.ssh/authorized_keys2)?$' <<<"$effective" || die 'nonstandard AuthorizedKeysFile; manual review required'
            grep -Fqx 'authorizedkeyscommand none' <<<"$effective" || die 'AuthorizedKeysCommand requires manual review'
        fi
        grep -Fqx "port $port" <<<"$effective" || die 'target SSH port not effective'
        while read -r seen_port; do
            [[ $seen_port == "$port" || ( $stage == transition && $seen_port == "$old_port" ) ]] || die "unexpected SSH port: $seen_port"
        done < <(awk '$1 == "port" {print $2}' <<<"$effective")
        if [[ $stage == transition ]]; then
            grep -Fqx "port $old_port" <<<"$effective" || die 'old SSH port not preserved in transition'
        elif grep -Fqx "port $old_port" <<<"$effective"; then die 'old port still configured'; fi
    done
}
verify_effective() {
    local stage=$1 effective setting
    verify_match_contexts "$stage"
    effective=$(sshd -T -C "user=$user,addr=$CLIENT,laddr=$SERVER,lport=$port") || die 'cannot read effective sshd configuration'
    for setting in 'authenticationmethods publickey' 'pubkeyauthentication yes' 'passwordauthentication no' 'kbdinteractiveauthentication no' 'permitrootlogin no' 'gssapiauthentication no' 'hostbasedauthentication no' 'permitemptypasswords no'; do
        grep -Fqx "$setting" <<<"$effective" || die "effective sshd setting differs: $setting"
    done
    grep -Fqx "port $port" <<<"$effective" || die 'target SSH port not effective'
    local seen_port
    while read -r seen_port; do
        [[ "$seen_port" == "$port" || ( "$stage" == transition && "$seen_port" == "$old_port" ) ]] || die "unexpected SSH port: $seen_port"
    done < <(awk '$1 == "port" {print $2}' <<<"$effective")
    if [[ "$stage" == transition ]]; then
        grep -Fqx "port $old_port" <<<"$effective" || die 'old SSH port not preserved in transition'
    else
        if grep -Fqx "port $old_port" <<<"$effective"; then die 'old port still configured; manual config review required'; fi
    fi
}
check_key_metadata() {
    # Refuse extended attributes (including POSIX ACLs) before state mutation.
    python3 - "$sshdir" "$auth" <<'PY' || die 'unsupported ACL/xattr metadata on SSH key path; manual review required'
import os, sys
for path in sys.argv[1:]:
    if os.path.exists(path) and os.listxattr(path, follow_symlinks=False):
        sys.exit(1)
PY
}
ufw_owned_rules() {
    local output
    output=$(ufw status numbered) || die 'cannot inspect UFW rules'
    grep -Fxq 'Status: active' <<<"$output" || die 'UFW inactive or status changed'
    sed -nE 's/^[[:space:]]*\[[[:space:]]*[0-9]+\][[:space:]]+//p' <<<"$output" |
        grep -E "^${port}/tcp[[:space:]]+ALLOW IN[[:space:]]+Anywhere( \(v6\))?[[:space:]]+# hermes-ssh-workflow[[:space:]]*$" || true
}
verify_ufw_retry() {
    local requested=$1 actual
    if [[ -f "$STATE/ufw-added" ]]; then
        ((requested == 1)) || die 'UFW option mismatch on prepared retry; manual review required'
        [[ ! -L "$STATE/ufw-added" && $(stat -c %u:%a "$STATE/ufw-added") == 0:600 ]] || die 'unsafe UFW state'
        actual=$(ufw_owned_rules)
        [[ -n "$actual" && "$actual" == "$(<"$STATE/ufw-added")" ]] || die 'UFW managed rule missing or changed; manual review required'
    else
        ((requested == 0)) || die 'UFW option mismatch on prepared retry; manual review required'
    fi
}
case "$phase" in
prepare)
    connection; verify_session_ancestry; verify_session_socket
    [[ ${SUDO_USER:-} == "$user" ]] || die 'sudo initiator must be the selected admin user'
    if [[ -e "$meta" ]]; then
        [[ -f "$meta" && $(stat -c %u:%a "$meta") == 0:600 ]] || die 'unsafe transition metadata'
        if [[ $(get_meta phase) == prepared && $(get_meta port) == "$port" && $(get_meta user) == "$user" && -f "$DROPIN" && -f "$STATE/transition.conf" ]] && cmp -s "$DROPIN" "$STATE/transition.conf"; then
            [[ -f "$pub" && ! -L "$pub" ]] || die 'public key missing on retry'
            key=$(<"$pub"); auth=$(<"$STATE/auth-path")
            grep -Fqx -- "$key" "$auth" || die 'prepared key changed; manual review required'
            old_port=$(get_meta old_port)
            verify_ufw_retry "$ufw_opt"
            verify_effective transition
            verify_listener "$old_port" present; verify_listener "$port" present
            printf 'Already prepared on port %s; keep old session open.\n' "$port"
            exit 0
        fi
        die 'existing workflow state; inspect status and rollback first'
    fi
    [[ ! -e "$DROPIN" ]] || die 'managed SSH drop-in already exists'
    [[ ! -e "$STATE/auth-path" && ! -e "$STATE/authorized_keys.backup" && ! -e "$STATE/authorized_keys.absent" && ! -e "$STATE/transition.conf" ]] || die 'incomplete state; review and rollback manually'
    [[ "$user" =~ ^[a-z_][a-z0-9_-]*$ && "$user" != root ]] || die 'invalid admin user'
    valid_port "$port" || die 'invalid target port'
    [[ -f "$pub" && ! -L "$pub" && -s "$pub" ]] || die 'public key must be a nonempty regular file'
    [[ $(awk 'END {print NR}' "$pub") == 1 ]] || die 'expected exactly one public key line'
    key=$(<"$pub")
    [[ $key != *$'\r'* ]] || die 'carriage return in public key'
    [[ "$key" =~ ^(ssh-ed25519|ecdsa-sha2-nistp256|ssh-rsa)[[:space:]][A-Za-z0-9+/=]+([[:space:]].*)?$ ]] || die 'invalid public key format'
    ssh-keygen -lf "$pub" >/dev/null || die 'invalid SSH public key'
    old_port=$SESSION_PORT
    [[ "$old_port" != "$port" ]] || die 'target port must differ from current SSH session port'
    id "$user" >/dev/null || die 'admin account missing'
    home=$(getent passwd "$user" | cut -d: -f6)
    [[ "$home" == /* && -d "$home" && ! -L "$home" ]] || die 'unsafe admin home'
    sshdir="$home/.ssh"; auth="$sshdir/authorized_keys"
    [[ ! -L "$sshdir" && ! -L "$auth" ]] || die 'symlink in authorized_keys path'
    [[ ! -e "$auth" || -f "$auth" ]] || die 'authorized_keys is not a regular file'
    [[ ! -e "$sshdir" || -d "$sshdir" ]] || die 'unsafe .ssh directory'
    [[ ! -e "$auth" || $(stat -c %U "$auth") == "$user" ]] || die 'authorized_keys owner mismatch'
    [[ ! -e "$sshdir" || $(stat -c %U "$sshdir") == "$user" ]] || die '.ssh owner mismatch'
    check_key_metadata
    check_ssh_sockets
    services=$(systemctl list-units --all --type=service --no-legend --no-pager) || die 'cannot inventory services'
    if grep -Eq '(^|[[:space:]])[^[:space:]]*ssh[^[:space:]]*\.service[[:space:]]' <<<"$services" && grep -Eq '(ssh@|sshd@)' <<<"$services"; then die 'templated SSH service detected; manual review required'; fi
    service=
    for unit in ssh.service sshd.service; do
        if systemctl is-active --quiet "$unit"; then [[ -z "$service" ]] || die 'multiple SSH services active'; service=$unit; fi
    done
    [[ -n "$service" ]] || die 'no active SSH service (socket activation unsupported)'
    # Refuse unmanaged listeners and detect Docker published ports (not necessarily visible in ss).
    listening=$(ss -Hlnpt "sport = :$port") || die 'cannot inspect target port'
    [[ -z $listening ]] || die 'target port already listening'
    check_docker_ports
    effective=$(sshd -T -C "user=$user,addr=$CLIENT,laddr=$SERVER,lport=$port") || die 'cannot inspect SSH policy'
    grep -Fqx 'pubkeyauthentication yes' <<<"$effective" || die 'public key authentication not effective'
    grep -Eq '^authorizedkeysfile (\.ssh/authorized_keys|%h/\.ssh/authorized_keys)( \.ssh/authorized_keys2)?$' <<<"$effective" || die 'nonstandard AuthorizedKeysFile'
    # Avoid modifying any existing firewall policy or rule. UFW must already be active.
    if ((ufw_opt)); then
        command -v ufw >/dev/null || die 'ufw missing'
        ufw status verbose >&2
        ufw status | grep -qx 'Status: active' || die 'UFW inactive; configure it manually before opting in'
        ufw status numbered >&2
    fi
    write_meta preparing
    if [[ ! -e "$sshdir" ]]; then install -d -m 0700 -o "$user" -g "$(id -gn "$user")" "$sshdir"; sync_dir "$home"; fi
    if [[ -e "$auth" ]]; then cp -p -- "$auth" "$STATE/authorized_keys.backup" || die 'cannot back up authorized_keys'; sync_file "$STATE/authorized_keys.backup"; else : >"$STATE/authorized_keys.absent"; sync_file "$STATE/authorized_keys.absent"; fi
    printf '%s\n' "$auth" >"$STATE/auth-path"
    chmod 600 "$STATE/auth-path"
    if [[ -f "$STATE/authorized_keys.absent" ]]; then chmod 600 "$STATE/authorized_keys.absent"; fi
    sync_file "$STATE/auth-path"; sync_dir "$STATE"
    if ! grep -Fqx -- "$key" "$auth" 2>/dev/null; then
        tmp_auth=$(mktemp "$sshdir/.authorized_keys.XXXXXXXX") || die 'cannot stage authorized_keys'
        if [[ -e "$auth" ]]; then cp -p -- "$auth" "$tmp_auth" || die 'cannot preserve authorized_keys ownership and mode'; fi
        if [[ -s "$tmp_auth" && -n $(tail -c 1 -- "$tmp_auth") ]]; then printf '\n' >>"$tmp_auth"; fi
        printf '%s\n' "$key" >>"$tmp_auth"
        if [[ ! -e "$auth" ]]; then chown "$user:$(id -gn "$user")" "$tmp_auth"; chmod 600 "$tmp_auth"; fi
        sync_file "$tmp_auth"
        mv -T -- "$tmp_auth" "$auth"
        sync_dir "$sshdir"
    fi
    cp -p -- "$auth" "$STATE/authorized_keys.after" || die 'cannot snapshot installed key'
    sync_file "$STATE/authorized_keys.after"
    printf 'Port %s\nPort %s\nPubkeyAuthentication yes\nAuthenticationMethods publickey\nPasswordAuthentication no\nKbdInteractiveAuthentication no\nGSSAPIAuthentication no\nHostbasedAuthentication no\nPermitEmptyPasswords no\nPermitRootLogin no\n' "$old_port" "$port" >"$STATE/transition.conf"
    chmod 600 "$STATE/transition.conf"
    sync_file "$STATE/transition.conf"; sync_dir "$STATE"
    atomic_config "$STATE/transition.conf"
    sshd -t || die 'sshd validation failed; run rollback'
    verify_effective transition
    reload_ssh
    verify_listener "$old_port" present
    verify_listener "$port" present
    if ((ufw_opt)); then
        ufw allow "$port/tcp" comment 'hermes-ssh-workflow' || die 'UFW rule failed; run rollback'
        owned=$(ufw_owned_rules)
        [[ -n "$owned" ]] || die 'UFW managed rule not visible after insertion; run rollback'
        printf '%s\n' "$owned" >"$STATE/ufw-added"
        sync_file "$STATE/ufw-added"; sync_dir "$STATE"
    fi
    write_meta prepared
    printf 'Prepared: keep current session open. Open a SECOND external SSH connection on port %s with your private key held on your client, then finalize there.\n' "$port";;
finalize)
    case $(get_meta phase) in prepared|finalizing|finalized) ;; *) die 'not prepared; rollback incomplete transition if necessary';; esac
    if ((console_override)); then
        [[ -z ${SSH_CONNECTION:-} && -t 0 && -t 1 ]] || die 'console override requires an interactive local console without SSH_CONNECTION'
        verify_console_ancestry
        CLIENT=127.0.0.1 SERVER=127.0.0.1
    else
        [[ "$SESSION_PORT" == "$port" ]] || die 'SSH_CONNECTION must prove a NEW session on target port'
        [[ ${SUDO_USER:-} == "$user" ]] || die 'sudo initiator must be the selected admin user'
    fi
    check_ssh_sockets
    if [[ $(get_meta phase) == finalized ]]; then
        if ! { [[ -f "$DROPIN" && -f "$STATE/final.conf" ]] && cmp -s "$DROPIN" "$STATE/final.conf"; }; then die 'finalized config changed'; fi
        verify_effective final
        verify_listener "$port" present; verify_listener "$old_port" absent
        printf 'Already finalized on port %s.\n' "$port"; exit 0
    fi
    if [[ $(get_meta phase) == prepared ]]; then
        if ! { [[ -f "$DROPIN" ]] && cmp -s "$DROPIN" "$STATE/transition.conf"; }; then die 'managed SSH config changed outside workflow'; fi
    else
        if ! { [[ -f "$DROPIN" ]] && { cmp -s "$DROPIN" "$STATE/final.conf" || cmp -s "$DROPIN" "$STATE/transition.conf"; }; }; then die 'interrupted finalization changed config; manual review required'; fi
    fi
    printf 'Port %s\nPubkeyAuthentication yes\nAuthenticationMethods publickey\nPasswordAuthentication no\nKbdInteractiveAuthentication no\nGSSAPIAuthentication no\nHostbasedAuthentication no\nPermitEmptyPasswords no\nPermitRootLogin no\n' "$port" >"$STATE/final.conf"
    chmod 600 "$STATE/final.conf"
    sync_file "$STATE/final.conf"; sync_dir "$STATE"
    write_meta finalizing
    atomic_config "$STATE/final.conf"
    sshd -t || die 'sshd validation failed; run rollback'
    verify_effective final
    reload_ssh
    verify_listener "$port" present
    verify_listener "$old_port" absent
    write_meta finalized
    printf 'Finalized on port %s. Old UFW rules and other service ports were not changed.\n' "$port";;
rollback)
    [[ $(get_meta phase) != finalized ]] || die 'finalized configuration; manual recovery required to avoid closing active sessions'
    if [[ -e "$DROPIN" ]]; then
        if [[ -f "$STATE/transition.conf" ]] && cmp -s "$DROPIN" "$STATE/transition.conf"; then rm -- "$DROPIN"
        elif [[ -f "$STATE/final.conf" ]] && cmp -s "$DROPIN" "$STATE/final.conf"; then rm -- "$DROPIN"
        else die 'managed SSH config changed outside workflow; manual recovery required'; fi
    fi
    sync_dir /etc/ssh/sshd_config.d
    sshd -t || die 'sshd validation failed after rollback; manual intervention required'
    reload_ssh
    verify_listener "$old_port" present
    if [[ -f "$STATE/auth-path" ]]; then
        auth=$(<"$STATE/auth-path")
        [[ "$auth" == /*/\.ssh/authorized_keys && ! -L "$auth" ]] || die 'unsafe authorized_keys rollback path'
        [[ -f "$auth" && ! -L "$auth" ]] || die 'missing authorized_keys; preserve keys for manual review'
        if [[ ! -f "$STATE/authorized_keys.after" ]]; then
            printf 'NOTICE: key snapshot incomplete; preserve %s for manual review.\n' "$auth" >&2
        elif cmp -s "$auth" "$STATE/authorized_keys.after"; then
            if [[ -f "$STATE/authorized_keys.backup" ]]; then
                tmp_auth=$(mktemp "$(dirname "$auth")/.authorized_keys.XXXXXXXX") || die 'cannot stage key rollback'
                cp -p -- "$STATE/authorized_keys.backup" "$tmp_auth" || die 'cannot preserve key backup metadata'
                sync_file "$tmp_auth"
                mv -T -- "$tmp_auth" "$auth"
                sync_dir "$(dirname "$auth")"
            elif [[ -f "$STATE/authorized_keys.absent" ]]; then
                printf 'NOTICE: key retained: it is the only recorded access for this user. Review %s manually.\n' "$auth" >&2
            else die 'missing key backup'; fi
        else
            printf 'NOTICE: authorized_keys changed since preparation; preserve %s for manual review.\n' "$auth" >&2
        fi
    fi
    if [[ -f "$STATE/ufw-added" ]]; then printf 'NOTICE: UFW new-port rule retained for safety; review manually.\n' >&2; fi
    write_meta rolled_back
    # Archive only this workflow's fixed state files; keep evidence and permit
    # another prepare without silently adopting an interrupted transaction.
    archive="$STATE/rolled-back-$(date +%s)-$$"
    mkdir -m 0700 -- "$archive" || die 'cannot archive rollback state'
    for artifact in meta auth-path authorized_keys.backup authorized_keys.absent authorized_keys.after transition.conf final.conf ufw-added; do
        if [[ -e "$STATE/$artifact" || -L "$STATE/$artifact" ]]; then
            mv -T -- "$STATE/$artifact" "$archive/$artifact" || die 'cannot archive rollback state'
        fi
    done
    sync_dir "$archive"; sync_dir "$STATE"
    printf '%s\n' 'Rolled back managed SSH drop-in; old SSH configuration restored.';;
esac
