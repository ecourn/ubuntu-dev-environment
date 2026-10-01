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
        if ! record="$(getent passwd "$SUDO_USER")"; then record=""; fi
        [[ -n "$record" ]] || die "Le compte initial '$SUDO_USER' est introuvable."
        cut -d: -f6 <<< "$record"
    else
        printf '/root\n'
    fi
}


validate_initial_public_key_source() {
    local initial_home="$1"
    local ssh_dir="$initial_home/.ssh"
    local auth="$ssh_dir/authorized_keys"
    [[ -d "$initial_home" && ! -L "$initial_home" ]] || die "Le home initial $initial_home doit être un répertoire normal."
    [[ ! -L "$ssh_dir" ]] || die "$ssh_dir est un lien symbolique inattendu; vérifiez-le avant de relancer."
    if [[ -e "$ssh_dir" && ! -d "$ssh_dir" ]]; then
        die "$ssh_dir existe mais n'est pas un répertoire."
    fi
    [[ ! -L "$auth" ]] || die "$auth est un lien symbolique inattendu; vérifiez-le avant de relancer."
    if [[ -e "$auth" && ! -f "$auth" ]]; then
        die "$auth existe mais n'est pas un fichier normal."
    fi
}

authorized_keys_status() {
    local path="$1"
    [[ ! -L "$path" ]] || return 4
    [[ -e "$path" ]] || return 1
    [[ -f "$path" ]] || return 4
    [[ -s "$path" ]] || return 2
    ssh-keygen -lf "$path" >/dev/null 2>&1 || return 3
    return 0
}

key_fingerprint_for_line() {
    local line="$1" scratch_file="$2" listing fingerprint
    local -a fingerprint_fields=()
    [[ -n "${line//[[:space:]]/}" ]] || return 1
    line="${line%$'\r'}"
    printf '%s\n' "$line" > "$scratch_file"
    listing="$(ssh-keygen -lf "$scratch_file" 2>/dev/null)" || return 1
    [[ "$listing" != *$'\n'* ]] || return 1
    read -r -a fingerprint_fields <<< "$listing"
    fingerprint="${fingerprint_fields[1]:-}"
    [[ -n "$fingerprint" ]] || return 1
    printf '%s\n' "$fingerprint"
}

prompt_public_key() {
    local tty_path="$1" prompt_mode="${2:-required}" tty_fd key_line scratch_directory scratch_file fingerprint
    [[ -r "$tty_path" && -w "$tty_path" ]] || return 2
    if ! { exec {tty_fd}<>"$tty_path"; } 2>/dev/null; then return 2; fi
    if [[ "$prompt_mode" == "optional" ]]; then
        printf "\nUne clé publique OpenSSH exploitable est déjà disponible.\n" >&"$tty_fd"
        printf 'Si vous avez créé une nouvelle clé dans PuTTYgen, collez son champ « Public key for pasting into OpenSSH authorized_keys file » pour autoriser la clé privée .ppk correspondante.\n' >&"$tty_fd"
        printf 'Appuyez sur Entrée pour utiliser uniquement les clés déjà trouvées.\n' >&"$tty_fd"
    else
        printf "\nAucune clé publique OpenSSH exploitable n'a été trouvée.\n" >&"$tty_fd"
        printf 'PuTTYgen : collez la valeur du champ « Public key for pasting into OpenSSH authorized_keys file ».\n' >&"$tty_fd"
    fi
    printf "Ne collez jamais la clé privée et n'envoyez jamais le fichier privé .ppk au serveur.\n" >&"$tty_fd"
    printf 'Exemple de format : ssh-ed25519 AAAAC3... utilisateur@poste\n' >&"$tty_fd"
    printf 'Clé publique OpenSSH : ' >&"$tty_fd"
    if ! IFS= read -r key_line <&"$tty_fd"; then
        exec {tty_fd}>&-
        return 3
    fi
    exec {tty_fd}>&-
    if [[ -z "${key_line//[[:space:]]/}" ]]; then
        [[ "$prompt_mode" == "optional" ]] && return 1
        return 3
    fi
    scratch_directory="$(mktemp -d)" || return 3
    scratch_file="$scratch_directory/public-key"
    if fingerprint="$(key_fingerprint_for_line "$key_line" "$scratch_file")"; then
        rm -f -- "$scratch_file"
        rmdir -- "$scratch_directory"
        printf '%s\n' "${key_line%$'\r'}"
        return 0
    fi
    rm -f -- "$scratch_file"
    rmdir -- "$scratch_directory"
    return 3
}

ensure_authorized_keys() {
    local target_home="$1" target_user="$2" target_group="$3" initial_home="$4"
    local tty_path="${5:-/dev/tty}" prompted_key="${6:-}" optional_prompt_done="${7:-0}"
    local source_directory="$initial_home/.ssh" source_file="$initial_home/.ssh/authorized_keys"
    local key_directory="$target_home/.ssh" key_file="$target_home/.ssh/authorized_keys"
    local source_status=0 target_status=0 prompt_status=0
    local temporary_file scratch_file line fingerprint key_added=0
    local -A fingerprints=()

    [[ ! -L "$target_home" ]] || die "$target_home ne doit pas être un lien symbolique."
    [[ -d "$target_home" ]] || die "Le home $target_home n'existe pas ou n'est pas un répertoire normal."
    [[ ! -L "$key_directory" ]] || die "$key_directory ne doit pas être un lien symbolique."
    if [[ -e "$key_directory" && ! -d "$key_directory" ]]; then
        die "$key_directory existe mais n'est pas un répertoire. Aucune clé n'a été écrasée."
    fi
    [[ ! -L "$key_file" ]] || die "$key_file ne doit pas être un lien symbolique."
    if [[ -e "$key_file" && ! -f "$key_file" ]]; then
        die "$key_file existe mais n'est pas un fichier normal. Aucune clé n'a été écrasée."
    fi
    [[ ! -L "$source_directory" ]] || die "$source_directory est un lien symbolique inattendu; vérifiez-le avant de relancer."
    if [[ -e "$source_directory" && ! -d "$source_directory" ]]; then
        die "$source_directory existe mais n'est pas un répertoire."
    fi
    [[ ! -L "$source_file" ]] || die "$source_file est un lien symbolique inattendu; vérifiez-le manuellement."
    if [[ -e "$source_file" && ! -f "$source_file" ]]; then
        die "$source_file existe mais n'est pas un fichier normal."
    fi

    authorized_keys_status "$source_file" || source_status=$?
    authorized_keys_status "$key_file" || target_status=$?
    (( source_status != 4 )) || die "$source_file n'est pas un fichier normal. Ne placez jamais de clé privée sur le serveur."
    (( target_status != 4 )) || die "$key_file n'est pas un fichier normal. Aucune clé existante n'a été remplacée."

    if (( source_status != 0 && target_status != 0 )) && [[ -z "$prompted_key" ]]; then
        local key_reason destination_reason
        case "$source_status" in
            1) key_reason="$source_file est absent" ;;
            2) key_reason="$source_file est vide" ;;
            *) key_reason="$source_file ne contient aucune clé publique OpenSSH valide" ;;
        esac
        case "$target_status" in
            1) destination_reason="$key_file est absent" ;;
            2) destination_reason="$key_file est vide" ;;
            *) destination_reason="$key_file ne contient aucune clé publique OpenSSH valide" ;;
        esac
        if prompted_key="$(prompt_public_key "$tty_path")"; then
            :
        else
            prompt_status=$?
            if (( prompt_status == 2 )); then
                die "Aucune clé SSH publique exploitable n'est disponible (source : $key_reason; destination : $destination_reason). Relancez l'étape 1 depuis un terminal interactif et collez le champ PuTTYgen « Public key for pasting into OpenSSH authorized_keys file ». Ne transmettez jamais la clé privée .ppk."
            fi
            die "La valeur saisie n'est pas une clé publique OpenSSH exploitable. Aucune clé n'a été installée. Reprenez l'étape 1 du README; ne collez jamais la clé privée .ppk."
        fi
    fi

    if (( source_status == 0 || target_status == 0 )) &&
        [[ -z "$prompted_key" && "$optional_prompt_done" != 1 ]] &&
        [[ -r "$tty_path" && -w "$tty_path" ]]; then
        if prompted_key="$(prompt_public_key "$tty_path" optional)"; then
            :
        else
            prompt_status=$?
            (( prompt_status == 1 || prompt_status == 2 )) ||
                die "La valeur saisie n'est pas une clé publique OpenSSH exploitable. Les clés existantes n'ont pas été modifiées; reprenez l'étape 1 et ne collez jamais la clé privée .ppk."
        fi
    fi

    if [[ -n "$prompted_key" ]]; then
        local prompt_scratch_directory prompt_scratch_file prompt_fingerprint
        prompt_scratch_directory="$(mktemp -d)" || die "Impossible de préparer la validation de la clé publique."
        prompt_scratch_file="$prompt_scratch_directory/public-key"
        if ! prompt_fingerprint="$(key_fingerprint_for_line "$prompted_key" "$prompt_scratch_file" 2>/dev/null)"; then prompt_fingerprint=""; fi
        rm -f -- "$prompt_scratch_file"
        rmdir -- "$prompt_scratch_directory"
        [[ -n "$prompt_fingerprint" ]] || die "La valeur fournie n'est pas une clé publique OpenSSH valide. Aucune clé privée ne doit être envoyée."
    fi

    mkdir -p -- "$key_directory"
    temporary_file="$(mktemp "$key_directory/.authorized_keys.XXXXXX")" || die "Impossible de préparer une écriture atomique de $key_file."
    if [[ -f "$key_file" ]]; then
        cat -- "$key_file" > "$temporary_file"
        while IFS= read -r line || [[ -n "$line" ]]; do
            scratch_file="${temporary_file}.fingerprint"
            if ! fingerprint="$(key_fingerprint_for_line "$line" "$scratch_file" 2>/dev/null)"; then fingerprint=""; fi
            [[ -z "$fingerprint" ]] || fingerprints["$fingerprint"]=1
        done < "$key_file"
    fi

    scratch_file="${temporary_file}.candidate"
    if (( source_status == 0 )); then
        while IFS= read -r line || [[ -n "$line" ]]; do
            if ! fingerprint="$(key_fingerprint_for_line "$line" "$scratch_file" 2>/dev/null)"; then fingerprint=""; fi
            [[ -n "$fingerprint" ]] || continue
            [[ -z "${fingerprints[$fingerprint]+present}" ]] || continue
            [[ ! -s "$temporary_file" ]] || printf '\n' >> "$temporary_file"
            printf '%s\n' "${line%$'\r'}" >> "$temporary_file"
            fingerprints["$fingerprint"]=1
            key_added=1
        done < "$source_file"
    fi
    if [[ -n "$prompted_key" ]]; then
        if ! fingerprint="$(key_fingerprint_for_line "$prompted_key" "$scratch_file" 2>/dev/null)"; then fingerprint=""; fi
        if [[ -n "$fingerprint" && -z "${fingerprints[$fingerprint]+present}" ]]; then
            [[ ! -s "$temporary_file" ]] || printf '\n' >> "$temporary_file"
            printf '%s\n' "${prompted_key%$'\r'}" >> "$temporary_file"
            fingerprints["$fingerprint"]=1
            key_added=1
        fi
    fi
    rm -f -- "$scratch_file" "$temporary_file.fingerprint"

    if [[ -f "$key_file" ]] && cmp -s -- "$temporary_file" "$key_file"; then
        rm -f -- "$temporary_file"
    else
        ssh-keygen -lf "$temporary_file" >/dev/null 2>&1 || {
            rm -f -- "$temporary_file"
            die "La préparation de $key_file ne contient aucune clé publique OpenSSH valide. Le fichier existant a été conservé."
        }
        chown "$target_user:$target_group" "$temporary_file"
        chmod 0600 "$temporary_file"
        mv -fT -- "$temporary_file" "$key_file"
    fi

    [[ -f "$key_file" && ! -L "$key_file" ]] || die "$key_file n'est pas un fichier normal après installation."
    ssh-keygen -lf "$key_file" >/dev/null 2>&1 || die "$key_file ne contient aucune clé publique OpenSSH exploitable. Reprenez l'étape 1 du README; ne transmettez jamais une clé privée."
    chown "$target_user:$target_group" "$key_directory" "$key_file"
    chmod 0700 "$key_directory"
    chmod 0600 "$key_file"
    (( key_added == 0 )) || log "Clé(s) publique(s) exploitable(s) ajoutée(s) à $key_file sans supprimer le contenu existant."
}

prepare_ssh_key() {
    local initial_home="$1" user_group actual_home actual_shell
    local key_source="$initial_home/.ssh/authorized_keys" source_status=0 prompt_status optional_prompt_done=0
    local prompted_key=""

    [[ ! -L "$HOME_DIR" ]] || die "$HOME_DIR ne doit pas être un lien symbolique."
    if [[ -e "$HOME_DIR" && ! -d "$HOME_DIR" ]]; then
        die "$HOME_DIR existe et n'est pas un répertoire. Aucune modification n'a été faite."
    fi
    if ! getent passwd "$USER_NAME" >/dev/null 2>&1; then
        [[ ! -e "$HOME_DIR" ]] || die "$HOME_DIR existe sans compte ubuntu. Déplacez ou vérifiez ce répertoire avant de relancer."
        validate_initial_public_key_source "$initial_home"
        authorized_keys_status "$key_source" || source_status=$?
        if (( source_status == 4 )); then
            die "$key_source n'est pas un fichier normal. Vérifiez-le manuellement; ne placez jamais de clé privée sur le serveur."
        fi
        if (( source_status != 0 )); then
            if prompted_key="$(prompt_public_key /dev/tty)"; then
                :
            else
                prompt_status=$?
                if (( prompt_status != 2 )); then
                    die "La valeur saisie n'est pas une clé publique OpenSSH exploitable. Aucune clé n'a été installée. Reprenez l'étape 1; ne collez jamais la clé privée .ppk."
                fi
                case "$source_status" in
                    1) die "Aucun authorized_keys n'existe dans $key_source et aucun terminal interactif n'est disponible. Préparez une clé publique dans PuTTYgen, puis relancez l'étape 1 depuis une session PuTTY et collez le champ « Public key for pasting into OpenSSH authorized_keys file ». Ne transmettez jamais le fichier privé .ppk." ;;
                    2) die "$key_source est vide et aucun terminal interactif n'est disponible. Préparez une clé publique dans PuTTYgen, puis relancez l'étape 1 depuis une session PuTTY et collez le champ « Public key for pasting into OpenSSH authorized_keys file ». Ne transmettez jamais le fichier privé .ppk." ;;
                    *) die "$key_source ne contient aucune clé publique OpenSSH valide et aucun terminal interactif n'est disponible. Suivez l'étape 1 du README et collez depuis une session PuTTY le champ « Public key for pasting into OpenSSH authorized_keys file »; ne transmettez jamais le fichier privé .ppk." ;;
                esac
            fi
        elif [[ -r /dev/tty && -w /dev/tty ]]; then
            optional_prompt_done=1
            if prompted_key="$(prompt_public_key /dev/tty optional)"; then
                :
            else
                prompt_status=$?
                if (( prompt_status != 1 && prompt_status != 2 )); then
                    die "La valeur saisie n'est pas une clé publique OpenSSH exploitable. Le compte n'a pas été créé; ne collez jamais la clé privée .ppk."
                fi
            fi
        fi
        adduser --disabled-password --gecos "" --home "$HOME_DIR" --shell "$LOGIN_SHELL" "$USER_NAME" >/dev/null
    else
        actual_home="$(getent passwd "$USER_NAME" | cut -d: -f6)"
        actual_shell="$(getent passwd "$USER_NAME" | cut -d: -f7)"
        [[ "$actual_home" == "$HOME_DIR" ]] || die "Le compte ubuntu utilise $actual_home au lieu de $HOME_DIR. Aucun changement de home n'a été fait."
        [[ "$actual_shell" == "$LOGIN_SHELL" ]] || usermod --shell "$LOGIN_SHELL" "$USER_NAME"
    fi

    user_group="$(id -gn "$USER_NAME")"
    ensure_authorized_keys "$HOME_DIR" "$USER_NAME" "$user_group" "$initial_home" /dev/tty "$prompted_key" "$optional_prompt_done"
    (( $(id -u "$USER_NAME") >= 1000 )) || die "ubuntu doit être un compte utilisateur normal avec un UID supérieur ou égal à 1000."
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
    printf '  git clone %s ~/.ubuntu-dev-environment\n' "$REPOSITORY_URL"
    printf '  cd ~/.ubuntu-dev-environment\n'
    printf 'Consultez ensuite 01-configuration-serveur/README.md.\n'
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

if [[ "${BASH_SOURCE[0]}" == "$0" ]]; then
    main "$@"
fi
