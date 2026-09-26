#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

readonly USER_NAME="ubuntu"
readonly HOME_DIR="/home/ubuntu"
readonly LOGIN_SHELL="/bin/bash"
readonly SUDOERS_FILE="/etc/sudoers.d/90-ubuntu-dev-environment"
readonly SUDOERS_RULE="ubuntu ALL=(ALL:ALL) NOPASSWD: ALL"
readonly REPOSITORY_URL="https://github.com/ecourn/ubuntu-dev-environment.git"

log() { printf '[INFO] %s\n' "$*"; }
ok() { printf '[OK] %s\n' "$*"; }
die() { printf '[ERREUR] %s\n' "$*" >&2; exit 1; }

require_root() {
    [[ "$EUID" -eq 0 ]] || die "Lancez ce script avec sudo, par exemple : sudo $0"
}

check_ubuntu() {
    [[ -r /etc/os-release ]] || die "/etc/os-release est introuvable."
    # shellcheck disable=SC1091
    . /etc/os-release
    [[ "${ID:-}" == ubuntu ]] || die "Ce script cible Ubuntu. Système détecté : ${ID:-inconnu}."
    ok "Ubuntu détecté : ${PRETTY_NAME:-Ubuntu}"
}

initial_user_home() {
    local record
    if [[ -n "${SUDO_USER:-}" && "$SUDO_USER" != root ]]; then
        record="$(getent passwd "$SUDO_USER" || true)"
        [[ -n "$record" ]] || die "Le compte initial '$SUDO_USER' est introuvable."
        cut -d: -f6 <<< "$record"
    else
        printf '/root\n'
    fi
}

prepare_ssh_key() {
    local initial_home="$1"
    local key_source="$initial_home/.ssh/authorized_keys"
    local key_directory="$HOME_DIR/.ssh" key_file="$HOME_DIR/.ssh/authorized_keys"
    local user_group

    [[ ! -L "$HOME_DIR" ]] || die "$HOME_DIR ne doit pas être un lien symbolique."
    if [[ -e "$HOME_DIR" && ! -d "$HOME_DIR" ]]; then
        die "$HOME_DIR existe et n'est pas un répertoire. Aucune modification n'a été faite."
    fi
    if ! getent passwd "$USER_NAME" >/dev/null 2>&1 && [[ -e "$HOME_DIR" ]]; then
        die "$HOME_DIR existe sans compte ubuntu. Déplacez ou vérifiez ce répertoire avant de relancer."
    fi

    user_group="$(id -gn "$USER_NAME" 2>/dev/null || true)"
    if [[ -z "$user_group" ]]; then
        if [[ ! -f "$key_source" || -L "$key_source" || ! -s "$key_source" ]]; then
            die "Aucune clé SSH publique exploitable n'a été trouvée dans $key_source. Connectez-vous avec une clé SSH ou placez-y authorized_keys avant de créer ubuntu."
        fi
        adduser --disabled-password --gecos "" --home "$HOME_DIR" --shell "$LOGIN_SHELL" "$USER_NAME" >/dev/null
        user_group="$(id -gn "$USER_NAME")"
        install -d -o "$USER_NAME" -g "$user_group" -m 0700 "$key_directory"
        install -o "$USER_NAME" -g "$user_group" -m 0600 "$key_source" "$key_file"
        log "Compte ubuntu créé; la clé publique autorisée du compte initial a été copiée."
    else
        local actual_home actual_shell
        actual_home="$(getent passwd "$USER_NAME" | cut -d: -f6)"
        actual_shell="$(getent passwd "$USER_NAME" | cut -d: -f7)"
        [[ "$actual_home" == "$HOME_DIR" ]] || die "Le compte ubuntu utilise $actual_home au lieu de $HOME_DIR. Aucun changement de home n'a été fait."
        [[ "$actual_shell" == "$LOGIN_SHELL" ]] || usermod --shell "$LOGIN_SHELL" "$USER_NAME"
        user_group="$(id -gn "$USER_NAME")"

        [[ ! -L "$key_directory" ]] || die "$key_directory ne doit pas être un lien symbolique."
        [[ ! -L "$key_file" ]] || die "$key_file ne doit pas être un lien symbolique."
        if [[ ! -e "$key_file" ]]; then
            if [[ ! -f "$key_source" || -L "$key_source" || ! -s "$key_source" ]]; then
                die "Aucun authorized_keys n'existe pour ubuntu et aucune clé publique n'a été trouvée dans $key_source."
            fi
            install -d -o "$USER_NAME" -g "$user_group" -m 0700 "$key_directory"
            install -o "$USER_NAME" -g "$user_group" -m 0600 "$key_source" "$key_file"
            log "authorized_keys installé pour le compte ubuntu."
        fi
    fi

    (( $(id -u "$USER_NAME") >= 1000 )) || die "ubuntu doit être un compte utilisateur normal avec un UID supérieur ou égal à 1000."

    [[ -d "$key_directory" && ! -L "$key_directory" ]] || die "$key_directory doit être un répertoire normal, pas un lien symbolique."
    [[ -f "$key_file" && ! -L "$key_file" && -s "$key_file" ]] || die "$key_file doit contenir au moins une clé publique et ne pas être un lien symbolique."
    chown "$USER_NAME:$user_group" "$key_directory" "$key_file"
    chmod 0700 "$key_directory"
    chmod 0600 "$key_file"

    [[ -d "$HOME_DIR" ]] || die "Le home $HOME_DIR n'existe pas."
    chown "$USER_NAME:$user_group" "$HOME_DIR"
    ok "Home, shell et accès par clé SSH vérifiés pour ubuntu"
}

install_base_packages() {
    local -a packages=(ca-certificates curl git iproute2 openssh-client openssh-server python3 sudo unzip vim ripgrep openssl util-linux)
    export DEBIAN_FRONTEND=noninteractive
    log "Actualisation des index APT et installation des outils de base"
    apt-get update
    apt-get install -y "${packages[@]}"
    ok "curl, git, iproute2, sudo, OpenSSH client/serveur et outils de base disponibles"
    ensure_ssh_service
}

ensure_ssh_service() {
    local candidate active_unit=""
    command -v systemctl >/dev/null 2>&1 || return 0
    [[ -d /run/systemd/system ]] || return 0

    for candidate in ssh.socket sshd.socket ssh.service sshd.service; do
        if systemctl is-active --quiet "$candidate" 2>/dev/null; then
            ok "Service SSH déjà actif : $candidate"
            return 0
        fi
    done

    for candidate in ssh.socket sshd.socket ssh.service sshd.service; do
        if systemctl cat "$candidate" >/dev/null 2>&1; then
            active_unit="$candidate"
            break
        fi
    done
    [[ -n "$active_unit" ]] || die "OpenSSH Server est installé mais aucune unité systemd SSH n'a été trouvée."
    systemctl enable --now "$active_unit"
    systemctl is-active --quiet "$active_unit" || die "Impossible de démarrer l'unité SSH $active_unit."
    ok "Service SSH activé : $active_unit"
}

configure_sudo() {
    local temporary_file user_group
    user_group="$(id -gn "$USER_NAME")"
    usermod --append --groups sudo "$USER_NAME"

    visudo -c >/dev/null || die "La configuration sudo existante est invalide."
    if [[ -e "$SUDOERS_FILE" ]]; then
        [[ -f "$SUDOERS_FILE" && ! -L "$SUDOERS_FILE" ]] || die "$SUDOERS_FILE existe mais n'est pas un fichier normal."
        [[ "$(stat -c '%U:%G:%a' "$SUDOERS_FILE")" == root:root:440 ]] || die "$SUDOERS_FILE existe avec des propriétaire ou permissions inattendus."
        [[ "$(<"$SUDOERS_FILE")" == "$SUDOERS_RULE" ]] || die "$SUDOERS_FILE contient une règle différente; il n'a pas été écrasé."
    else
        temporary_file="$(mktemp /etc/sudoers.d/.ubuntu-dev-environment.XXXXXX)"
        printf '%s\n' "$SUDOERS_RULE" > "$temporary_file"
        chown root:root "$temporary_file"
        chmod 0440 "$temporary_file"
        visudo -cf "$temporary_file" >/dev/null || { rm -f -- "$temporary_file"; die "La règle sudo générée est invalide."; }
        mv -- "$temporary_file" "$SUDOERS_FILE"
    fi

    id -nG "$USER_NAME" | tr ' ' '\n' | grep -Fxq sudo || die "ubuntu n'appartient pas au groupe sudo."
    visudo -c >/dev/null || die "La configuration sudo finale est invalide."
    runuser -u "$USER_NAME" -- sudo -n true || die "Le sudo non interactif de ubuntu ne fonctionne pas."
    ok "Droits sudo non interactifs vérifiés pour ubuntu"
}

verify_prerequisites() {
    local command
    for command in curl git sudo runuser; do
        command -v "$command" >/dev/null 2>&1 || die "Commande requise introuvable après installation : $command"
    done
    [[ -x "$LOGIN_SHELL" ]] || die "$LOGIN_SHELL n'existe pas ou n'est pas exécutable."
    runuser -u "$USER_NAME" -- test -w "$HOME_DIR" || die "$HOME_DIR n'est pas accessible en écriture par ubuntu."
    ok "Prérequis du clone et des étapes suivantes vérifiés"
}

summary() {
    printf '\nConfiguration terminée.\n'
    printf 'Compte : %s (%s)\n' "$USER_NAME" "$HOME_DIR"
    printf 'Clé SSH : %s\n' "$HOME_DIR/.ssh/authorized_keys"
    printf 'Sudo : accès administrateur non interactif activé\n'
    printf '\nReconnectez-vous en tant que ubuntu, puis lancez :\n'
    printf '  git clone %s ~/ubuntu-dev-environment\n' "$REPOSITORY_URL"
    printf '  cd ~/ubuntu-dev-environment\n'
    printf 'Consultez ensuite 02-configuration-serveur/README.md.\n'
}

main() {
    require_root
    check_ubuntu
    local user_home
    user_home="$(initial_user_home)"
    install_base_packages
    prepare_ssh_key "$user_home"
    configure_sudo
    verify_prerequisites
    summary
}

main "$@"
