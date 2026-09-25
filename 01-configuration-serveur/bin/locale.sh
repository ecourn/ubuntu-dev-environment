#!/usr/bin/env bash
# Optional locale/timezone step; never invoked by the SSH workflow.
set -Eeuo pipefail
PATH=/usr/sbin:/usr/bin:/sbin:/bin

usage() {
    printf '%s\n' \
        'Usage: locale.sh --apply (--locale CODE.UTF-8 --timezone ZONE | --preset-fr) [--language-pack]' \
        'The French preset is fr_FR.UTF-8 / Europe/Paris; no locale is selected implicitly.' \
        'A language pack is installed with APT only when --language-pack is supplied.'
}
apply=0 preset=0 language_pack=0 target_locale='' target_timezone=''
while (($#)); do
    case $1 in
        --apply) apply=1; shift ;;
        --preset-fr) preset=1; shift ;;
        --language-pack) language_pack=1; shift ;;
        --locale|--timezone)
            (($# >= 2)) || { printf 'Missing value: %s\n' "$1" >&2; exit 2; }
            if [[ $1 == --locale ]]; then target_locale=$2; else target_timezone=$2; fi
            shift 2 ;;
        -h|--help) usage; exit 0 ;;
        *) printf 'Unknown option: %s\n' "$1" >&2; exit 2 ;;
    esac
done
((apply)) || { usage >&2; exit 2; }
if ((preset)); then
    [[ -z $target_locale && -z $target_timezone ]] || { printf 'Preset conflicts with explicit locale/timezone.\n' >&2; exit 2; }
    target_locale=fr_FR.UTF-8 target_timezone=Europe/Paris
fi
[[ $target_locale =~ ^[a-z]{2,3}_[A-Z]{2}\.UTF-8$ ]] || { printf 'Invalid or missing UTF-8 locale.\n' >&2; exit 2; }
[[ $target_timezone == UTC || $target_timezone =~ ^[A-Za-z0-9_+-]+(/[A-Za-z0-9_+-]+)+$ ]] || { printf 'Invalid or missing timezone.\n' >&2; exit 2; }
((EUID == 0)) || { printf 'Root required.\n' >&2; exit 1; }
if ! { command -v locale-gen >/dev/null && command -v localectl >/dev/null && command -v timedatectl >/dev/null; }; then
    printf 'Install system locale tools first.\n' >&2; exit 1
fi
timedatectl list-timezones | grep -Fxq -- "$target_timezone" || { printf 'Unknown timezone.\n' >&2; exit 1; }
if ((language_pack)); then
    command -v apt-get >/dev/null || { printf 'apt-get missing.\n' >&2; exit 1; }
    apt-get install --yes --no-remove --no-install-recommends "language-pack-${target_locale%%_*}"
fi
locale-gen "$target_locale"
locale -a | grep -Fxiq -- "${target_locale%.UTF-8}.utf8" || { printf 'Locale generation did not produce requested locale.\n' >&2; exit 1; }
localectl set-locale "LANG=$target_locale"
localectl status --no-pager | awk -v expected="LANG=$target_locale" '{for(i=1;i<=NF;i++) if($i==expected) found=1} END{exit !found}' || { printf 'System locale did not update.\n' >&2; exit 1; }
timedatectl set-timezone "$target_timezone"
[[ $(timedatectl show -p Timezone --value) == "$target_timezone" ]] || { printf 'Timezone did not update.\n' >&2; exit 1; }
printf 'Locale %s and timezone %s updated; SSH configuration unchanged.\n' "$target_locale" "$target_timezone"