#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
export LC_ALL=C

TARGET_PORT="${SSH_PORT:-}"
REQUESTED_ADMIN_USER="${SSH_ADMIN_USER:-}"
MAX_AUTH_TRIES="${MAX_AUTH_TRIES:-3}"
LOGIN_GRACE_TIME="${LOGIN_GRACE_TIME:-20}"
CLIENT_ALIVE_INTERVAL="${CLIENT_ALIVE_INTERVAL:-300}"
CLIENT_ALIVE_COUNT_MAX="${CLIENT_ALIVE_COUNT_MAX:-2}"
F2B_MAX_RETRY="${F2B_MAX_RETRY:-5}"
F2B_FINDTIME="${F2B_FINDTIME:-10m}"
F2B_BANTIME="${F2B_BANTIME:-1h}"
FAIL2BAN_START_TIMEOUT="${FAIL2BAN_START_TIMEOUT:-15}"
BACKUP_ROOT="${BACKUP_ROOT:-/var/backups}"
DRY_RUN=0
KEEP_OLD_PORT=0

SSH_SERVICE=""
SSH_SOCKET_UNIT=""
ADMIN_USER=""
ADMIN_HOME=""
AUTHORIZED_KEYS_FILE=""
CURRENT_SSH_PORT=""
CLIENT_IP=""
BACKUP_DIR=""
TMP_DIR=""
FORWARD_SERVER_PID=""
TUNNEL_PID=""
TEMP_KEY_LINE=""
CHANGES_STARTED=0
SUCCESS=0
UFW_WAS_ACTIVE=0
UFW_WAS_INSTALLED=0
FAIL2BAN_WAS_ACTIVE=0
FAIL2BAN_WAS_ENABLED=0

SSH_DROPIN="/etc/ssh/sshd_config.d/00-hermes-hardening.conf"
LEGACY_SSH_DROPIN="/etc/ssh/sshd_config.d/99-hermes-hardening.conf"
FAIL2BAN_DROPIN="/etc/fail2ban/jail.d/99-hermes-sshd.local"
UFW_DEFAULTS="/etc/default/ufw"
SOCKET_DROPIN=""

OLD_PORTS=()
TRANSITION_PORTS=()
AUTHORIZED_KEYS_CANDIDATES=()

usage() {
    cat <<'EOF'
Usage: durcir-ssh.sh [options]

Options:
  --port PORT          port SSH final, défaut: port SSH actuel ou 22
  --user USER          compte SSH à conserver, défaut: SSH_ADMIN_USER ou détection sûre
  --dry-run            affiche les détections sans modifier la machine
  --keep-old-port      conserve les anciennes règles UFW à la fin
  -h, --help           affiche cette aide

Variables d'environnement utiles:
  SSH_PORT
  SSH_ADMIN_USER
  MAX_AUTH_TRIES
  LOGIN_GRACE_TIME
  CLIENT_ALIVE_INTERVAL
  CLIENT_ALIVE_COUNT_MAX
  F2B_MAX_RETRY
  F2B_FINDTIME
  F2B_BANTIME
  FAIL2BAN_START_TIMEOUT
  BACKUP_ROOT
EOF
}

die() { printf 'ERREUR: %s\n' "$*" >&2; exit 1; }
info() { printf '[+] %s\n' "$*"; }
warn() { printf '[!] %s\n' "$*" >&2; }
need_cmd() { command -v "$1" >/dev/null 2>&1 || die "commande manquante: $1"; }

valid_port() {
    [[ "${1:-}" =~ ^[0-9]+$ ]] || return 1
    (( 1 <= 10#$1 && 10#$1 <= 65535 ))
}

if [[ -z "$TARGET_PORT" ]]; then
    current_connection_port=""
    if [[ -n "${SSH_CONNECTION:-}" ]]; then
        IFS=' ' read -r _client_ip _client_port _server_ip current_connection_port _extra <<< "$SSH_CONNECTION"
    fi
    if valid_port "${current_connection_port:-}"; then
        TARGET_PORT="$current_connection_port"
    else
        TARGET_PORT=22
    fi
fi

valid_positive_integer() {
    [[ "${1:-}" =~ ^[0-9]+$ ]] || return 1
    (( 10#$1 > 0 ))
}

append_unique_to() {
    local array_name="$1" candidate="$2" item
    local -n array_ref="$array_name"
    for item in "${array_ref[@]}"; do
        [[ "$item" == "$candidate" ]] && return 0
    done
    array_ref+=("$candidate")
}

join_by_comma() { local IFS=,; printf '%s' "$*"; }
user_record() { getent passwd "$1" || true; }
user_home() { user_record "$1" | awk -F: 'NR == 1 { print $6 }'; }
user_uid() { user_record "$1" | awk -F: 'NR == 1 { print $3 }'; }

is_human_user() {
    local candidate="$1" record uid shell
    record="$(user_record "$candidate")"
    [[ -n "$record" ]] || return 1
    IFS=: read -r _ _ uid _ _ _ shell <<< "$record"
    (( uid >= 1000 )) || return 1
    [[ "$candidate" != root && "$candidate" != nobody ]] || return 1
    [[ "$shell" != */nologin && "$shell" != */false ]] || return 1
}

expand_authorized_keys_pattern() {
    local pattern="$1" uid
    uid="$(user_uid "$ADMIN_USER")"
    pattern="${pattern//%%/__PERCENT__}"
    pattern="${pattern//%h/$ADMIN_HOME}"
    pattern="${pattern//%u/$ADMIN_USER}"
    pattern="${pattern//%U/$uid}"
    pattern="${pattern//__PERCENT__/%}"
    [[ "$pattern" == /* ]] || pattern="$ADMIN_HOME/$pattern"
    printf '%s\n' "$pattern"
}

load_authorized_keys_candidates() {
    local pattern resolved
    local -a patterns=()
    AUTHORIZED_KEYS_CANDIDATES=()
    while read -r pattern; do
        [[ -n "$pattern" ]] || continue
        patterns+=("$pattern")
    done < <(
        sshd -T -C "user=$ADMIN_USER,addr=127.0.0.1,laddr=127.0.0.1,lport=$TARGET_PORT" 2>/dev/null |
            awk '$1 == "authorizedkeysfile" { for (i = 2; i <= NF; i++) print $i; exit }'
    )
    ((${#patterns[@]} > 0)) || patterns=(".ssh/authorized_keys" ".ssh/authorized_keys2")
    for pattern in "${patterns[@]}"; do
        resolved="$(expand_authorized_keys_pattern "$pattern")"
        append_unique_to AUTHORIZED_KEYS_CANDIDATES "$resolved"
    done
}

find_existing_authorized_keys_for_user() {
    local candidate="$1" previous_user="$ADMIN_USER" previous_home="$ADMIN_HOME" path
    ADMIN_USER="$candidate"
    ADMIN_HOME="$(user_home "$candidate")"
    [[ -n "$ADMIN_HOME" && -d "$ADMIN_HOME" ]] || {
        ADMIN_USER="$previous_user"; ADMIN_HOME="$previous_home"; return 1;
    }
    load_authorized_keys_candidates
    for path in "${AUTHORIZED_KEYS_CANDIDATES[@]}"; do
        if [[ -f "$path" && ! -L "$path" && -s "$path" ]] &&
            ssh-keygen -lf "$path" >/dev/null 2>&1; then
            AUTHORIZED_KEYS_FILE="$path"
            return 0
        fi
    done
    ADMIN_USER="$previous_user"
    ADMIN_HOME="$previous_home"
    AUTHORIZED_KEYS_FILE=""
    return 1
}

no_authorized_keys_error() {
    local candidate="$1" home
    home="$(user_home "$candidate")"
    [[ -n "$home" ]] || home="/home/$candidate"
    die "Aucune clé SSH publique exploitable n'a été trouvée pour $candidate. Vérifiez le fichier AuthorizedKeysFile (emplacement habituel : $home/.ssh/authorized_keys), terminez d'abord l'étape 1 du README et testez une connexion réelle par clé depuis votre poste client avant de relancer le durcissement. Ne transmettez jamais votre clé privée."
}

select_admin_user() {
    local name _ uid shell
    local -a matches=()

    if [[ -n "$REQUESTED_ADMIN_USER" ]]; then
        is_human_user "$REQUESTED_ADMIN_USER" || die "compte administrateur invalide ou non interactif: $REQUESTED_ADMIN_USER"
        find_existing_authorized_keys_for_user "$REQUESTED_ADMIN_USER" || no_authorized_keys_error "$REQUESTED_ADMIN_USER"
        return 0
    fi

    if [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != root ]] && is_human_user "$SUDO_USER"; then
        if find_existing_authorized_keys_for_user "$SUDO_USER"; then return 0; fi
    fi

    while IFS=: read -r name _ uid _ _ _ shell; do
        (( uid >= 1000 )) || continue
        [[ "$name" != nobody ]] || continue
        [[ "$shell" != */nologin && "$shell" != */false ]] || continue
        if find_existing_authorized_keys_for_user "$name"; then matches+=("$name"); fi
    done < <(getent passwd)

    ((${#matches[@]} > 0)) || die "Aucune clé SSH publique exploitable n'a été détectée pour un compte humain. Terminez l'étape 1 du README, testez une connexion par clé depuis votre poste client, puis relancez; ne transmettez jamais de clé privée."
    ((${#matches[@]} == 1)) || die "plusieurs comptes SSH sont éligibles (${matches[*]}); utilisez --user pour choisir explicitement"
    find_existing_authorized_keys_for_user "${matches[0]}" || die "impossible de résoudre authorized_keys pour ${matches[0]}"
}

detect_ssh_units() {
    local candidate
    SSH_SERVICE=""
    SSH_SOCKET_UNIT=""
    SOCKET_DROPIN=""
    for candidate in ssh sshd; do
        if systemctl cat "$candidate.service" >/dev/null 2>&1; then SSH_SERVICE="$candidate"; break; fi
    done
    [[ -n "$SSH_SERVICE" ]] || die "service SSH introuvable"
    for candidate in ssh.socket sshd.socket; do
        if systemctl cat "$candidate" >/dev/null 2>&1 && systemctl is-active --quiet "$candidate" 2>/dev/null; then
            SSH_SOCKET_UNIT="$candidate"
            SOCKET_DROPIN="/etc/systemd/system/${candidate}.d/99-hermes-hardening.conf"
            break
        fi
    done
}

detect_socket_ports() {
    local raw port
    [[ -n "$SSH_SOCKET_UNIT" ]] || return 0
    raw="$(systemctl show "$SSH_SOCKET_UNIT" -p Listen --value 2>/dev/null || true)"
    while read -r port; do
        valid_port "$port" && append_unique_to OLD_PORTS "$port"
    done < <(grep -oE '[0-9]{1,5}[[:space:]]+\(Stream\)' <<< "$raw" | awk '{print $1}' || true)
}

detect_current_ports() {
    local port
    OLD_PORTS=()
    CURRENT_SSH_PORT=""
    CLIENT_IP=""
    if [[ -n "${SSH_CONNECTION:-}" ]]; then
        IFS=' ' read -r CLIENT_IP _ _ CURRENT_SSH_PORT <<< "$SSH_CONNECTION"
        valid_port "$CURRENT_SSH_PORT" && append_unique_to OLD_PORTS "$CURRENT_SSH_PORT"
    fi
    if [[ -n "$SSH_SOCKET_UNIT" ]]; then
        detect_socket_ports
    fi
    if [[ -z "$SSH_SOCKET_UNIT" || ${#OLD_PORTS[@]} -eq 0 ]]; then
        while read -r port; do
            valid_port "$port" && append_unique_to OLD_PORTS "$port"
        done < <(sshd -T 2>/dev/null | awk '$1 == "port" { print $2 }')
    fi
    ((${#OLD_PORTS[@]} > 0)) || die "aucun port SSH actuel n'a pu être déterminé"
}

port_is_listening() {
    local port="$1"
    ss -Hlnt 2>/dev/null | awk -v p="$port" '{a=$4; sub(/^.*:/,"",a); if (a==p) found=1} END {exit found ? 0 : 1}'
}

preflight_target_port() {
    local port
    for port in "${OLD_PORTS[@]}"; do [[ "$port" == "$TARGET_PORT" ]] && return 0; done
    port_is_listening "$TARGET_PORT" && die "le port cible $TARGET_PORT est déjà occupé par un autre listener"
    return 0
}

show_discovery() {
    printf 'Compte administratif : %s\n' "$ADMIN_USER"
    printf 'Répertoire personnel : %s\n' "$ADMIN_HOME"
    printf 'authorized_keys : %s\n' "$AUTHORIZED_KEYS_FILE"
    printf 'Service SSH : %s.service\n' "$SSH_SERVICE"
    printf 'Socket SSH actif : %s\n' "${SSH_SOCKET_UNIT:-aucun}"
    printf 'Ports SSH détectés : %s\n' "${OLD_PORTS[*]}"
    printf 'Port SSH final : %s\n' "$TARGET_PORT"
    printf 'Client SSH actuel : %s\n' "${CLIENT_IP:-non détecté / console locale}"
    if command -v ufw >/dev/null 2>&1; then printf 'État UFW : %s\n' "$(ufw status 2>/dev/null | head -n1 || true)"; else printf 'État UFW : non installé\n'; fi
}

backup_path() {
    local path="$1" name="$2"
    if [[ -e "$path" || -L "$path" ]]; then
        printf 'present\n' > "$BACKUP_DIR/$name.state"
        cp -a -- "$path" "$BACKUP_DIR/$name"
    else
        printf 'absent\n' > "$BACKUP_DIR/$name.state"
    fi
}

restore_path() {
    local path="$1" name="$2" state
    [[ -f "$BACKUP_DIR/$name.state" ]] || return 0
    state="$(<"$BACKUP_DIR/$name.state")"
    rm -rf -- "$path"
    if [[ "$state" == present ]]; then
        mkdir -p -- "$(dirname "$path")"
        cp -a -- "$BACKUP_DIR/$name" "$path"
    fi
}

backup_state() {
    BACKUP_DIR="$BACKUP_ROOT/ssh-hardening-$(date -u +%Y%m%dT%H%M%SZ)-$$"
    install -d -o root -g root -m 0700 "$BACKUP_DIR"
    if dpkg-query -W -f='${Status}' ufw 2>/dev/null | grep -qx 'install ok installed'; then UFW_WAS_INSTALLED=1; else UFW_WAS_INSTALLED=0; fi
    backup_path "$SSH_DROPIN" ssh-dropin
    backup_path "$LEGACY_SSH_DROPIN" legacy-ssh-dropin
    backup_path "$FAIL2BAN_DROPIN" fail2ban-dropin
    [[ -z "$SOCKET_DROPIN" ]] || backup_path "$SOCKET_DROPIN" socket-dropin
    backup_path /etc/default/ufw ufw-defaults
    backup_path /etc/ufw ufw-dir
    if command -v ufw >/dev/null 2>&1 && ufw status 2>/dev/null | grep -q '^Status: active'; then UFW_WAS_ACTIVE=1; else UFW_WAS_ACTIVE=0; fi
    if systemctl is-active --quiet fail2ban.service 2>/dev/null; then FAIL2BAN_WAS_ACTIVE=1; else FAIL2BAN_WAS_ACTIVE=0; fi
    if systemctl is-enabled --quiet fail2ban.service 2>/dev/null; then FAIL2BAN_WAS_ENABLED=1; else FAIL2BAN_WAS_ENABLED=0; fi
    info "sauvegarde transactionnelle : $BACKUP_DIR"
}

refresh_ufw_backup_after_install() {
    (( UFW_WAS_INSTALLED )) && return 0
    backup_path /etc/default/ufw ufw-defaults
    backup_path /etc/ufw ufw-dir
}

restore_ufw_state() {
    restore_path /etc/default/ufw ufw-defaults
    restore_path /etc/ufw ufw-dir
    if command -v ufw >/dev/null 2>&1; then
        if (( UFW_WAS_ACTIVE )); then
            ufw --force enable >/dev/null 2>&1 || true
            ufw reload >/dev/null 2>&1 || true
        else
            ufw --force disable >/dev/null 2>&1 || true
        fi
    fi
}

rollback_state() {
    warn "échec détecté : restauration des éléments gérés"
    restore_path "$SSH_DROPIN" ssh-dropin
    restore_path "$LEGACY_SSH_DROPIN" legacy-ssh-dropin
    restore_path "$FAIL2BAN_DROPIN" fail2ban-dropin
    [[ -z "$SOCKET_DROPIN" ]] || restore_path "$SOCKET_DROPIN" socket-dropin
    restore_ufw_state
    systemctl daemon-reload >/dev/null 2>&1 || true
    [[ -z "$SSH_SOCKET_UNIT" ]] || systemctl restart "$SSH_SOCKET_UNIT" >/dev/null 2>&1 || true
    systemctl reload "$SSH_SERVICE.service" >/dev/null 2>&1 || systemctl restart "$SSH_SERVICE.service" >/dev/null 2>&1 || true
    if systemctl cat fail2ban.service >/dev/null 2>&1; then
        if (( FAIL2BAN_WAS_ENABLED )); then systemctl enable fail2ban.service >/dev/null 2>&1 || true; else systemctl disable fail2ban.service >/dev/null 2>&1 || true; fi
        if (( FAIL2BAN_WAS_ACTIVE )); then systemctl restart fail2ban.service >/dev/null 2>&1 || true; else systemctl stop fail2ban.service >/dev/null 2>&1 || true; fi
    fi
    warn "rollback terminé; sauvegarde conservée dans $BACKUP_DIR"
}

remove_temp_key() {
    [[ -n "$TEMP_KEY_LINE" && -n "$AUTHORIZED_KEYS_FILE" && -f "$AUTHORIZED_KEYS_FILE" ]] || return 0
    python3 - "$AUTHORIZED_KEYS_FILE" "$TEMP_KEY_LINE" <<'PY'
from pathlib import Path
import os, sys
path = Path(sys.argv[1])
target = sys.argv[2]
st = path.stat()
lines = path.read_text().splitlines(keepends=True)
kept = [line for line in lines if line.rstrip("\r\n") != target]
if kept != lines:
    tmp = path.with_name(path.name + ".hermes-cleanup.tmp")
    tmp.write_text("".join(kept))
    os.chown(tmp, st.st_uid, st.st_gid)
    os.chmod(tmp, st.st_mode & 0o7777)
    os.replace(tmp, path)
PY
    TEMP_KEY_LINE=""
}

cleanup() {
    local rc="$1"
    trap - EXIT
    set +e
    if [[ -n "${TUNNEL_PID:-}" ]]; then kill "$TUNNEL_PID" 2>/dev/null || true; wait "$TUNNEL_PID" 2>/dev/null || true; fi
    if [[ -n "${FORWARD_SERVER_PID:-}" ]]; then kill "$FORWARD_SERVER_PID" 2>/dev/null || true; wait "$FORWARD_SERVER_PID" 2>/dev/null || true; fi
    remove_temp_key
    if (( CHANGES_STARTED && ! SUCCESS )); then rollback_state; fi
    [[ -z "${TMP_DIR:-}" ]] || rm -rf -- "$TMP_DIR"
    exit "$rc"
}
trap 'cleanup $?' EXIT

ensure_ufw_ipv6() {
    if awk -F= '$1 ~ /^[[:space:]]*IPV6[[:space:]]*$/ {gsub(/[[:space:]]/,"",$2); if (toupper($2)=="YES") ok=1} END {exit ok ? 0 : 1}' "$UFW_DEFAULTS"; then return 0; fi
    python3 - "$UFW_DEFAULTS" <<'PY'
from pathlib import Path
import re, sys
path = Path(sys.argv[1])
text = path.read_text()
if re.search(r"(?m)^\s*IPV6\s*=", text): text = re.sub(r"(?m)^\s*IPV6\s*=.*$", "IPV6=yes", text)
else: text = text.rstrip() + "\nIPV6=yes\n"
path.write_text(text)
PY
}

write_ssh_config() {
    local output="$1" port
    shift
    {
        printf '# Managed by durcir-ssh.sh.\n'
        for port in "$@"; do printf 'Port %s\n' "$port"; done
        printf '%s\n' \
            'PermitRootLogin no' \
            'AuthenticationMethods publickey' \
            'PubkeyAuthentication yes' \
            'PasswordAuthentication no' \
            'KbdInteractiveAuthentication no' \
            'PermitEmptyPasswords no' \
            'GSSAPIAuthentication no' \
            'HostbasedAuthentication no'
        printf 'AllowUsers %s\n' "$ADMIN_USER"
        printf 'MaxAuthTries %s\n' "$MAX_AUTH_TRIES"
        printf 'LoginGraceTime %s\n' "$LOGIN_GRACE_TIME"
        printf 'ClientAliveInterval %s\n' "$CLIENT_ALIVE_INTERVAL"
        printf 'ClientAliveCountMax %s\n' "$CLIENT_ALIVE_COUNT_MAX"
        printf '%s\n' \
            'X11Forwarding no' \
            'AllowAgentForwarding no' \
            'AllowTcpForwarding local' \
            'AllowStreamLocalForwarding local' \
            'GatewayPorts no' \
            'PermitTunnel no'
    } > "$output"
}

write_socket_config() {
    local output="$1" port
    shift
    {
        printf '# Managed by durcir-ssh.sh.\n[Socket]\nListenStream=\nBindIPv6Only=ipv6-only\n'
        for port in "$@"; do
            printf 'ListenStream=0.0.0.0:%s\n' "$port"
            printf 'ListenStream=[::]:%s\n' "$port"
        done
    } > "$output"
}

write_fail2ban_config() {
    local output="$1" ports
    shift
    ports="$(join_by_comma "$@")"
    cat > "$output" <<EOF
# Managed by durcir-ssh.sh.
[sshd]
enabled = true
backend = systemd
port = $ports
maxretry = $F2B_MAX_RETRY
findtime = $F2B_FINDTIME
bantime = $F2B_BANTIME
banaction = ufw
EOF
}

validate_ssh_policy() {
    local expected effective port wanted p
    local -a expected_ports=("$@")
    sshd -t
    effective="$(sshd -T -C "user=$ADMIN_USER,addr=127.0.0.1,laddr=127.0.0.1,lport=$TARGET_PORT")"
    for port in "${expected_ports[@]}"; do
        awk -v wanted="port $port" '$0==wanted {found=1} END {exit found ? 0 : 1}' <<< "$effective" || die "politique SSH effective inattendue : port $port absent"
    done
    while read -r expected; do
        [[ -n "$expected" ]] || continue
        awk -v wanted="$expected" '$0==wanted {found=1} END {exit found ? 0 : 1}' <<< "$effective" || die "politique SSH effective inattendue : $expected"
    done <<EOF
permitrootlogin no
authenticationmethods publickey
pubkeyauthentication yes
passwordauthentication no
kbdinteractiveauthentication no
permitemptypasswords no
gssapiauthentication no
hostbasedauthentication no
allowusers $ADMIN_USER
maxauthtries $MAX_AUTH_TRIES
logingracetime $LOGIN_GRACE_TIME
clientaliveinterval $CLIENT_ALIVE_INTERVAL
clientalivecountmax $CLIENT_ALIVE_COUNT_MAX
x11forwarding no
allowagentforwarding no
allowtcpforwarding local
allowstreamlocalforwarding local
gatewayports no
permittunnel no
EOF
    while read -r port; do
        valid_port "$port" || continue
        wanted=0
        for p in "${expected_ports[@]}"; do [[ "$p" == "$port" ]] && wanted=1; done
        (( wanted )) || die "un port SSH non prévu reste effectif : $port"
    done < <(awk '$1=="port" {print $2}' <<< "$effective")
}

ufw_has_managed_port() {
    local port="$1"
    ufw status numbered 2>/dev/null | awk -v wanted="${port}/tcp" '
        index($0, wanted) && index($0, "SSH managed by durcir-ssh.sh") {found=1}
        END {exit found ? 0 : 1}
    '
}

allow_ufw_port() {
    local port="$1"
    ufw_has_managed_port "$port" || ufw allow "$port/tcp" comment "SSH managed by durcir-ssh.sh"
}

list_managed_ufw_ports() {
    ufw status numbered 2>/dev/null | awk '''
        index($0, "SSH managed by durcir-ssh.sh") {
            for (i = 1; i <= NF; i++) {
                if ($i ~ /^[0-9]+\/tcp$/) {
                    port = $i
                    sub(/\/tcp$/, "", port)
                    print port
                    break
                }
            }
        }
    ''' | awk '!seen[$0]++'
}

delete_ufw_port() {
    local port="$1" number
    while :; do
        number="$(ufw status numbered 2>/dev/null | awk -v wanted="${port}/tcp" '''
            index($0, wanted) && index($0, "SSH managed by durcir-ssh.sh") {
                line = $0
                sub(/^\[[[:space:]]*/, "", line)
                sub(/\].*$/, "", line)
                if (line ~ /^[0-9]+$/) n = line
            }
            END {if (n != "") print n}
        ''')"
        [[ -n "$number" ]] || break
        ufw --force delete "$number" >/dev/null
    done
}

cleanup_managed_ufw_ports() {
    local port
    (( KEEP_OLD_PORT )) && return 0
    while read -r port; do
        valid_port "$port" || continue
        [[ "$port" == "$TARGET_PORT" ]] && continue
        delete_ufw_port "$port"
    done < <(list_managed_ufw_ports)
}

verify_final_ufw_state() {
    local stale
    ufw status 2>/dev/null | grep -q '^Status: active' || die "UFW n'est pas actif après finalisation"
    ufw_has_managed_port "$TARGET_PORT" || die "la règle UFW du port SSH final $TARGET_PORT est absente"
    if (( ! KEEP_OLD_PORT )); then
        stale="$(list_managed_ufw_ports | awk -v target="$TARGET_PORT" '$0 != target')"
        [[ -z "$stale" ]] || die "des anciennes règles UFW SSH gérées subsistent : ${stale//$'\n'/, }"
    fi
}

configure_ufw() {
    local port
    ensure_ufw_ipv6
    ufw default deny incoming
    ufw default allow outgoing
    for port in "${TRANSITION_PORTS[@]}"; do allow_ufw_port "$port"; done
    ufw --force enable
}

install_transition_configs() {
    write_ssh_config "$TMP_DIR/sshd-transition.conf" "${TRANSITION_PORTS[@]}"
    write_fail2ban_config "$TMP_DIR/fail2ban-transition.local" "${TRANSITION_PORTS[@]}"
    install -d -o root -g root -m 0755 /etc/ssh/sshd_config.d /etc/fail2ban/jail.d
    install -o root -g root -m 0644 "$TMP_DIR/sshd-transition.conf" "$SSH_DROPIN"
    rm -f -- "$LEGACY_SSH_DROPIN"
    install -o root -g root -m 0644 "$TMP_DIR/fail2ban-transition.local" "$FAIL2BAN_DROPIN"
    if [[ -n "$SSH_SOCKET_UNIT" ]]; then
        install -d -o root -g root -m 0755 "$(dirname "$SOCKET_DROPIN")"
        write_socket_config "$TMP_DIR/socket-transition.conf" "${TRANSITION_PORTS[@]}"
        install -o root -g root -m 0644 "$TMP_DIR/socket-transition.conf" "$SOCKET_DROPIN"
    fi
}

apply_ssh_runtime() {
    systemctl daemon-reload
    [[ -z "$SSH_SOCKET_UNIT" ]] || systemctl restart "$SSH_SOCKET_UNIT"
    if systemctl is-active --quiet "$SSH_SERVICE.service" 2>/dev/null; then
        systemctl reload "$SSH_SERVICE.service" || systemctl restart "$SSH_SERVICE.service"
    elif [[ -z "$SSH_SOCKET_UNIT" ]]; then
        systemctl start "$SSH_SERVICE.service"
    fi
}

verify_listener() { port_is_listening "$1"; }
verify_all_listeners() { local port; for port in "$@"; do verify_listener "$port" || die "le port SSH attendu n'écoute pas : $port"; done; }
verify_no_listener() { local port="$1"; verify_listener "$port" && die "un ancien port SSH écoute encore : $port"; return 0; }
verify_ssh_handshake() { timeout 10 ssh-keyscan -T 5 -p "$TARGET_PORT" 127.0.0.1 >/dev/null 2>&1 || die "échec du handshake SSH sur le port $TARGET_PORT"; }

dump_fail2ban_diagnostics() {
    warn "diagnostic Fail2Ban :"
    systemctl status fail2ban.service --no-pager -l >&2 || true
    journalctl -u fail2ban.service -b --no-pager -n 80 >&2 || true
}

wait_for_fail2ban() {
    local deadline=$((SECONDS + FAIL2BAN_START_TIMEOUT))
    while (( SECONDS < deadline )); do
        if fail2ban-client ping >/dev/null 2>&1; then return 0; fi
        if systemctl is-failed --quiet fail2ban.service 2>/dev/null; then
            dump_fail2ban_diagnostics
            return 1
        fi
        sleep 0.25
    done
    dump_fail2ban_diagnostics
    return 1
}

activate_fail2ban() {
    fail2ban-client -t
    systemctl enable fail2ban.service

    if systemctl is-active --quiet fail2ban.service 2>/dev/null; then
        wait_for_fail2ban || die "Fail2Ban est actif mais son socket n'est pas devenu disponible"
        systemctl reload fail2ban.service
    else
        systemctl start fail2ban.service
    fi

    wait_for_fail2ban || die "Fail2Ban n'a pas démarré correctement"
    fail2ban-client status sshd >/dev/null || {
        dump_fail2ban_diagnostics
        die "le jail Fail2Ban sshd n'est pas opérationnel"
    }
}

reload_fail2ban() {
    fail2ban-client -t
    wait_for_fail2ban || die "Fail2Ban n'est plus joignable avant rechargement"
    systemctl reload fail2ban.service
    wait_for_fail2ban || die "Fail2Ban n'est plus joignable après rechargement"
    fail2ban-client status sshd >/dev/null || {
        dump_fail2ban_diagnostics
        die "le jail Fail2Ban sshd n'est plus opérationnel après rechargement"
    }
}

verify_local_key_and_tunnel() {
    local test_key remote_test_port local_forward_port marker public_key i
    local -a ssh_args tunnel_args
    [[ -f "$AUTHORIZED_KEYS_FILE" && ! -L "$AUTHORIZED_KEYS_FILE" ]] || die "authorized_keys doit être un fichier régulier et non un lien symbolique"
    test_key="$TMP_DIR/temporary-test-key"
    marker="durcir-ssh-test-$$-$(date +%s)"
    ssh-keygen -q -t ed25519 -N '' -C "$marker" -f "$test_key"
    public_key="$(<"${test_key}.pub")"
    TEMP_KEY_LINE="$public_key"
    printf '%s\n' "$TEMP_KEY_LINE" >> "$AUTHORIZED_KEYS_FILE"

    ssh_args=(-o BatchMode=yes -o IdentitiesOnly=yes -o PreferredAuthentications=publickey -o PasswordAuthentication=no -o KbdInteractiveAuthentication=no -o GSSAPIAuthentication=no -o HostbasedAuthentication=no -o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=10 -i "$test_key" -p "$TARGET_PORT")
    ssh "${ssh_args[@]}" "$ADMIN_USER@127.0.0.1" true
    info "connexion SSH par clé validée sur $TARGET_PORT"

    cat > "$TMP_DIR/forward-server.py" <<'PY'
import pathlib, socket, sys
port_file = pathlib.Path(sys.argv[1])
server = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
server.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
server.bind(("127.0.0.1", 0))
server.listen(1)
port_file.write_text(str(server.getsockname()[1]))
conn, _ = server.accept()
data = conn.recv(1024)
conn.sendall(b"HERMES_TUNNEL_OK:" + data)
conn.close(); server.close()
PY
    python3 "$TMP_DIR/forward-server.py" "$TMP_DIR/forward.port" &
    FORWARD_SERVER_PID=$!
    for ((i=0; i<50; i++)); do [[ -s "$TMP_DIR/forward.port" ]] && break; sleep 0.1; done
    [[ -s "$TMP_DIR/forward.port" ]] || die "serveur de test du tunnel indisponible"
    remote_test_port="$(<"$TMP_DIR/forward.port")"
    local_forward_port="$(python3 - <<'PY'
import socket
s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1]); s.close()
PY
)"
    tunnel_args=("${ssh_args[@]}" -o ExitOnForwardFailure=yes -N -T -L "127.0.0.1:${local_forward_port}:127.0.0.1:${remote_test_port}")
    # The SSH target is intentionally expanded locally; no remote command is passed.
    # shellcheck disable=SC2029
    ssh "${tunnel_args[@]}" "$ADMIN_USER@127.0.0.1" &
    TUNNEL_PID=$!
    python3 - "$local_forward_port" <<'PY'
import socket, sys, time
port=int(sys.argv[1]); deadline=time.time()+10; expected=b"HERMES_TUNNEL_OK:ping"
while time.time()<deadline:
    try:
        with socket.create_connection(("127.0.0.1",port),timeout=0.5) as sock:
            sock.sendall(b"ping"); response=sock.recv(1024)
        if response==expected: raise SystemExit(0)
    except OSError: time.sleep(0.2)
raise SystemExit("tunnel local non fonctionnel")
PY
    kill "$TUNNEL_PID" 2>/dev/null || true; wait "$TUNNEL_PID" 2>/dev/null || true; TUNNEL_PID=""
    kill "$FORWARD_SERVER_PID" 2>/dev/null || true; wait "$FORWARD_SERVER_PID" 2>/dev/null || true; FORWARD_SERVER_PID=""
    remove_temp_key
    info "tunnel TCP local -L validé"
}

confirm_client_key_pretested() {
    local tty_path="${1:-/dev/tty}" answer
    [[ -r "$tty_path" && -w "$tty_path" ]] || die "Un terminal est nécessaire pour confirmer le test PuTTY réalisé après l'étape 1."
    printf "\nAvant le durcissement, confirmez que vous avez ouvert une deuxième session PuTTY après l'étape 1.\n" >&2
    printf "Elle doit utiliser le compte ubuntu et la clé privée .ppk correspondante, et l'authentification par clé doit avoir réussi.\n" >&2
    if ! IFS= read -r -p "Tapez oui pour confirmer ce test préalable : " answer < "$tty_path"; then
        die "Test préalable non confirmé; aucune modification SSH, UFW ou Fail2ban n'a commencé."
    fi
    [[ "$answer" == "oui" ]] || die "Terminez d'abord le test PuTTY de l'étape 1. Aucune modification SSH, UFW ou Fail2ban n'a commencé."
    info "test PuTTY préalable confirmé"
}

confirm_external_ssh() {
    local answer
    printf '\n'
    printf 'Le port %s écoute et le test SSH local a réussi.\n' "$TARGET_PORT"
    printf 'Depuis votre poste client, ouvrez une deuxième connexion SSH vers ce port.\n'
    printf 'Gardez cette session ouverte. Revenez ici après avoir vérifié que la nouvelle connexion fonctionne.\n'
    if ! IFS= read -r -p "Tapez oui pour confirmer cette politique SSH et finaliser : " answer </dev/tty; then
        die "validation SSH interrompue; restauration de la configuration précédente"
    fi
    [[ "$answer" == "oui" ]] || die "nouvelle connexion SSH non confirmée; restauration de la configuration précédente"
}

install_final_configs() {
    write_ssh_config "$TMP_DIR/sshd-final.conf" "$TARGET_PORT"
    write_fail2ban_config "$TMP_DIR/fail2ban-final.local" "$TARGET_PORT"
    install -o root -g root -m 0644 "$TMP_DIR/sshd-final.conf" "$SSH_DROPIN"
    install -o root -g root -m 0644 "$TMP_DIR/fail2ban-final.local" "$FAIL2BAN_DROPIN"
    if [[ -n "$SSH_SOCKET_UNIT" ]]; then
        write_socket_config "$TMP_DIR/socket-final.conf" "$TARGET_PORT"
        install -o root -g root -m 0644 "$TMP_DIR/socket-final.conf" "$SOCKET_DROPIN"
    fi
}

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
while (($# > 0)); do
    case "$1" in
        --port) (($# >= 2)) || die "--port attend une valeur"; TARGET_PORT="$2"; shift 2 ;;
        --port=*) TARGET_PORT="${1#*=}"; shift ;;
        --user) (($# >= 2)) || die "--user attend une valeur"; REQUESTED_ADMIN_USER="$2"; shift 2 ;;
        --user=*) REQUESTED_ADMIN_USER="${1#*=}"; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        --keep-old-port) KEEP_OLD_PORT=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "option inconnue : $1" ;;
    esac
done

(( EUID == 0 )) || die "exécuter en root, par exemple : sudo bash $0"
valid_port "$TARGET_PORT" || die "port invalide : $TARGET_PORT"
valid_positive_integer "$MAX_AUTH_TRIES" || die "MAX_AUTH_TRIES invalide"
valid_positive_integer "$LOGIN_GRACE_TIME" || die "LOGIN_GRACE_TIME invalide"
valid_positive_integer "$CLIENT_ALIVE_INTERVAL" || die "CLIENT_ALIVE_INTERVAL invalide"
valid_positive_integer "$CLIENT_ALIVE_COUNT_MAX" || die "CLIENT_ALIVE_COUNT_MAX invalide"
valid_positive_integer "$F2B_MAX_RETRY" || die "F2B_MAX_RETRY invalide"
valid_positive_integer "$FAIL2BAN_START_TIMEOUT" || die "FAIL2BAN_START_TIMEOUT invalide"
for command in awk date dpkg-query getent grep install journalctl mktemp ss sshd ssh-keygen systemctl; do need_cmd "$command"; done
command -v apt-get >/dev/null 2>&1 || die "ce script nécessite apt-get (Debian/Ubuntu)"

detect_ssh_units
select_admin_user
detect_current_ports
preflight_target_port
if (( DRY_RUN )); then show_discovery; exit 0; fi
[[ -r /dev/tty && -w /dev/tty ]] || die "un terminal interactif est nécessaire pour valider le nouvel accès SSH depuis le client"
confirm_client_key_pretested /dev/tty

TMP_DIR="$(mktemp -d)"
backup_state

export DEBIAN_FRONTEND=noninteractive
apt-get update
apt-get install -y openssh-client ufw fail2ban python3 python3-systemd
refresh_ufw_backup_after_install
CHANGES_STARTED=1
for command in fail2ban-client ssh ssh-keygen ssh-keyscan timeout ufw; do need_cmd "$command"; done

detect_ssh_units
detect_current_ports
preflight_target_port
select_admin_user
TRANSITION_PORTS=("${OLD_PORTS[@]}")
append_unique_to TRANSITION_PORTS "$TARGET_PORT"

install_transition_configs
validate_ssh_policy "${TRANSITION_PORTS[@]}"
configure_ufw
apply_ssh_runtime
verify_all_listeners "${TRANSITION_PORTS[@]}"
verify_ssh_handshake
activate_fail2ban
verify_local_key_and_tunnel
confirm_external_ssh

install_final_configs
validate_ssh_policy "$TARGET_PORT"
apply_ssh_runtime
verify_listener "$TARGET_PORT" || die "le port cible n'écoute plus après la finalisation"
verify_ssh_handshake
for port in "${OLD_PORTS[@]}"; do
    if [[ "$port" != "$TARGET_PORT" ]]; then
        verify_no_listener "$port"
    fi
done
cleanup_managed_ufw_ports
verify_final_ufw_state
reload_fail2ban

SUCCESS=1
info "durcissement terminé"
info "compte SSH autorisé : $ADMIN_USER"
info "port SSH final : $TARGET_PORT"
info "sauvegarde : $BACKUP_DIR"
ufw status verbose
fail2ban-client status sshd
fi
