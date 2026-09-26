# Étape 3 — Installer l’environnement de développement

Cette étape termine la configuration après la préparation du compte et du serveur. L’installateur vérifie les prérequis et les versions Ubuntu prises en charge, installe les outils sans dépendance Python externe, puis configure Git et GitHub CLI pour le compte `ubuntu`.

## Exécution

Connectez-vous avec le compte `ubuntu` et lancez le script **sans `sudo`** :

```bash
python3 ~/ubuntu-dev-environment/03-environnement-de-developpement/install_dev_environment.py
```

L’installateur utilise les droits `sudo` nécessaires pour les opérations système. L’installation standard doit être lancée depuis un terminal interactif. À la fin, il demande le nom et l’adresse e-mail Git, en proposant les valeurs déjà configurées; appuyez sur Entrée pour les conserver. Il règle aussi la branche par défaut sur `main`.

Pour GitHub CLI, une authentification déjà valide sur `github.com` est conservée par défaut; l’installateur demande avant toute réauthentification. Si aucune authentification valide n’est détectée, il demande un Personal Access Token avec une saisie masquée et vérifie ensuite la connexion. Le token est transmis à GitHub CLI par l’entrée standard, pas dans les arguments d’un processus.

## Ordre suivi par l’installateur

1. Vérifie Ubuntu, l’architecture, Python, `apt`, `systemd`, le compte, le terminal interactif et les accès réseau nécessaires.
2. Détermine les versions compatibles et vérifie les sources et empreintes des téléchargements.
3. Configure les dépôts officiels requis et installe les paquets système.
4. Installe les outils utilisateur, met à jour la configuration du shell et démarre Docker.
5. Configure interactivement l’identité Git et GitHub CLI, puis vérifie l’installation complète.

## Outils installés

- Node.js LTS, npm et pnpm.
- Bun et uv.
- zoxide et fzf.
- GitHub CLI.
- Docker Engine, Buildx et Docker Compose.

## Changements apportés au serveur

- Ajoute les clés de signature et les sources APT officielles nécessaires pour Node.js, GitHub CLI et Docker, puis actualise les index APT.
- Installe ou met à jour les paquets système pour les outils ci-dessus; active et démarre le service `docker`.
- Installe les binaires et outils utilisateur dans l’espace personnel de `ubuntu` et ajoute les initialisations zoxide/fzf à la configuration de son shell.
- Configure `user.name`, `user.email` et `init.defaultBranch=main` dans la configuration Git globale de `ubuntu`, en préservant le nom et l’adresse e-mail existants si vous validez les valeurs proposées.
- Enregistre un manifeste des composants gérés afin de vérifier les mises à jour et de permettre le retrait des binaires utilisateur pris en charge.

Les commandes Docker nécessitent `sudo`; l’installateur n’ajoute pas `ubuntu` au groupe `docker`. Ouvrez un nouveau shell après l’installation pour charger le `PATH` et les initialisations des outils utilisateur.

## Options

- `--dry-run` affiche le plan et vérifie les sources sans installer. Une connexion Internet reste nécessaire; ce mode ne résout pas les versions APT.
- `--uninstall --dry-run` prévisualise le retrait des binaires utilisateur gérés; `--uninstall` l’effectue. Cette opération ne retire pas les paquets APT/npm, les sources système ni les changements de shell.

Pour l’authentification `gh auth login --with-token`, la [documentation GitHub CLI](https://cli.github.com/manual/gh_auth_login) indique un PAT classique avec les scopes `repo`, `read:org` et `gist`. Elle avertit que les PAT fine-grained peuvent produire des comportements inattendus avec cette méthode et recommande plutôt `GH_TOKEN` pour ces tokens.

Les tests unitaires du code sont rangés dans [`tests/`](tests/) et exécutés par la CI; ils ne font pas partie du parcours d’installation du serveur.
