# Script serveur complet 3-en-1, version corrigée

Cette version corrige un défaut important de la première fusion : les scripts embarqués ne sont plus exécutés via `bash -s` avec un heredoc.

Cette ancienne méthode détournait l'entrée standard. Lorsqu'une clé privée était protégée par une passphrase, `ssh-add` ne pouvait plus la demander dans le terminal et tentait d'utiliser `ssh-askpass`, ce qui provoquait notamment :

```text
ssh_askpass: exec(/usr/bin/ssh-askpass): No such file or directory
Erreur : impossible de charger la clé privée
```

La version corrigée écrit chaque étape dans un fichier temporaire protégé, puis l'exécute normalement. `ssh-add` conserve ainsi l'accès au terminal et peut demander la passphrase.

## Reprendre après l'échec observé

La première étape a déjà réussi et les fichiers `cle` et `priv` ont été conservés.

Avec cette version corrigée, vous pouvez donc reprendre directement à l'étape 2 :

```bash
ADMIN_USER="$(id -un)"
sudo env SSH_CONNECTION="$SSH_CONNECTION" \
  ./serveur-bootstrap-complet-corrige.sh \
  --user "$ADMIN_USER" \
  --skip-locale
```

Si la clé privée est protégée par une passphrase, `ssh-add` doit maintenant afficher une invite dans le terminal.

## Création du fichier avec Vim

```bash
SCRIPT="serveur-bootstrap-complet.sh"; if [[ -e "$SCRIPT" ]]; then printf 'Erreur : %s existe déjà.\n' "$SCRIPT" >&2; else install -m 700 /dev/null "$SCRIPT" && vim "$SCRIPT"; fi
```

Puis :

```bash
bash -n "$SCRIPT" && printf '%s\n' 'Syntaxe Bash OK'
```

## Exécution complète ultérieure

```bash
ADMIN_USER="$(id -un)"
sudo env SSH_CONNECTION="$SSH_CONNECTION" \
  ./serveur-bootstrap-complet.sh \
  --user "$ADMIN_USER"
```

## Script complet corrigé

```bash
#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'

PROGRAM="${0##*/}"

FINAL_SSH_PORT="${SSH_PORT:-62238}"
ADMIN_USER="${SSH_ADMIN_USER:-${SSH_TARGET_USER:-${SUDO_USER:-}}}"
TARGET_HOST="${SSH_TARGET_HOST:-}"
CURRENT_SSH_PORT="${SSH_TARGET_PORT:-}"
PUBLIC_KEY="${SSH_PUBLIC_KEY:-./cle}"
PRIVATE_KEY="${SSH_PRIVATE_KEY:-./priv}"
HOST_KEY_FINGERPRINT="${SSH_HOST_KEY_FINGERPRINT:-}"

KEEP_KEYS=0
KEEP_OLD_PORT=0
SKIP_LOCALE=0
SKIP_KEY=0
SKIP_HARDENING=0

TMP_STAGE_DIR=""

usage() {
    cat <<'EOF'
Usage:
  serveur-bootstrap-complet.sh [options]

Options:
  --user USER                 Compte administrateur SSH à conserver.
  --host HOST                 Hôte utilisé pour le test de la clé SSH.
  --current-port PORT         Port SSH actuel utilisé pour le test de clé.
  --port PORT                 Port SSH final, défaut: 62238.
  --public-key FILE           Clé publique temporaire, défaut: ./cle.
  --private-key FILE          Clé privée temporaire, défaut: ./priv.
  --host-key-fingerprint FP   Empreinte SHA256 attendue de la clé d'hôte.
  --keep-keys                 Conserve les fichiers de clés après validation.
  --keep-old-port             Conserve les anciennes règles UFW SSH gérées.
  --skip-locale               Ignore l'étape français / Europe/Paris.
  --skip-key                  Ignore l'installation/test de la clé SSH.
  --skip-hardening            Ignore le durcissement SSH/UFW/Fail2Ban.
  -h, --help                  Affiche cette aide.
EOF
}

die() {
    printf 'ERREUR: %s\n' "$*" >&2
    exit 1
}

info() {
    printf '\n===== %s =====\n' "$*"
}

need_value() {
    (($# >= 2)) || die "l'option $1 attend une valeur"
    [[ -n "$2" ]] || die "l'option $1 attend une valeur non vide"
}

valid_port() {
    [[ "${1:-}" =~ ^[0-9]+$ ]] || return 1
    (( 1 <= 10#$1 && 10#$1 <= 65535 ))
}

cleanup_launcher() {
    local rc=$?
    trap - EXIT
    if [[ -n "$TMP_STAGE_DIR" && -d "$TMP_STAGE_DIR" ]]; then
        rm -rf -- "$TMP_STAGE_DIR" || true
    fi
    exit "$rc"
}
trap cleanup_launcher EXIT

while (($# > 0)); do
    case "$1" in
        --user) need_value "$@"; ADMIN_USER="$2"; shift 2 ;;
        --user=*) ADMIN_USER="${1#*=}"; shift ;;
        --host) need_value "$@"; TARGET_HOST="$2"; shift 2 ;;
        --host=*) TARGET_HOST="${1#*=}"; shift ;;
        --current-port) need_value "$@"; CURRENT_SSH_PORT="$2"; shift 2 ;;
        --current-port=*) CURRENT_SSH_PORT="${1#*=}"; shift ;;
        --port) need_value "$@"; FINAL_SSH_PORT="$2"; shift 2 ;;
        --port=*) FINAL_SSH_PORT="${1#*=}"; shift ;;
        --public-key) need_value "$@"; PUBLIC_KEY="$2"; shift 2 ;;
        --public-key=*) PUBLIC_KEY="${1#*=}"; shift ;;
        --private-key) need_value "$@"; PRIVATE_KEY="$2"; shift 2 ;;
        --private-key=*) PRIVATE_KEY="${1#*=}"; shift ;;
        --host-key-fingerprint) need_value "$@"; HOST_KEY_FINGERPRINT="$2"; shift 2 ;;
        --host-key-fingerprint=*) HOST_KEY_FINGERPRINT="${1#*=}"; shift ;;
        --keep-keys) KEEP_KEYS=1; shift ;;
        --keep-old-port) KEEP_OLD_PORT=1; shift ;;
        --skip-locale) SKIP_LOCALE=1; shift ;;
        --skip-key) SKIP_KEY=1; shift ;;
        --skip-hardening) SKIP_HARDENING=1; shift ;;
        -h|--help) usage; exit 0 ;;
        *) die "option inconnue: $1" ;;
    esac
done

valid_port "$FINAL_SSH_PORT" || die "port SSH final invalide: $FINAL_SSH_PORT"
if [[ -n "$CURRENT_SSH_PORT" ]]; then
    valid_port "$CURRENT_SSH_PORT" || die "port SSH actuel invalide: $CURRENT_SSH_PORT"
fi

(( EUID == 0 )) || die "ce script doit être exécuté avec sudo ou directement par root"

[[ -n "$ADMIN_USER" ]] || die "compte administrateur indéterminé. Utilisez --user NOM."
[[ "$ADMIN_USER" != root ]] || die "root ne peut pas être le compte SSH administrateur conservé"
id "$ADMIN_USER" >/dev/null 2>&1 || die "compte inexistant: $ADMIN_USER"

# Précontrôles avant toute modification.
if (( SKIP_KEY == 0 )); then
    [[ -f "$PUBLIC_KEY" ]] || die "clé publique introuvable: $PUBLIC_KEY"
    [[ -f "$PRIVATE_KEY" ]] || die "clé privée introuvable: $PRIVATE_KEY"
    [[ ! -L "$PUBLIC_KEY" ]] || die "la clé publique ne doit pas être un lien symbolique"
    [[ ! -L "$PRIVATE_KEY" ]] || die "la clé privée ne doit pas être un lien symbolique"

    if [[ -z "$TARGET_HOST" && -z "${SSH_CONNECTION:-}" ]]; then
        die "hôte de test SSH indéterminé. Utilisez --host HOTE ou transmettez SSH_CONNECTION à sudo."
    fi
fi

export SSH_ADMIN_USER="$ADMIN_USER"
export SSH_TARGET_USER="$ADMIN_USER"
export SSH_PORT="$FINAL_SSH_PORT"
export SSH_PUBLIC_KEY="$PUBLIC_KEY"
export SSH_PRIVATE_KEY="$PRIVATE_KEY"

[[ -z "$TARGET_HOST" ]] || export SSH_TARGET_HOST="$TARGET_HOST"
[[ -z "$CURRENT_SSH_PORT" ]] || export SSH_TARGET_PORT="$CURRENT_SSH_PORT"
[[ -z "$HOST_KEY_FINGERPRINT" ]] || export SSH_HOST_KEY_FINGERPRINT="$HOST_KEY_FINGERPRINT"

# Les scripts embarqués sont matérialisés dans des fichiers temporaires.
# Ils ne sont PAS exécutés via "bash -s <<HEREDOC", car cette technique
# détourne stdin et empêche ssh-add de demander une passphrase dans le terminal.
TMP_STAGE_DIR="$(mktemp -d /tmp/serveur-bootstrap.XXXXXX)"
chmod 700 "$TMP_STAGE_DIR"

run_locale() {
    local stage_script="$TMP_STAGE_DIR/01-locale.sh"
    cat >"$stage_script" <<'__LOCALE_SCRIPT__'
#!/usr/bin/env bash
set -Eeuo pipefail

LANGUAGE_CODE="${LANGUAGE_CODE:-fr}"
COUNTRY_CODE="${COUNTRY_CODE:-FR}"
CHARSET="${CHARSET:-UTF-8}"
TIMEZONE_REGION="${TIMEZONE_REGION:-Europe}"
TIMEZONE_CITY="${TIMEZONE_CITY:-Paris}"

TARGET_LOCALE="${TARGET_LOCALE:-${LANGUAGE_CODE}_${COUNTRY_CODE}.${CHARSET}}"
TARGET_TIMEZONE="${TARGET_TIMEZONE:-${TIMEZONE_REGION}/${TIMEZONE_CITY}}"
LANGUAGE_PACK="${LANGUAGE_PACK:-language-pack-${LANGUAGE_CODE}}"
NTP_WAIT_SECONDS="${NTP_WAIT_SECONDS:-30}"
NTP_POLL_SECONDS="${NTP_POLL_SECONDS:-1}"

[[ "$NTP_WAIT_SECONDS" =~ ^[0-9]+$ ]] || {
  printf '%s\n' 'Erreur : NTP_WAIT_SECONDS doit être un entier positif ou nul.' >&2
  exit 1
}

[[ "$NTP_POLL_SECONDS" =~ ^[1-9][0-9]*$ ]] || {
  printf '%s\n' 'Erreur : NTP_POLL_SECONDS doit être un entier strictement positif.' >&2
  exit 1
}

if [ "${EUID:-$(id -u)}" -eq 0 ]; then
  SUDO=()
else
  command -v sudo >/dev/null 2>&1 || {
    printf '%s\n' 'Erreur : sudo est requis pour ce compte.' >&2
    exit 1
  }
  SUDO=(sudo)
fi

fail() {
  printf 'Erreur : %s\n' "$*" >&2
  exit 1
}

trap 'printf "Erreur à la ligne %s.\n" "$LINENO" >&2' ERR

command -v apt-get >/dev/null 2>&1 || fail 'apt-get est introuvable.'
command -v locale >/dev/null 2>&1 || fail 'locale est introuvable.'
command -v timedatectl >/dev/null 2>&1 || fail 'timedatectl est introuvable.'

if [ -r /etc/os-release ]; then
  . /etc/os-release
  [ "${ID:-}" = 'ubuntu' ] || fail "ce script cible Ubuntu, système détecté : ${ID:-inconnu}."
fi

printf '%s\n' 'Installation des composants nécessaires...'
"${SUDO[@]}" apt-get update
"${SUDO[@]}" env DEBIAN_FRONTEND=noninteractive \
  apt-get install -y locales "$LANGUAGE_PACK"

command -v locale-gen >/dev/null 2>&1 || fail 'locale-gen est introuvable après installation.'
command -v localectl >/dev/null 2>&1 || fail 'localectl est introuvable après installation.'

printf 'Génération de la locale %s...\n' "$TARGET_LOCALE"
"${SUDO[@]}" locale-gen "$TARGET_LOCALE"

LOCALE_PREFIX="${TARGET_LOCALE%%.*}"
locale -a | grep -i -E "^${LOCALE_PREFIX}\.utf-?8$" >/dev/null || \
  fail "la locale $TARGET_LOCALE n'a pas été générée."

printf 'Configuration de la locale système : %s\n' "$TARGET_LOCALE"
"${SUDO[@]}" localectl set-locale "LANG=$TARGET_LOCALE"

SYSTEM_LOCALE_STATUS="$(localectl status --no-pager)"
CURRENT_SYSTEM_LANG="$(
  printf '%s\n' "$SYSTEM_LOCALE_STATUS" |
    grep -oE 'LANG=[^[:space:]]+' |
    head -n 1 |
    cut -d= -f2- || true
)"
[ "$CURRENT_SYSTEM_LANG" = "$TARGET_LOCALE" ] || \
  fail "locale système : ${CURRENT_SYSTEM_LANG:-indéterminée}, attendue : $TARGET_LOCALE."

printf 'Configuration du fuseau horaire : %s\n' "$TARGET_TIMEZONE"
timedatectl list-timezones | grep -Fx "$TARGET_TIMEZONE" >/dev/null || \
  fail "fuseau horaire inconnu : $TARGET_TIMEZONE."
"${SUDO[@]}" timedatectl set-timezone "$TARGET_TIMEZONE"

CURRENT_TIMEZONE="$(timedatectl show -p Timezone --value)"
[ "$CURRENT_TIMEZONE" = "$TARGET_TIMEZONE" ] || \
  fail "fuseau configuré : $CURRENT_TIMEZONE, attendu : $TARGET_TIMEZONE."

printf '%s\n' 'Activation de la synchronisation NTP...'
"${SUDO[@]}" timedatectl set-ntp true

NTP_ENABLED="$(timedatectl show -p NTP --value 2>/dev/null || true)"
if [ "$NTP_ENABLED" != 'yes' ]; then
  fail 'timedatectl ne signale pas NTP comme activé.'
fi

START_SECONDS="$(date +%s)"
while :; do
  NTP_SYNCED="$(timedatectl show -p NTPSynchronized --value 2>/dev/null || true)"

  if [ "$NTP_SYNCED" = 'yes' ]; then
    break
  fi

  NOW_SECONDS="$(date +%s)"
  ELAPSED_SECONDS="$((NOW_SECONDS - START_SECONDS))"

  if [ "$ELAPSED_SECONDS" -ge "$NTP_WAIT_SECONDS" ]; then
    printf '%s\n' \
      "Avertissement : NTP est activé, mais la synchronisation n'est pas encore confirmée." >&2
    break
  fi

  sleep "$NTP_POLL_SECONDS"
done

printf '%s\n' '--- Vérification finale ---'
printf 'Locale cible : %s\n' "$TARGET_LOCALE"
printf 'Locale système : %s\n' "$CURRENT_SYSTEM_LANG"
printf 'Fuseau : %s\n' "$(timedatectl show -p Timezone --value)"
printf 'NTP activé : %s\n' "$(timedatectl show -p NTP --value 2>/dev/null || printf 'indisponible')"
printf 'NTP synchronisé : %s\n' "$(timedatectl show -p NTPSynchronized --value 2>/dev/null || printf 'indisponible')"
printf 'Date locale : %s\n' "$(LANG="$TARGET_LOCALE" LC_ALL="$TARGET_LOCALE" date '+%A %d %B %Y %H:%M:%S %Z %z')"
printf '%s\n' 'CONFIGURATION_OK'
__LOCALE_SCRIPT__
    chmod 700 "$stage_script"

    info "ÉTAPE 1/3 - Français, locale et fuseau Europe/Paris"
    bash "$stage_script"

}

run_key() {
    local stage_script="$TMP_STAGE_DIR/02-cle-ssh.sh"
    cat >"$stage_script" <<'__KEY_SCRIPT__'
#!/usr/bin/env bash
set -Eeuo pipefail
umask 077

PROGRAM="${0##*/}"
DELETE_AFTER_SUCCESS=1
OPEN_SHELL=0
INSTALL_KEY=1
TARGET_HOST="${SSH_TARGET_HOST:-}"
TARGET_PORT="${SSH_TARGET_PORT:-}"
TARGET_USER="${SSH_TARGET_USER:-$(id -un)}"
PUBLIC_KEY_INPUT="${SSH_PUBLIC_KEY:-./cle}"
PRIVATE_KEY_INPUT="${SSH_PRIVATE_KEY:-./priv}"
AUTHORIZED_KEYS="${SSH_AUTHORIZED_KEYS:-}"
KNOWN_HOSTS="${SSH_KNOWN_HOSTS:-}"
HOST_KEY_FINGERPRINT="${SSH_HOST_KEY_FINGERPRINT:-}"
CONNECT_TIMEOUT="${SSH_CONNECT_TIMEOUT:-10}"
SCAN_TIMEOUT="${SSH_SCAN_TIMEOUT:-5}"
STARTED_AGENT_PID=""
STARTED_AGENT_SOCK=""
AUTHENTICATION_SUCCEEDED=0

usage() {
  cat <<USAGE
Usage:
  $PROGRAM [options]

Options:
  -H, --host HOST                  Hôte cible.
                                   Si absent, utilise l’adresse serveur de SSH_CONNECTION.
  -u, --user USER                  Compte cible, défaut : compte courant.
  -P, --port PORT                  Port SSH, défaut : port serveur de SSH_CONNECTION ou 22.
  -k, --public-key FILE            Clé publique, défaut : ./cle.
  -i, --private-key FILE           Clé privée, défaut : ./priv.
  -a, --authorized-keys FILE       Fichier authorized_keys cible.
  -K, --known-hosts FILE           Fichier known_hosts utilisé par le test SSH.
      --host-key-fingerprint FP    Empreinte SHA256 attendue de la clé d’hôte.
      --shell                      Ouvre un shell SSH après l’installation.
      --no-install                 N’installe pas la clé publique.
      --keep                       Ne supprime pas les fichiers de clés après succès.
  -h, --help                       Affiche cette aide.

Variables équivalentes :
  SSH_TARGET_HOST, SSH_TARGET_USER, SSH_TARGET_PORT,
  SSH_PUBLIC_KEY, SSH_PRIVATE_KEY, SSH_AUTHORIZED_KEYS,
  SSH_KNOWN_HOSTS, SSH_HOST_KEY_FINGERPRINT,
  SSH_CONNECT_TIMEOUT, SSH_SCAN_TIMEOUT.
USAGE
}

die() {
  printf 'Erreur : %s\n' "$*" >&2
  exit 1
}

info() {
  printf '%s\n' "$*"
}

need_arg() {
  (($# >= 2)) || die "l’option $1 attend une valeur"
  [[ -n "$2" ]] || die "l’option $1 attend une valeur non vide"
  printf '%s' "$2"
}

while (($# > 0)); do
  case "$1" in
    -H|--host)
      TARGET_HOST="$(need_arg "$@")"
      shift 2
      ;;
    -u|--user)
      TARGET_USER="$(need_arg "$@")"
      shift 2
      ;;
    -P|--port)
      TARGET_PORT="$(need_arg "$@")"
      shift 2
      ;;
    -k|--public-key)
      PUBLIC_KEY_INPUT="$(need_arg "$@")"
      shift 2
      ;;
    -i|--private-key)
      PRIVATE_KEY_INPUT="$(need_arg "$@")"
      shift 2
      ;;
    -a|--authorized-keys)
      AUTHORIZED_KEYS="$(need_arg "$@")"
      shift 2
      ;;
    -K|--known-hosts)
      KNOWN_HOSTS="$(need_arg "$@")"
      shift 2
      ;;
    --host-key-fingerprint)
      HOST_KEY_FINGERPRINT="$(need_arg "$@")"
      shift 2
      ;;
    --shell)
      OPEN_SHELL=1
      shift
      ;;
    --no-install)
      INSTALL_KEY=0
      shift
      ;;
    --keep)
      DELETE_AFTER_SUCCESS=0
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    --)
      shift
      (($# == 0)) || die "arguments positionnels inattendus : $*"
      ;;
    *)
      die "option inconnue : $1"
      ;;
  esac
done

if [[ -z "$TARGET_HOST" && -n "${SSH_CONNECTION:-}" ]]; then
  # SSH_CONNECTION = IP_client port_client IP_serveur port_serveur
  read -r _client_ip _client_port connection_server_ip connection_server_port _extra <<<"$SSH_CONNECTION"
  [[ -n "${connection_server_ip:-}" && -n "${connection_server_port:-}" ]] \
    || die "SSH_CONNECTION a un format inattendu"
  TARGET_HOST="$connection_server_ip"
  TARGET_PORT="${TARGET_PORT:-$connection_server_port}"
fi

TARGET_PORT="${TARGET_PORT:-22}"

[[ -n "$TARGET_HOST" ]] || die "aucun hôte : utiliser --host ou SSH_TARGET_HOST"
[[ "$TARGET_HOST" != -* ]] || die "hôte invalide : $TARGET_HOST"
[[ "$TARGET_HOST" != *[$'\r\n\t ']* ]] || die "l’hôte ne doit pas contenir d’espace ni de caractère de contrôle"
[[ -n "$TARGET_USER" ]] || die "compte cible vide"
[[ "$TARGET_PORT" =~ ^[0-9]+$ ]] || die "port invalide : $TARGET_PORT"
((TARGET_PORT >= 1 && TARGET_PORT <= 65535)) || die "port hors limites : $TARGET_PORT"
[[ "$CONNECT_TIMEOUT" =~ ^[0-9]+$ ]] || die "SSH_CONNECT_TIMEOUT invalide"
[[ "$SCAN_TIMEOUT" =~ ^[0-9]+$ ]] || die "SSH_SCAN_TIMEOUT invalide"
((CONNECT_TIMEOUT >= 1)) || die "SSH_CONNECT_TIMEOUT doit être supérieur ou égal à 1"
((SCAN_TIMEOUT >= 1)) || die "SSH_SCAN_TIMEOUT doit être supérieur ou égal à 1"

for required_command in \
  ssh ssh-add ssh-agent ssh-keygen ssh-keyscan getent realpath install \
  id chmod chown dirname mktemp rm grep awk touch tail; do
  command -v "$required_command" >/dev/null 2>&1 \
    || die "$required_command est introuvable"
done

lookup_home() {
  local entry home
  entry="$(getent passwd -- "$1" 2>/dev/null || true)"
  [[ -n "$entry" ]] || return 1
  IFS=: read -r _ _ _ _ _ home _ <<<"$entry"
  [[ -n "$home" ]] || return 1
  printf '%s\n' "$home"
}

TARGET_HOME="$(lookup_home "$TARGET_USER")" || die "compte local introuvable : $TARGET_USER"
[[ -d "$TARGET_HOME" ]] || die "répertoire personnel introuvable : $TARGET_HOME"

CURRENT_USER="$(id -un)"
if [[ "$CURRENT_USER" != "$TARGET_USER" && "$EUID" -ne 0 ]]; then
  die "le script doit être exécuté par $TARGET_USER ou par root"
fi

[[ -f "$PUBLIC_KEY_INPUT" ]] || die "clé publique introuvable : $PUBLIC_KEY_INPUT"
[[ -f "$PRIVATE_KEY_INPUT" ]] || die "clé privée introuvable : $PRIVATE_KEY_INPUT"
[[ ! -L "$PUBLIC_KEY_INPUT" ]] || die "la clé publique ne doit pas être un lien symbolique"
[[ ! -L "$PRIVATE_KEY_INPUT" ]] || die "la clé privée ne doit pas être un lien symbolique"

PUBLIC_KEY="$(realpath -- "$PUBLIC_KEY_INPUT")"
PRIVATE_KEY="$(realpath -- "$PRIVATE_KEY_INPUT")"
[[ "$PUBLIC_KEY" != "$PRIVATE_KEY" ]] \
  || die "la clé publique et la clé privée désignent le même fichier"

# La clé privée ne doit pas être accessible au groupe ni aux autres utilisateurs.
chmod 600 -- "$PRIVATE_KEY" || die "impossible de sécuriser les permissions de la clé privée"

public_contents="$(<"$PUBLIC_KEY")"
[[ -n "$public_contents" ]] || die "clé publique vide"
[[ "$public_contents" != *$'\n'* ]] || die "le fichier public doit contenir une seule ligne"

read -r public_type public_blob public_comment <<<"$public_contents"
[[ "$public_type" =~ ^(ssh|ecdsa|sk)- ]] \
  || die "le fichier public ne ressemble pas à une clé SSH publique"
[[ -n "$public_blob" ]] || die "clé publique invalide"
ssh-keygen -lf "$PUBLIC_KEY" >/dev/null 2>&1 || die "clé publique invalide"

SSH_DIR="$TARGET_HOME/.ssh"
DEFAULT_AUTHORIZED_KEYS="$SSH_DIR/authorized_keys"
DEFAULT_KNOWN_HOSTS="$SSH_DIR/known_hosts"
AUTHORIZED_KEYS="${AUTHORIZED_KEYS:-$DEFAULT_AUTHORIZED_KEYS}"
KNOWN_HOSTS="${KNOWN_HOSTS:-$DEFAULT_KNOWN_HOSTS}"

AUTHORIZED_KEYS_REAL="$(realpath -m -- "$AUTHORIZED_KEYS")"
KNOWN_HOSTS_REAL="$(realpath -m -- "$KNOWN_HOSTS")"

for protected_path in "$AUTHORIZED_KEYS_REAL" "$KNOWN_HOSTS_REAL"; do
  [[ "$protected_path" != "$PUBLIC_KEY" ]] \
    || die "un fichier SSH persistant ne doit pas désigner la clé publique temporaire"
  [[ "$protected_path" != "$PRIVATE_KEY" ]] \
    || die "un fichier SSH persistant ne doit pas désigner la clé privée temporaire"
done

[[ "$AUTHORIZED_KEYS_REAL" != "$KNOWN_HOSTS_REAL" ]] \
  || die "authorized_keys et known_hosts doivent être deux fichiers différents"

cleanup() {
  local rc=$?

  # Empêche le trap EXIT de se réexécuter via l'exit final.
  trap - EXIT

  if [[ -n "$STARTED_AGENT_PID" ]]; then
    SSH_AGENT_PID="$STARTED_AGENT_PID" \
    SSH_AUTH_SOCK="$STARTED_AGENT_SOCK" \
      ssh-agent -k >/dev/null 2>&1 || true
  fi

  if ((rc == 0 && AUTHENTICATION_SUCCEEDED == 1 && DELETE_AFTER_SUCCESS == 1)); then
    if ! rm -f -- "$PUBLIC_KEY" "$PRIVATE_KEY"; then
      printf 'Erreur : impossible de supprimer les fichiers temporaires\n' >&2
      rc=1
    elif [[ -e "$PUBLIC_KEY" || -e "$PRIVATE_KEY" ]]; then
      printf 'Erreur : vérification de suppression échouée\n' >&2
      rc=1
    else
      info "Clés temporaires supprimées après authentification réussie."
    fi
  elif ((rc == 0 && AUTHENTICATION_SUCCEEDED == 1)); then
    info "Authentification réussie ; fichiers conservés (--keep)."
  elif ((rc != 0)); then
    info "Échec : les fichiers de clés sont conservés pour diagnostic."
  fi

  exit "$rc"
}
trap cleanup EXIT

# Démarre un agent isolé afin d'éviter qu'une clé d'un agent préexistant ne valide le test.
agent_environment="$(ssh-agent -s)" || die "impossible de démarrer ssh-agent"
eval "$agent_environment" >/dev/null 2>&1
unset agent_environment

STARTED_AGENT_PID="${SSH_AGENT_PID:-}"
STARTED_AGENT_SOCK="${SSH_AUTH_SOCK:-}"
[[ -n "$STARTED_AGENT_PID" && -n "$STARTED_AGENT_SOCK" ]] \
  || die "ssh-agent n’a pas fourni un environnement valide"

if ! ssh-add "$PRIVATE_KEY"; then
  die "impossible de charger la clé privée ; vérifiez le fichier et sa passphrase"
fi

# L'agent isolé doit contenir exactement une identité, celle qui vient d'être chargée.
agent_key_count="$(ssh-add -L 2>/dev/null | awk 'NF {count++} END {print count+0}')"
[[ "$agent_key_count" == "1" ]] \
  || die "l’agent SSH temporaire contient un nombre inattendu d’identités : $agent_key_count"

public_fingerprint="$(ssh-keygen -lf "$PUBLIC_KEY" 2>/dev/null | awk 'NR==1 {print $2}')"
agent_fingerprint="$(ssh-add -L 2>/dev/null | ssh-keygen -lf - 2>/dev/null | awk 'NR==1 {print $2}')"
[[ -n "$public_fingerprint" && -n "$agent_fingerprint" ]] \
  || die "impossible de calculer les empreintes de la paire de clés"
[[ "$public_fingerprint" == "$agent_fingerprint" ]] \
  || die "la clé publique et la clé privée ne correspondent pas"
info "Paire de clés vérifiée : $public_fingerprint"

ensure_target_ssh_dir() {
  if [[ ! -d "$SSH_DIR" ]]; then
    install -d -m 700 -- "$SSH_DIR" || die "impossible de créer $SSH_DIR"
  else
    chmod 700 -- "$SSH_DIR" || die "impossible de sécuriser $SSH_DIR"
  fi

  if ((EUID == 0)); then
    target_group="$(id -gn -- "$TARGET_USER")" || die "groupe introuvable pour $TARGET_USER"
    chown "$TARGET_USER:$target_group" -- "$SSH_DIR" \
      || die "impossible d’attribuer $SSH_DIR à $TARGET_USER"
  fi
}

if ((INSTALL_KEY == 1)); then
  ensure_target_ssh_dir

  auth_parent="$(dirname -- "$AUTHORIZED_KEYS")"
  if [[ ! -d "$auth_parent" ]]; then
    install -d -m 700 -- "$auth_parent" || die "impossible de créer $auth_parent"
  fi

  if [[ ! -e "$AUTHORIZED_KEYS" ]]; then
    : >"$AUTHORIZED_KEYS" || die "impossible de créer $AUTHORIZED_KEYS"
  fi
  [[ -f "$AUTHORIZED_KEYS" ]] || die "$AUTHORIZED_KEYS n’est pas un fichier régulier"
  [[ ! -L "$AUTHORIZED_KEYS" ]] || die "$AUTHORIZED_KEYS ne doit pas être un lien symbolique"
  chmod 600 -- "$AUTHORIZED_KEYS" || die "impossible de sécuriser $AUTHORIZED_KEYS"

  if ((EUID == 0)); then
    target_group="$(id -gn -- "$TARGET_USER")" || die "groupe introuvable pour $TARGET_USER"
    chown "$TARGET_USER:$target_group" -- "$AUTHORIZED_KEYS" \
      || die "impossible d’attribuer $AUTHORIZED_KEYS à $TARGET_USER"
  fi

  key_present=0
  if [[ -s "$AUTHORIZED_KEYS" ]]; then
    if ssh-keygen -lf "$AUTHORIZED_KEYS" 2>/dev/null \
      | awk -v expected="$public_fingerprint" '$2 == expected {found=1} END {exit !found}'; then
      key_present=1
    fi
  fi

  if ((key_present == 0)); then
    # Ajoute une séparation uniquement si le fichier existant ne se termine pas déjà par LF.
    if [[ -s "$AUTHORIZED_KEYS" ]]; then
      last_byte="$(tail -c 1 -- "$AUTHORIZED_KEYS" 2>/dev/null || true)"
      [[ -z "$last_byte" ]] || printf '\n' >>"$AUTHORIZED_KEYS"
    fi
    printf '%s\n' "$public_contents" >>"$AUTHORIZED_KEYS" \
      || die "impossible d’ajouter la clé à $AUTHORIZED_KEYS"
    info "Clé publique ajoutée à $AUTHORIZED_KEYS."
  else
    info "Clé publique déjà présente dans $AUTHORIZED_KEYS."
  fi

  chmod 600 -- "$AUTHORIZED_KEYS" || die "impossible de sécuriser $AUTHORIZED_KEYS"
  if ((EUID == 0)); then
    chown "$TARGET_USER:$target_group" -- "$AUTHORIZED_KEYS" \
      || die "impossible de conserver le propriétaire de $AUTHORIZED_KEYS"
  fi
fi

if [[ "$KNOWN_HOSTS_REAL" == "$DEFAULT_KNOWN_HOSTS" ]]; then
  ensure_target_ssh_dir
fi
known_parent="$(dirname -- "$KNOWN_HOSTS")"
if [[ ! -d "$known_parent" ]]; then
  install -d -m 700 -- "$known_parent" || die "impossible de créer $known_parent"
fi

if [[ -e "$KNOWN_HOSTS" ]]; then
  [[ -f "$KNOWN_HOSTS" ]] || die "$KNOWN_HOSTS n’est pas un fichier régulier"
  [[ ! -L "$KNOWN_HOSTS" ]] || die "$KNOWN_HOSTS ne doit pas être un lien symbolique"
  chmod 600 -- "$KNOWN_HOSTS" || die "impossible de sécuriser $KNOWN_HOSTS"
  if ((EUID == 0)); then
    target_group="$(id -gn -- "$TARGET_USER")" || die "groupe introuvable pour $TARGET_USER"
    chown "$TARGET_USER:$target_group" -- "$KNOWN_HOSTS" \
      || die "impossible d’attribuer $KNOWN_HOSTS à $TARGET_USER"
  fi
fi

if [[ -n "$HOST_KEY_FINGERPRINT" ]]; then
  [[ "$HOST_KEY_FINGERPRINT" == SHA256:* ]] \
    || die "l’empreinte d’hôte doit être au format SHA256:..."

  scanned_host_keys="$(ssh-keyscan -T "$SCAN_TIMEOUT" -p "$TARGET_PORT" \
    -t ed25519,ecdsa,rsa "$TARGET_HOST" 2>/dev/null || true)"
  [[ -n "$scanned_host_keys" ]] || die "aucune clé d’hôte reçue de $TARGET_HOST:$TARGET_PORT"

  matched_host_keys=""
  while IFS= read -r scanned_line; do
    [[ -n "$scanned_line" ]] || continue
    scanned_fp="$(printf '%s\n' "$scanned_line" | ssh-keygen -lf - 2>/dev/null | awk 'NR==1 {print $2}')"
    if [[ "$scanned_fp" == "$HOST_KEY_FINGERPRINT" ]]; then
      matched_host_keys+="$scanned_line"$'\n'
    fi
  done <<<"$scanned_host_keys"

  [[ -n "$matched_host_keys" ]] \
    || die "aucune clé d’hôte reçue ne correspond à l’empreinte attendue $HOST_KEY_FINGERPRINT"

  # N'écrase jamais le known_hosts existant. Ajoute uniquement la ou les clés vérifiées absentes.
  touch -- "$KNOWN_HOSTS" || die "impossible de créer $KNOWN_HOSTS"
  chmod 600 -- "$KNOWN_HOSTS" || die "impossible de sécuriser $KNOWN_HOSTS"

  while IFS= read -r matched_line; do
    [[ -n "$matched_line" ]] || continue
    if ! grep -Fqx -- "$matched_line" "$KNOWN_HOSTS"; then
      printf '%s\n' "$matched_line" >>"$KNOWN_HOSTS" \
        || die "impossible d’ajouter la clé d’hôte vérifiée à $KNOWN_HOSTS"
    fi
  done <<<"$matched_host_keys"

  if ((EUID == 0)); then
    chown "$TARGET_USER:$target_group" -- "$KNOWN_HOSTS" \
      || die "impossible de conserver le propriétaire de $KNOWN_HOSTS"
  fi

  HOST_KEY_POLICY=yes
elif [[ -s "$KNOWN_HOSTS" ]]; then
  HOST_KEY_POLICY=yes
else
  # TOFU : accepte uniquement une clé encore inconnue, mais refusera ensuite une clé modifiée.
  HOST_KEY_POLICY=accept-new
fi

SSH_OPTIONS=(
  -p "$TARGET_PORT"
  -o "BatchMode=yes"
  -o "PreferredAuthentications=publickey"
  -o "PasswordAuthentication=no"
  -o "KbdInteractiveAuthentication=no"
  -o "PubkeyAuthentication=yes"
  -o "IdentitiesOnly=yes"
  -o "IdentityAgent=$STARTED_AGENT_SOCK"
  -o "ConnectTimeout=$CONNECT_TIMEOUT"
  -o "StrictHostKeyChecking=$HOST_KEY_POLICY"
  -o "UserKnownHostsFile=$KNOWN_HOSTS"
  -i "$PRIVATE_KEY"
)
DESTINATION="$TARGET_USER@$TARGET_HOST"

if ((OPEN_SHELL == 1)); then
  info "Ouverture de la session SSH $DESTINATION:$TARGET_PORT"
  ssh "${SSH_OPTIONS[@]}" "$DESTINATION"
else
  info "Vérification de l’authentification SSH vers $DESTINATION:$TARGET_PORT"
  ssh "${SSH_OPTIONS[@]}" "$DESTINATION" true
fi

AUTHENTICATION_SUCCEEDED=1
info "Authentification SSH réussie avec la clé privée explicitement sélectionnée."
__KEY_SCRIPT__
    chmod 700 "$stage_script"

    info "ÉTAPE 2/3 - Installation et validation de la clé SSH"
    local -a args=()
    (( KEEP_KEYS == 0 )) || args+=(--keep)
    bash "$stage_script" "${args[@]}"

}

run_hardening() {
    local stage_script="$TMP_STAGE_DIR/03-durcissement.sh"
    cat >"$stage_script" <<'__HARDENING_SCRIPT__'
#!/usr/bin/env bash
set -Eeuo pipefail
IFS=$'\n\t'
export LC_ALL=C

TARGET_PORT="${SSH_PORT:-62238}"
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
  --port PORT          port SSH final, défaut: SSH_PORT ou 62238
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
        if [[ -f "$path" && ! -L "$path" && -s "$path" ]]; then
            AUTHORIZED_KEYS_FILE="$path"
            return 0
        fi
    done
    ADMIN_USER="$previous_user"
    ADMIN_HOME="$previous_home"
    AUTHORIZED_KEYS_FILE=""
    return 1
}

select_admin_user() {
    local name _ uid home shell
    local -a matches=()

    if [[ -n "$REQUESTED_ADMIN_USER" ]]; then
        is_human_user "$REQUESTED_ADMIN_USER" || die "compte administrateur invalide ou non interactif: $REQUESTED_ADMIN_USER"
        find_existing_authorized_keys_for_user "$REQUESTED_ADMIN_USER" || die "aucun authorized_keys non vide et exploitable trouvé pour $REQUESTED_ADMIN_USER"
        return 0
    fi

    if [[ -n "${SUDO_USER:-}" && "${SUDO_USER}" != root ]] && is_human_user "$SUDO_USER"; then
        if find_existing_authorized_keys_for_user "$SUDO_USER"; then return 0; fi
    fi

    while IFS=: read -r name _ uid _ _ home shell; do
        (( uid >= 1000 )) || continue
        [[ "$name" != nobody ]] || continue
        [[ "$shell" != */nologin && "$shell" != */false ]] || continue
        if find_existing_authorized_keys_for_user "$name"; then matches+=("$name"); fi
    done < <(getent passwd)

    ((${#matches[@]} > 0)) || die "aucun compte humain avec authorized_keys non vide n'a été détecté; utilisez --user"
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
        read -r CLIENT_IP _ _ CURRENT_SSH_PORT <<< "$SSH_CONNECTION"
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
        printf '# Managed by durcir-ssh.sh.\n[Socket]\nListenStream=\n'
        for port in "$@"; do printf 'ListenStream=%s\n' "$port"; done
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
for command in awk date dpkg-query getent grep install journalctl mktemp ss sshd systemctl; do need_cmd "$command"; done
command -v apt-get >/dev/null 2>&1 || die "ce script nécessite apt-get (Debian/Ubuntu)"

detect_ssh_units
select_admin_user
detect_current_ports
preflight_target_port
if (( DRY_RUN )); then show_discovery; exit 0; fi

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
__HARDENING_SCRIPT__
    chmod 700 "$stage_script"

    info "ÉTAPE 3/3 - Durcissement SSH, UFW et Fail2Ban"
    local -a args=(--user "$ADMIN_USER" --port "$FINAL_SSH_PORT")
    (( KEEP_OLD_PORT == 0 )) || args+=(--keep-old-port)
    bash "$stage_script" "${args[@]}"

}

if (( SKIP_LOCALE == 0 )); then
    run_locale
else
    info "ÉTAPE 1/3 IGNORÉE - Français, locale et fuseau"
fi

if (( SKIP_KEY == 0 )); then
    run_key
else
    info "ÉTAPE 2/3 IGNORÉE - Installation et validation de la clé SSH"
fi

if (( SKIP_HARDENING == 0 )); then
    run_hardening
else
    info "ÉTAPE 3/3 IGNORÉE - Durcissement SSH, UFW et Fail2Ban"
fi

info "TERMINÉ"
printf 'Compte administrateur : %s\n' "$ADMIN_USER"
printf 'Port SSH final : %s\n' "$FINAL_SSH_PORT"
```
