# Ubuntu : environnement de développement et serveur

Les dossiers sont numérotés dans l’ordre recommandé. Le parcours serveur est facultatif et indépendant du premier :

1. `01-environnement-de-developpement/` installe un environnement de développement Ubuntu : Node.js LTS, npm, pnpm, Bun, uv, zoxide, fzf, GitHub CLI et Docker.
2. `02-configuration-serveur/` contient un guide distinct pour préparer un serveur : locale, fuseau horaire, clé SSH et durcissement SSH/UFW/Fail2Ban. Il ne dépend pas du premier parcours.

## Environnement de développement

Prérequis : Ubuntu officiellement pris en charge, architecture `amd64` ou `arm64`, Python 3.10+, `apt`, `dpkg`, `systemd`, `sudo`, accès réseau et compte utilisateur avec Bash, Zsh ou Fish. Aucune dépendance Python externe n’est requise.

```bash
git clone https://github.com/ecourn/ubuntu-dev-environment.git
cd ubuntu-dev-environment/01-environnement-de-developpement

# Vérifier les tests
python3 -m unittest discover -s tests -v

# Résoudre les versions et valider les sources sans modifier la machine
python3 install_dev_environment.py --dry-run

# Après examen du résultat, lancer l’installation
python3 install_dev_environment.py
```

Lancez l’installateur depuis votre compte utilisateur, pas avec `sudo` ajouté à la commande. Il vérifie la version Ubuntu et l’architecture avant d’agir.

## Configuration du serveur

Depuis la racine du dépôt, placez-vous dans le dossier du guide avant de générer les scripts et clés temporaires :

```bash
cd 02-configuration-serveur
```

Consultez ensuite `tuto-script-serveur-complet-3-en-1.md` et suivez ses étapes dans l’ordre. Ce parcours modifie notamment SSH et le pare-feu : vérifiez les paramètres, gardez un accès console ou de secours au serveur et ne versionnez jamais vos clés privées.
