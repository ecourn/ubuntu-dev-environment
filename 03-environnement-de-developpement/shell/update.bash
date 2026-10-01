#!/usr/bin/env bash
# Actualiser le dépôt sans modifier .bashrc ni écraser des changements locaux.
set -euo pipefail

if (( $# )); then
    printf 'Usage : dev-shell-update\n' >&2
    exit 2
fi
shell_directory=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
repository=$(git -C "$shell_directory" rev-parse --show-toplevel)
cd -- "$repository"

status=$(git status --porcelain --untracked-files=all)
if [[ -n $status ]]; then
    printf 'Mise à jour interrompue : le dépôt contient des modifications locales. Conservez-les avant de réessayer.\n' >&2
    exit 1
fi
branch=$(git branch --show-current)
if [[ -z $branch ]] || ! remote=$(git config --get "branch.$branch.remote") ||
        ! merge_ref=$(git config --get "branch.$branch.merge"); then
    printf 'Mise à jour interrompue : la branche doit suivre une branche distante.\n' >&2
    exit 1
fi
# Récupérer explicitement la branche suivie, même avec un refspec personnalisé.
git fetch -- "$remote" "$merge_ref"
candidate=$(git rev-parse --verify 'FETCH_HEAD^{commit}')
if ! git merge-base --is-ancestor HEAD "$candidate"; then
    printf 'Mise à jour interrompue : la branche contient des commits locaux ou a divergé.\n' >&2
    exit 1
fi

# Valider les fichiers distants avant de changer les fichiers actifs.
for file in config.bash update.bash; do
    relative="03-environnement-de-developpement/shell/$file"
    # Un lien symbolique peut être syntaxiquement valide, mais détourner source.
    mode=$(git ls-tree "$candidate" -- "$relative")
    if [[ $mode != '100644 blob '* && $mode != '100755 blob '* ]]; then
        printf 'Mise à jour interrompue : fichier Bash distant absent ou non régulier (%s).\n' "$file" >&2
        exit 1
    fi
    if ! git show "$candidate:$relative" | bash -n; then
        printf 'Mise à jour interrompue : fichier Bash distant absent ou invalide (%s).\n' "$file" >&2
        exit 1
    fi
done
git merge --no-autostash --no-overwrite-ignore --ff-only "$candidate"
printf '\nConfiguration mise à jour. Exécutez exec bash pour prendre en compte les modifications.\n'
