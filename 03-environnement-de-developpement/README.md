# Étape 3 — Installer l’environnement de développement

Cette étape termine la configuration après la préparation du compte et du serveur. L’installateur vérifie les prérequis et les versions Ubuntu prises en charge, puis installe les outils sans dépendance Python externe.

## Exécution

Connectez-vous avec le compte `ubuntu` et lancez le script **sans `sudo`** :

```bash
python3 ~/ubuntu-dev-environment/03-environnement-de-developpement/install_dev_environment.py
```

L’installateur utilise les droits `sudo` nécessaires pour les opérations système. Il n’a pas besoin d’interaction pour l’installation standard. L’authentification à GitHub est facultative et ne se lance que si vous ajoutez `--github-auth`.

## Ordre suivi par l’installateur

1. Vérifie Ubuntu, l’architecture, Python, `apt`, `systemd`, le compte et les accès réseau nécessaires.
2. Détermine les versions compatibles et vérifie les sources et empreintes des téléchargements.
3. Configure les dépôts officiels requis et installe les paquets système.
4. Installe les outils utilisateur, met à jour la configuration du shell, démarre Docker et vérifie le résultat.

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
- Enregistre un manifeste des composants gérés afin de vérifier les mises à jour et de permettre le retrait des binaires utilisateur pris en charge.

Les commandes Docker nécessitent `sudo`; l’installateur n’ajoute pas `ubuntu` au groupe `docker`. Ouvrez un nouveau shell après l’installation pour charger le `PATH` et les initialisations des outils utilisateur.

## Options

- `--dry-run` affiche le plan et vérifie les sources sans installer. Une connexion Internet reste nécessaire; ce mode ne résout pas les versions APT.
- `--github-auth` demande facultativement un jeton GitHub dans un terminal sécurisé.
- `--uninstall --dry-run` prévisualise le retrait des binaires utilisateur gérés; `--uninstall` l’effectue. Cette opération ne retire pas les paquets APT/npm, les sources système ni les changements de shell.

Les tests unitaires du code sont rangés dans [`tests/`](tests/) et exécutés par la CI; ils ne font pas partie du parcours d’installation du serveur.
