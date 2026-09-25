# Ubuntu : serveur et environnement de développement

Les dossiers suivent l’ordre d’exécution recommandé : préparez d’abord l'accès SSH serveur, puis installez l’environnement de développement.

## 1. Configuration du serveur

Clonez le dépôt et restez à sa racine pour suivre les commandes du guide :

```bash
git clone https://github.com/ecourn/ubuntu-dev-environment.git
cd ubuntu-dev-environment
```

Consultez `01-configuration-serveur/tuto-script-serveur-complet-3-en-1.md` et suivez ses étapes dans l’ordre. Le workflow serveur maintenu se trouve dans `01-configuration-serveur/bin/` ; il prépare puis finalise la transition SSH depuis deux connexions distinctes. Gardez l'ancienne session ouverte et un accès console/de secours ; confirmez une connexion indépendante au nouveau port avant finalisation. La clé privée reste sur le client. Le script ne configure pas automatiquement tous les pare-feu externes, Fail2ban ou les variantes de service SSH.

## 2. Environnement de développement

Prérequis : Ubuntu officiellement pris en charge (vérification à l'exécution), architecture `amd64` ou `arm64`, Python 3.10+, `apt`, `dpkg`, `systemd`, `sudo`, accès réseau et compte utilisateur avec Bash, Zsh ou Fish. Aucune dépendance Python externe n’est requise. Les tests CI sur Ubuntu 22.04 et 24.04 ne prouvent pas qu'une installation complète fonctionne sur ces versions ni sur d'autres versions.

Après avoir confirmé l’accès au serveur, depuis la racine du dépôt :

```bash
cd 02-environnement-de-developpement

# Vérifier les tests
python3 -m unittest discover -s tests -v

# Préflight sans mutation ; les versions APT ne sont pas encore résolues
python3 install_dev_environment.py --dry-run

# Après examen du résultat, lancer l’installation
python3 install_dev_environment.py
```

L’installateur vise Node.js LTS, npm, pnpm, Bun, uv, zoxide, fzf, GitHub CLI et Docker. Lancez-le depuis votre compte utilisateur, sans ajouter `sudo` à la commande. Le mode à blanc consulte des versions amont et vérifie la disponibilité de sources, mais ne résout pas les versions effectivement installables par APT et ne prouve pas la réussite d'une installation réelle. Lisez les changements proposés et utilisez une VM jetable pour un essai complet avant un hôte important.

L'installation de `gh` n'exige pas de connexion GitHub ; `--github-auth` demande séparément une authentification interactive. Sans cette option, le mode non interactif n'a pas besoin de TTY pour GitHub (les droits `sudo` restent un prérequis distinct). `--uninstall --dry-run` vérifie les binaires utilisateur enregistrés ; `--uninstall` retire uniquement ces binaires si leur empreinte correspond au manifeste. Les paquets APT, les paquets npm globaux, le bloc shell et les sources système restent en place : ce n'est pas une désinstallation complète.

## Modèle de menace et ressources

Le workflow SSH vise les erreurs de migration, les clés publiques mal placées et les chemins de configuration ambigus, non un administrateur root hostile ni un pare-feu amont mal configuré. L'ancienne session et l'accès console sont les moyens de récupération. Sa clé privée reste exclusivement sur le poste client ; seul `authorized_keys` du compte choisi est modifié. L'état root (`/var/lib/hermes-ssh-workflow`) et le drop-in SSH (`/etc/ssh/sshd_config.d/00-hermes-workflow.conf`) sont gérés par le projet ; le guide serveur détaille leur sauvegarde et `rollback`. Les règles UFW sont ajoutées uniquement sur demande à un UFW déjà actif ; les règles existantes ne sont pas retirées.

UFW seul ne protège pas nécessairement les ports publiés par Docker : Docker peut créer des règles réseau qui contournent sa politique. Le workflow vérifie les publications TCP des conteneurs Docker actifs et refuse un conflit avec le port SSH visé, sans filtrer les autres publications ; contrôlez aussi le pare-feu du fournisseur ou la politique Docker appropriée. Ne configurez pas `iptables=false` comme pseudo-correction. L'installateur de développement écrit également des keyrings/sources APT sous `/etc/apt`, des binaires utilisateur sous `~/.local/share/dev-bootstrap` et un manifeste sous `~/.local/state/dev-bootstrap` ; il ne supprime pas les configurations préexistantes non prouvées comme siennes.

## Contrôles et sécurité

La [CI](.github/workflows/ci.yml) lance les tests Python et shell, Ruff et mypy, vérifie la syntaxe Bash (`bash -n`, ShellCheck), la cohérence élémentaire des liens et blocs Markdown, et scanne l'arborescence courante pour les secrets avec Gitleaks. Elle n'exécute pas les scripts d'installation et ne modifie ni UFW ni SSH sur le runner. Les téléchargements d'outils statiques CI sont versionnés et vérifiés par SHA-256 ; les actions GitHub sont référencées par SHA. Les runners hébergés configurés sont Ubuntu 22.04 et 24.04, pas 26.04 tant que son label n'est pas disponible et vérifié.

Consultez [SECURITY.md](SECURITY.md) pour le signalement privé et les limites des vérifications. N'interprétez pas des tests unitaires ou un mode à blanc comme un audit de sécurité ou un test d'installation complet.
