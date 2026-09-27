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
command -v systemctl >/dev/null 2>&1 || fail 'systemctl est introuvable.'

if [ -r /etc/os-release ]; then
  # shellcheck disable=SC1091
  . /etc/os-release
  [ "${ID:-}" = 'ubuntu' ] || fail "ce script cible Ubuntu, système détecté : ${ID:-inconnu}."
fi

printf '%s\n' 'Installation des composants nécessaires...'
"${SUDO[@]}" apt-get update
"${SUDO[@]}" env DEBIAN_FRONTEND=noninteractive \
  apt-get install -y locales "$LANGUAGE_PACK" systemd-timesyncd

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
"${SUDO[@]}" systemctl enable --now systemd-timesyncd.service
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
