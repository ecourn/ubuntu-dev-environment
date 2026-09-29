# Étape 3 — Installer les outils de développement

Après avoir testé une nouvelle connexion `ubuntu` sur le **port SSH final** de l’étape 2, lancez dans cette session :

```bash
python3 ~/ubuntu-dev-environment/03-environnement-de-developpement/install_dev_environment.py
```

Lancez le script **sans `sudo`** depuis un terminal interactif. Il demande votre nom et votre adresse e-mail Git, puis l’authentification GitHub CLI si nécessaire. Préparez un Personal Access Token classique GitHub avec les scopes `repo`, `read:org` et `gist` ([documentation GitHub CLI](https://cli.github.com/manual/gh_auth_login)). La saisie du token est masquée. Une connexion `gh` déjà valide peut être conservée ; l’installateur configure Git pour l’utiliser avec GitHub en HTTPS.

Outils installés : Node.js LTS, npm, pnpm, Bun, uv, zoxide, fzf, GitHub CLI, Docker Engine, Buildx et Compose. L'installateur prépare aussi `bubblewrap` pour une éventuelle installation ultérieure de Codex, sans installer Codex. Il teste la création d'un bac à sable sous le compte utilisateur et, si nécessaire, installe et charge le profil AppArmor `bwrap-userns-restrict`. Si le test échoue encore, il signale les restrictions du noyau, de l'hébergeur ou du conteneur sans désactiver la restriction AppArmor globale. Voir la [documentation officielle OpenAI](https://learn.chatgpt.com/docs/sandboxing?surface=app#app-prerequisites). Ouvrez un nouveau terminal après l’installation pour charger le `PATH` et les réglages du shell. Utilisez `sudo` pour les commandes Docker.

Si `gh` est déjà connecté mais que `git push` redemande une connexion, lancez sous le compte `ubuntu` :

```bash
gh auth setup-git --hostname github.com
```

`--dry-run` prévisualise l’installation. `--uninstall --dry-run` prévisualise le retrait des binaires utilisateur gérés ; `--uninstall` effectue ce retrait, sans supprimer les paquets APT/npm ni les sources système. Les [tests unitaires](tests/README.md) concernent les développeurs du projet.
