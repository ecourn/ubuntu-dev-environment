#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ADMIN_USER="${SSH_ADMIN_USER:-${SUDO_USER:-}}"
FINAL_SSH_PORT="${SSH_PORT:-}"
KEEP_OLD_PORT=0
SKIP_LOCALE=0
SKIP_HARDENING=0
DRY_RUN=0

usage() {
    cat <<'EOF'
Usage:
  configuration-serveur.sh [options]

Options:
  --user USER        Compte SSH administrateur à conserver.
  --port PORT        Port SSH final; défaut : aléatoire (49152–65535).
  --keep-old-port    Conserve les anciennes règles UFW SSH gérées.
  --skip-locale      Garde la locale et le fuseau existants.
  --skip-hardening   Ne modifie pas SSH, UFW et Fail2ban.
  --dry-run          Affiche les détections SSH sans modifier le serveur.
  -h, --help         Affiche cette aide.
EOF
}

die() { printf '[ERREUR] %s\n' "$*" >&2; exit 1; }
info() { printf '\n===== %s =====\n' "$*"; }

need_value() {
    (($# >= 2)) || die "l'option $1 attend une valeur"
    [[ -n "$2" ]] || die "l'option $1 attend une valeur non vide"
}

valid_port() {
    [[ "${1:-}" =~ ^[0-9]+$ ]] || return 1
    (( 1 <= 10#$1 && 10#$1 <= 65535 ))
}

random_ssh_port() {
    command -v shuf >/dev/null 2>&1 || die "commande shuf introuvable"
    command -v ss >/dev/null 2>&1 || die "commande ss introuvable"

    local listeners candidate attempt
    listeners="$(ss -Hlnt 2>/dev/null)" || die "impossible de détecter les ports TCP à l'écoute"
    for ((attempt = 0; attempt < 256; attempt++)); do
        candidate="$(shuf -i 49152-65535 -n 1)" || die "impossible de choisir un port SSH aléatoire"
        if ! awk -v wanted="$candidate" '
            { address=$4; sub(/^.*:/, "", address); if (address == wanted) found=1 }
            END { exit found ? 0 : 1 }
        ' <<< "$listeners"; then
            printf '%s\n' "$candidate"
            return 0
        fi
    done
    die "impossible de trouver un port TCP libre dans la plage 49152–65535"
}

while (($# > 0)); do
    case "$1" in
        --user) need_value "$@"; ADMIN_USER="$2"; shift 2 ;;
        --user=*) ADMIN_USER="${1#*=}"; shift ;;
        --port) need_value "$@"; FINAL_SSH_PORT="$2"; shift 2 ;;
        --port=*) FINAL_SSH_PORT="${1#*=}"; shift ;;
        --keep-old-port) KEEP_OLD_PORT=1; shift ;;
        --skip-locale) SKIP_LOCALE=1; shift ;;
        --skip-hardening) SKIP_HARDENING=1; shift ;;
        --dry-run) DRY_RUN=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "option inconnue : $1" ;;
    esac
done

(( EUID == 0 )) || die "exécutez ce script avec sudo, par exemple : sudo bash $0 --user ubuntu"
[[ -n "$ADMIN_USER" ]] || die "compte administrateur indéterminé; utilisez --user ubuntu"
[[ "$ADMIN_USER" != root ]] || die "root ne peut pas être le compte SSH administrateur conservé"
id "$ADMIN_USER" >/dev/null 2>&1 || die "compte inexistant : $ADMIN_USER"
[[ -x "$SCRIPT_DIR/configuration-locale.sh" ]] || die "script de locale introuvable : $SCRIPT_DIR/configuration-locale.sh"
[[ -x "$SCRIPT_DIR/durcir-ssh.sh" ]] || die "script SSH introuvable : $SCRIPT_DIR/durcir-ssh.sh"

if [[ -z "$FINAL_SSH_PORT" ]]; then FINAL_SSH_PORT="$(random_ssh_port)"; fi
valid_port "$FINAL_SSH_PORT" || die "port SSH final invalide : $FINAL_SSH_PORT"

if (( DRY_RUN == 0 && SKIP_HARDENING == 0 )); then
    [[ -r /dev/tty && -w /dev/tty ]] || die "un terminal interactif est nécessaire pour valider le nouvel accès SSH depuis le client"
fi

export SSH_ADMIN_USER="$ADMIN_USER"
export SSH_PORT="$FINAL_SSH_PORT"

if (( DRY_RUN == 0 && SKIP_HARDENING == 0 )); then
    info "PRÉCONTRÔLE — compte, ports et configuration SSH"
    preflight_args=(--user "$ADMIN_USER" --port "$FINAL_SSH_PORT" --dry-run)
    "$SCRIPT_DIR/durcir-ssh.sh" "${preflight_args[@]}"
fi

if (( SKIP_LOCALE == 0 && DRY_RUN == 0 )); then
    info "ÉTAPE 1/2 — locale française et fuseau Europe/Paris"
    "$SCRIPT_DIR/configuration-locale.sh"
fi

if (( SKIP_HARDENING == 0 )); then
    local_args=(--user "$ADMIN_USER" --port "$FINAL_SSH_PORT")
    (( KEEP_OLD_PORT == 0 )) || local_args+=(--keep-old-port)
    (( DRY_RUN == 0 )) || local_args+=(--dry-run)
    if (( DRY_RUN )); then
        info "PRÉVISUALISATION — SSH, UFW et Fail2ban (aucune modification)"
    else
        info "ÉTAPE 2/2 — SSH, UFW et Fail2ban"
    fi
    "$SCRIPT_DIR/durcir-ssh.sh" "${local_args[@]}"
fi

if (( DRY_RUN )); then
    info "PRÉVISUALISATION TERMINÉE — aucune modification effectuée"
else
    info "CONFIGURATION DU SERVEUR TERMINÉE"
    printf 'Compte SSH : %s\n' "$ADMIN_USER"
    printf 'Port SSH : %s\n' "$FINAL_SSH_PORT"
    if (( SKIP_HARDENING == 0 )); then
        printf 'Conservez ce port. Gardez la session actuelle ouverte.\n'
        printf 'Depuis votre poste client, ouvrez une nouvelle connexion avec le compte %s, votre clé SSH et le port %s.\n' "$ADMIN_USER" "$FINAL_SSH_PORT"
        printf "Vérifiez cette nouvelle connexion avant de fermer l'ancienne session.\n"
    fi
fi
