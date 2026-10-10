# ============================================================
# Alias et fonctions Bash Pareto, Ubuntu
# ============================================================


# ----- Environnement ---------------------------------------------

case ":$PATH:" in
    *":$HOME/.local/bin:"*) ;;
    *) export PATH="$HOME/.local/bin:$PATH" ;;
esac
export NPM_CONFIG_PREFIX="$HOME/.local/share/dev-bootstrap"

# ----- Shell / configuration -------------------------------------

# Effacer le terminal.
alias cls='clear'

# Recharger cette configuration Bash dans le shell actuel.
alias eb='source ~/.bashrc'

# Créer un fichier AGENTS.md par défaut dans le dossier actuel.
function agents {
    (set -o noclobber
    cat > AGENTS.md <<'EOF'
Reponds toujours en francais.

Ne commit jamais, sauf si je te le demande.

## Politique multi-agent

### Role de l agent principal

L agent principal agit comme orchestrateur, pas comme executant principal.

Il conserve en priorite :

* l architecture ;
* le raisonnement complexe ;
* la decomposition des taches ;
* les decisions transverses ;
* la coordination des workers ;
* l analyse de leurs retours ;
* l integration et la verification finales.

Il doit preserver autant que possible son contexte et ses tokens pour ces taches de haut niveau.

### Delegation par defaut

Toute tache pouvant raisonnablement etre executee par un worker doit etre deleguee.

La delegation est la regle.
L execution directe par l agent principal est l exception.

Delegue en priorite toute tache :

* longue ou repetitive ;
* couteuse en tokens ;
* necessitant beaucoup de lecture ou d exploration ;
* produisant beaucoup de code ou de texte ;
* impliquant tests, build, lint ou inspection detaillee ;
* pouvant etre delimitee avec un objectif clair.

Cela inclut notamment l exploration du depot, l implementation, les tests, la documentation, les refactorings, le diagnostic de bugs et les revues detaillees.

Meme si l agent principal sait faire la tache, il doit la deleguer si elle consomme beaucoup de tokens sans necessiter sa vision globale.

En cas de doute, delegue.

### Workers

Utilise pour les workers le modele, la configuration et le niveau de raisonnement definis par defaut dans le harnais, sauf instruction explicite contraire.

Ne suppose aucun nom ou famille de modele particulier.

### Parallelisme

Lorsque plusieurs taches sont independantes, privilegie plusieurs workers en parallele.

Evite seulement les modifications concurrentes des memes fichiers lorsqu elles risquent de creer des conflits.

### Boucle d orchestration

Le fonctionnement attendu est :

1. analyser et decomposer ;
2. deleguer ;
3. recuperer les retours ;
4. analyser les resultats ;
5. reorienter si necessaire ;
6. deleguer de nouveau pour corriger, completer ou verifier ;
7. integrer et verifier le resultat final.

Si un worker echoue ou fournit un resultat incomplet, l agent principal ne doit pas reprendre automatiquement le travail lui-meme.

Il doit d abord :

* corriger les instructions ;
* relancer un worker ;
* ou faire verifier le resultat par un autre worker.

### Missions des workers

Chaque mission doit etre suffisamment precise pour permettre un travail autonome.

Indique lorsque pertinent :

* l objectif ;
* le perimetre ;
* les contraintes ;
* les fichiers ou zones concernes ;
* les criteres de reussite ;
* les tests ou verifications attendus.

Les workers doivent retourner un resume exploitable indiquant ce qu ils ont inspecte, modifie, teste et les problemes restant ouverts.

L agent principal doit s appuyer sur ces retours sans refaire inutilement leur travail.

### Verification

Pour les changements importants, separe autant que possible implementation et verification.

Un worker peut implementer, un autre verifier ou tester, puis l agent principal arbitre et valide.

### Exception

L agent principal peut effectuer directement une modification triviale de quelques lignes lorsque la delegation serait manifestement disproportionnee.

Cette exception doit rester rare et ne jamais justifier une tache longue, volumineuse ou couteuse en tokens.

### Principe prioritaire

Avant d effectuer lui-meme une tache, l agent principal doit se demander :

"Cette tache necessite-t-elle ma vision globale, ou est-elle surtout couteuse en tokens, en lecture, en ecriture ou en execution ?"

Si elle est surtout couteuse en ressources et peut etre delimitee, elle doit etre deleguee.

L agent principal pense, decompose, coordonne, arbitre, integre et verifie.

Les workers explorent, ecrivent, modifient, testent et inspectent.

### Validation stricte des workers

Une delegation n'est consideree comme effective que si le harnais retourne
explicitement un identifiant de worker/thread actif.

L'agent principal ne doit jamais :

* annoncer qu'un worker a ete lance sans identifiant retourne par le harnais ;
* appeler une operation d'attente si aucun worker actif n'est enregistre ;
* inventer ou reutiliser un chemin, un nom de tache ou un label comme identifiant de worker ;
* continuer lui-meme une tache deleguee simplement parce qu'aucun worker n'a encore termine.

Avant toute attente, verifier qu'au moins un worker reel est actif.

Si une creation de worker echoue ou ne retourne pas d'identifiant exploitable,
considerer la delegation comme echouee et relancer explicitement la creation
au lieu d'attendre un worker inexistant.
EOF
    )
}


# ----- Navigation ------------------------------------------------

alias ..='cd ..'
alias ...='cd ../..'

# Créer un dossier, y compris ses dossiers parents, puis y entrer.
# Utilisation : mkcd <dossier>
function mkcd {
    if [ "$#" -ne 1 ]; then
        printf 'Usage: mkcd <directory>\n' >&2
        return 2
    fi

    mkdir -p -- "$1" && cd -- "$1" || return
}

# Aller à la racine du dépôt Git actuel.
function croot {
    local root

    root=$(git rev-parse --show-toplevel 2>/dev/null) || {
        printf 'Not inside a Git repository.\n' >&2
        return 1
    }

    cd -- "$root" || return
}


# ----- Liste / inspection ----------------------------------------

# Affichage détaillé, avec les fichiers les plus récents en premier.
alias lt='ls -lah --sort=time'

# Afficher les permissions symboliques et numériques, le propriétaire et le groupe.
alias perms='stat -c "%A %a %U:%G %n"'


# ----- Recherche de fichiers / texte -----------------------------

# Afficher les 20 fichiers de code contenant le plus de lignes.
alias toplinecode="find . -type f \( -name '*.ts' -o -name '*.tsx' -o -name '*.js' -o -name '*.jsx' -o -name '*.py' -o -name '*.go' -o -name '*.rs' -o -name '*.sh' \) \
  -not -path '*/node_modules/*' \
  -not -path '*/.git/*' \
  -not -path '*/.venv/*' \
  -not -path '*/dist/*' \
  -not -path '*/build/*' \
  -exec wc -l {} \; | sort -nr | head -n 20"

# Rechercher des fichiers par nom partiel.
# Utilisation : ff <nom> [dossier]
function ff {
    if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
        printf 'Usage: ff <name-pattern> [directory]\n' >&2
        return 2
    fi

    local directory=${2:-.}
    [[ $directory == -* ]] && directory="./$directory"
    find "$directory" -type f -iname "*$1*"
}

# Rechercher du texte récursivement en ignorant les fichiers binaires et .git.
# Utilisation : ftext <texte> [dossier]
function ftext {
    if [ "$#" -lt 1 ] || [ "$#" -gt 2 ]; then
        printf 'Usage: ftext <text> [directory]\n' >&2
        return 2
    fi

    local directory=${2:-.}
    [[ $directory == -* ]] && directory="./$directory"
    grep -RInI --exclude-dir=.git -- "$1" "$directory"
}

# Afficher les 30 fichiers les plus récents du dossier actuel,
# récursivement, en excluant .git.
# Permet de choisir entre Markdown, Excel, TXT ou tous ces formats.
# Pour chaque fichier, utilise la date la plus récente entre création (si disponible)
# et modification. Les noms de fichiers contenant espaces, "|", tabulations ou
# retours à la ligne sont traités sans ambiguïté.
function mdrecent {
    local choice file metadata birth modify latest event record ts display_file count
    local -a patterns=()

    printf 'Type de fichiers à rechercher :\n'
    printf '  1) Markdown\n'
    printf '  2) Excel\n'
    printf '  3) TXT\n'
    printf '  4) Tout\n'

    if ! read -r -p 'Choix [1-4] : ' choice; then
        printf 'Impossible de lire le choix.\n' >&2
        return 1
    fi

    case "$choice" in
        1)
            patterns=(
                -iname '*.md' -o
                -iname '*.markdown'
            )
            ;;
        2)
            patterns=(
                -iname '*.xlsx' -o
                -iname '*.xls'  -o
                -iname '*.xlsm' -o
                -iname '*.xlsb' -o
                -iname '*.xltx' -o
                -iname '*.xltm'
            )
            ;;
        3)
            patterns=(
                -iname '*.txt'
            )
            ;;
        4)
            patterns=(
                -iname '*.md'       -o
                -iname '*.markdown' -o
                -iname '*.xlsx'     -o
                -iname '*.xls'      -o
                -iname '*.xlsm'     -o
                -iname '*.xlsb'     -o
                -iname '*.xltx'     -o
                -iname '*.xltm'     -o
                -iname '*.txt'
            )
            ;;
        *)
            printf 'Choix invalide. Utilise 1, 2, 3 ou 4.\n' >&2
            return 2
            ;;
    esac

    count=0

    (set -o pipefail
    find . -type d -name .git -prune -o \
        -type f \( "${patterns[@]}" \) -print0 |
    while IFS= read -r -d '' file; do
        # Le fichier peut disparaître entre find et stat : dans ce cas, on l'ignore.
        if ! metadata=$(stat --printf='%W %Y' -- "$file" 2>/dev/null); then
            continue
        fi

        read -r birth modify <<< "$metadata"

        # Garde-fou : stat doit renvoyer deux timestamps entiers.
        if [[ ! $birth =~ ^[0-9]+$ || ! $modify =~ ^[0-9]+$ ]]; then
            continue
        fi

        if (( birth > modify )); then
            latest=$birth
            event='CREATED'
        else
            latest=$modify
            event='MODIFIED'
        fi

        # Enregistrement terminé par NUL : aucun caractère autorisé dans un nom
        # de fichier ne peut casser le tri ou le découpage.
        printf '%020d|%-8s|%s\0' "$latest" "$event" "$file"
    done |
    sort -z -t '|' -k1,1nr |
    while IFS= read -r -d '' record; do
        (( count += 1 ))
        if (( count > 30 )); then
            continue
        fi

        # Préfixe de taille fixe : 20 chiffres + "|" + 8 caractères + "|".
        ts=${record:0:20}
        event=${record:21:8}
        event=${event%% *}
        file=${record:30}

        # Affichage échappé avec %q pour qu'un nom contenant des caractères
        # spéciaux reste sur une seule ligne et soit réutilisable dans Bash.
        printf -v display_file '%q' "$file"

        printf '%-9s %s  %s\n' \
            "$event" \
            "$(date -d "@$ts" '+%Y-%m-%d %H:%M:%S')" \
            "$display_file"
    done
    )
}


# ----- Historique Bash -------------------------------------------

# Rechercher dans l'historique Bash sans tenir compte de la casse.
# Utilisation : histfind <texte>
function histfind {
    if [ "$#" -eq 0 ]; then
        printf 'Usage: histfind <text>\n' >&2
        return 2
    fi

    history | grep -i -- "$*"
}


# ----- Processus -------------------------------------------------

# Rechercher des processus par nom ou motif et afficher leur commande complète.
# Utilisation : psg <motif>
alias psg='pgrep -af'


# ----- Disque / stockage -----------------------------------------

# Afficher l'utilisation des systèmes de fichiers avec des tailles lisibles.
alias dfh='df -hT'

# Afficher l'espace utilisé sur un niveau de sous-dossiers.
alias duh='du -h --max-depth=1'


# ----- Réseau ----------------------------------------------------

# Afficher un aperçu compact des interfaces réseau et des adresses IP.
alias ipb='ip -brief addr'

# Afficher les ports TCP/UDP en écoute et les processus associés si autorisé.
alias ports='ss -tulpn'


# ----- APT -------------------------------------------------------

# Actualiser les index des paquets.
alias aptup='sudo apt update'

# Mettre à niveau les paquets installés de manière interactive.
alias aptupgrade='sudo apt upgrade'

# Rechercher des paquets disponibles.
alias aptsearch='apt search'


# ----- systemd / journaux ----------------------------------------

# Afficher l'état d'un service.
# Utilisation : svcstatus <service>
alias svcstatus='systemctl status'

# Afficher les journaux d'un service.
# Utilisation : svclogs <service> [-f]
alias svclogs='journalctl -u'


# ----- Git : état / inspection -----------------------------------

# Afficher l'état standard du dépôt.
alias gs='git status'

# Afficher un état compact du dépôt.
alias gst='git status -sb'

# Afficher uniquement les fichiers modifiés ou non suivis.
alias gss="git status --short --untracked-files=all | sed 's/^...//'"

# Afficher uniquement les fichiers TypeScript (.ts et .tsx) modifiés ou non suivis.
alias gsts="gss | grep -E '\.tsx?$'"

# Afficher la branche actuelle.
alias gb='git branch --show-current'

# Afficher les modifications non indexées.
alias gd='git diff'

# Afficher les modifications indexées.
alias gds='git diff --staged'

# Afficher un graphe compact des 20 derniers commits.
alias glog='git log --oneline --graph --decorate -20'

# Afficher uniquement les chemins des fichiers modifiés ou créés dans le dernier commit.
alias gl='git show --pretty="" --name-only HEAD'


# ----- Git : branches --------------------------------------------

# Changer de branche.
alias gsw='git switch'

# Créer une nouvelle branche et basculer dessus.
alias gnew='git switch -c'

# Créer une nouvelle branche avec git checkout et basculer dessus.
# Utilisation : gcb <branche>
function gcb {
    if [ "$#" -ne 1 ]; then
        printf 'Usage: gcb <branch>\n' >&2
        return 2
    fi

    git check-ref-format --branch "$1" >/dev/null || return
    git checkout -b "$1"
}

# ----- Mise à jour depuis le dépôt Git ----------------------------
# Le chemin est capturé au chargement, quel que soit le dossier courant.
_DEV_SHELL_DIRECTORY=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)
function dev-shell-update {
    bash "$_DEV_SHELL_DIRECTORY/update.bash" "$@"
}
