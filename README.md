# Ubuntu : serveur et environnement de développement

Les dossiers suivent l’ordre d’exécution recommandé : configurez d’abord le serveur, puis installez l’environnement de développement.

## 1. Configuration du serveur

Clonez le dépôt, puis ouvrez le dossier du guide :

```bash
git clone https://github.com/ecourn/ubuntu-dev-environment.git
cd ubuntu-dev-environment
cd 01-configuration-serveur
```

Consultez `tuto-script-serveur-complet-3-en-1.md` et suivez ses étapes dans l’ordre. Le guide modifie notamment SSH et le pare-feu : gardez un accès console ou de secours, puis vérifiez qu’une nouvelle connexion SSH fonctionne avant de continuer. Ne versionnez jamais vos clés privées.

## 2. Environnement de développement

Prérequis : Ubuntu officiellement pris en charge, architecture `amd64` ou `arm64`, Python 3.10+, `apt`, `dpkg`, `systemd`, `sudo`, accès réseau et compte utilisateur avec Bash, Zsh ou Fish. Aucune dépendance Python externe n’est requise.

Après avoir confirmé l’accès au serveur, depuis le dossier `01-configuration-serveur` :

```bash
cd ../02-environnement-de-developpement

# Vérifier les tests
python3 -m unittest discover -s tests -v

# Résoudre les versions et valider les sources sans modifier la machine
python3 install_dev_environment.py --dry-run

# Après examen du résultat, lancer l’installation
python3 install_dev_environment.py
```

L’installateur installe Node.js LTS, npm, pnpm, Bun, uv, zoxide, fzf, GitHub CLI et Docker. Lancez-le depuis votre compte utilisateur, sans ajouter `sudo` à la commande.
