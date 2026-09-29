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

on_error() {
  printf 'Erreur à la ligne %s : commande %s (code retour %s).\n' "$1" "$2" "$3" >&2
}
trap 'on_error "$LINENO" "$BASH_COMMAND" "$?"' ERR

command -v apt-get >/dev/null 2>&1 || fail 'apt-get est introuvable.'
command -v locale >/dev/null 2>&1 || fail 'locale est introuvable.'
command -v timedatectl >/dev/null 2>&1 || fail 'timedatectl est introuvable.'
command -v systemctl >/dev/null 2>&1 || fail 'systemctl est introuvable.'

if [ -r /etc/os-release ]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  [ "${ID:-}" = 'ubuntu' ] || fail "ce script cible Ubuntu, système détecté : ${ID:-inconnu}."
fi

printf '%s\n' 'Installation des composants nécessaires...'
"${SUDO[@]}" apt-get update
# Refuser toute résolution APT qui retirerait un serveur de temps existant.
APT_PLAN="$("${SUDO[@]}" env LC_ALL=C apt-get -s install -y locales "$LANGUAGE_PACK")"
if printf '%s\n' "$APT_PLAN" | grep -Eq '^Remv (chrony|systemd-timesyncd)([[:space:]]|$)'; then
  fail 'installation de la locale : APT prévoit de supprimer un backend NTP.'
fi
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
package_installed() {
  [ "$(dpkg-query -W -f='${Status}' "$1" 2>/dev/null)" = 'install ok installed' ]
}

service_exists() {
  local state
  state="$(systemctl show -p LoadState --value "$1")"
  [ "$state" = 'loaded' ]
}

command -v dpkg-query >/dev/null 2>&1 || fail 'dpkg-query est introuvable.'
if package_installed chrony; then
  TIME_SERVICE=chrony
elif package_installed systemd-timesyncd; then
  TIME_SERVICE=systemd-timesyncd
else
  printf '%s\n' 'Aucun backend NTP installé : installation de chrony...'
  "${SUDO[@]}" env DEBIAN_FRONTEND=noninteractive apt-get install -y chrony
  package_installed chrony || fail "chrony n'est pas installé après APT."
  TIME_SERVICE=chrony
fi

TIME_UNIT="${TIME_SERVICE}.service"
service_exists "$TIME_UNIT" || fail "service NTP introuvable : $TIME_UNIT."

if [ "$TIME_SERVICE" = chrony ] && service_exists systemd-timesyncd.service; then
  if systemctl is-active --quiet systemd-timesyncd.service || \
     systemctl is-enabled --quiet systemd-timesyncd.service; then
    "${SUDO[@]}" systemctl disable --now systemd-timesyncd.service
  fi
fi

"${SUDO[@]}" systemctl enable --now "$TIME_UNIT"
systemctl is-active --quiet "$TIME_UNIT" || fail "service NTP inactif : $TIME_UNIT."
if [ "$TIME_SERVICE" = chrony ] && systemctl is-active --quiet systemd-timesyncd.service; then
  fail 'chrony et systemd-timesyncd sont actifs simultanément.'
fi

NTP_SYNCED=no
if [ "$TIME_SERVICE" = chrony ]; then
  command -v chronyc >/dev/null 2>&1 || fail 'chronyc est introuvable.'
  if [ "$NTP_WAIT_SECONDS" -eq 0 ]; then
    # Un seul essai : waitsync 0 attendrait indéfiniment.
    if chronyc waitsync 1 0 0 0.1 >/dev/null 2>&1; then NTP_SYNCED=yes; fi
  else
    NTP_TRIES="$(( (NTP_WAIT_SECONDS + NTP_POLL_SECONDS - 1) / NTP_POLL_SECONDS + 1 ))"
    if chronyc waitsync "$NTP_TRIES" 0 0 "$NTP_POLL_SECONDS" >/dev/null 2>&1; then
      NTP_SYNCED=yes
    fi
  fi
else
  START_SECONDS="$(date +%s)"
  while :; do
    if [ "$(timedatectl show -p NTPSynchronized --value)" = yes ]; then
      NTP_SYNCED=yes
      break
    fi
    NOW_SECONDS="$(date +%s)"
    ELAPSED_SECONDS="$((NOW_SECONDS - START_SECONDS))"
    [ "$ELAPSED_SECONDS" -lt "$NTP_WAIT_SECONDS" ] || break
    REMAINING_SECONDS="$((NTP_WAIT_SECONDS - ELAPSED_SECONDS))"
    if [ "$NTP_POLL_SECONDS" -lt "$REMAINING_SECONDS" ]; then
      sleep "$NTP_POLL_SECONDS"
    else
      sleep "$REMAINING_SECONDS"
    fi
  done
fi

if [ "$NTP_SYNCED" = no ]; then
  printf '%s\n' "Avertissement : $TIME_SERVICE est actif, mais la synchronisation n'est pas encore confirmée." >&2
fi

SERVICE_ACTIVE=non
if systemctl is-active --quiet "$TIME_UNIT"; then SERVICE_ACTIVE=oui; fi
SYNC_CONFIRMED=non
if [ "$NTP_SYNCED" = yes ]; then SYNC_CONFIRMED=oui; fi

printf '%s\n' '--- Vérification finale ---'
printf 'Locale cible : %s\n' "$TARGET_LOCALE"
printf 'Locale système : %s\n' "$CURRENT_SYSTEM_LANG"
printf 'Fuseau : %s\n' "$(timedatectl show -p Timezone --value)"
printf 'Service de temps : %s\n' "$TIME_SERVICE"
printf 'Service actif : %s\n' "$SERVICE_ACTIVE"
printf 'Synchronisation confirmée : %s\n' "$SYNC_CONFIRMED"
printf 'Date locale : %s\n' "$(LANG="$TARGET_LOCALE" LC_ALL="$TARGET_LOCALE" date '+%A %d %B %Y %H:%M:%S %Z %z')"
printf '%s\n' 'CONFIGURATION_OK'
