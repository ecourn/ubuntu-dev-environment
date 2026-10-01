# Étape 3 — Installer les outils de développement

Après avoir testé une nouvelle connexion `ubuntu` sur le **port SSH final** de l’étape 2, lancez dans cette session :

```bash
python3 ~/ubuntu-dev-environment/03-environnement-de-developpement/install_dev_environment.py
```

Lancez le script **sans `sudo`** depuis un terminal interactif. Il demande votre nom et votre adresse e-mail Git, puis l’authentification GitHub CLI si nécessaire. Préparez un Personal Access Token classique GitHub avec les scopes `repo`, `read:org` et `gist` ([documentation GitHub CLI](https://cli.github.com/manual/gh_auth_login)). La saisie du token est masquée. Une connexion `gh` déjà valide peut être conservée ; l’installateur configure Git pour l’utiliser avec GitHub en HTTPS.

Outils installés : Node.js LTS, npm, pnpm, Bun, uv, zoxide, fzf, GitHub CLI, Docker Engine, Buildx et Compose. L'installateur prépare aussi `bubblewrap` pour une éventuelle installation ultérieure de Codex, sans installer Codex. Il teste la création d'un bac à sable sous le compte utilisateur et, si nécessaire, installe et charge le profil AppArmor `bwrap-userns-restrict`. Si le test échoue encore, il signale les restrictions du noyau, de l'hébergeur ou du conteneur sans désactiver la restriction AppArmor globale. Voir la [documentation officielle OpenAI](https://learn.chatgpt.com/docs/sandboxing?surface=app#app-prerequisites). Exécutez `exec bash` après l’installation pour charger le `PATH`, les alias et les fonctions Bash. Utilisez `sudo` pour les commandes Docker.

Si `gh` est déjà connecté mais que `git push` redemande une connexion, lancez sous le compte `ubuntu` :

```bash
gh auth setup-git --hostname github.com
```

`--dry-run` prévisualise l’installation. `--uninstall --dry-run` prévisualise le retrait des binaires utilisateur gérés ; `--uninstall` effectue ce retrait, sans supprimer les paquets APT/npm ni les sources système. Les [tests unitaires](tests/README.md) concernent les développeurs du projet.

## Configuration Bash et mises à jour

L’étape 3 configure automatiquement `~/.bashrc`. Le fichier [shell/config.bash](shell/config.bash) contient les alias, fonctions et réglages Bash ; il reste dans le dépôt et peut être maintenu indépendamment de l’installateur. Les nouveaux utilisateurs reçoivent la version présente sur la branche qu’ils clonent.

Le chargement du fichier est placé dans un seul bloc délimité par `# >>> dev-bootstrap managed >>>` et `# <<< dev-bootstrap managed <<<`, avec le PATH des outils, zoxide et fzf. Une nouvelle installation remplace uniquement ce bloc et conserve les réglages personnels autour. Des balises incomplètes ou dupliquées interrompent l’opération. Si un ancien script Pareto a été collé manuellement hors du bloc, l’installateur demande de le retirer après sauvegarde avant de continuer.

Les fichiers Bash et les configurations utilisateur sont vérifiés avant le début de l’installation. La fonction `agents` crée un `AGENTS.md` uniquement s’il n’existe pas déjà. La valeur personnelle de `NODE_OPTIONS` est conservée ; en son absence, la mémoire maximale de Node.js est réglée à 4096 Mio.

Conservez le dépôt à son emplacement d’installation : `.bashrc` charge la configuration depuis ce chemin, et ignore ce chargement si le fichier est absent. Si vous déplacez le dépôt, relancez l’étape 3 pour actualiser le chemin. Pour Zsh et Fish, seul leur bloc géré est actualisé et `.bashrc` est également préparé ; `exec bash` ouvre la session Bash configurée.

Après avoir chargé Bash, les mises à jour se font avec :

```bash
dev-shell-update
exec bash
```

La commande récupère la branche distante suivie par le clone (GitHub pour le clone décrit ici), vérifie la syntaxe des fichiers Bash distants, puis avance le dépôt uniquement si aucune fusion n’est nécessaire. Elle actualise le dépôt entier sans relancer l’installation des outils et sans écrire dans `.bashrc`. Les changements de l’installateur nécessitant une nouvelle installation demandent de relancer l’étape 3.

Elle s’arrête si le dépôt comporte des fichiers modifiés ou non suivis, des commits locaux, une divergence ou des fichiers Bash distants absents, non réguliers ou invalides. Elle refuse aussi une mise à jour qui écraserait un fichier local ignoré par Git. Vos modifications locales sont conservées. Personnalisez de préférence votre shell hors du bloc géré dans `.bashrc` ; réservez les fichiers du dépôt aux réglages partagés. Pour publier ces réglages, commitez puis poussez vos changements sur GitHub.
