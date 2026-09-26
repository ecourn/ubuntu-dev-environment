# Ubuntu Dev Environment

Ce dépôt prépare un serveur Ubuntu neuf en trois étapes, dans l’ordre : créer le compte `ubuntu`, configurer le serveur, puis installer les outils de développement. Chaque étape possède son propre README.

## Prérequis

- Un serveur Ubuntu avec une connexion Internet.
- Un compte initial capable d’utiliser `sudo`.
- Une clé SSH publique autorisée pour ce compte. Le premier script s’en sert pour préparer l’accès du compte `ubuntu`; la clé privée reste sur votre poste.

## Parcours d’installation

### 1. Préparer le compte `ubuntu`

Depuis le compte initial, lancez le script hébergé sur GitHub. La commande installe `curl` s’il manque, télécharge le script dans un fichier temporaire et l’exécute avec les droits administrateur :

```bash
set -euo pipefail
umask 077
bootstrap_script="$(mktemp /tmp/ubuntu-dev-bootstrap.XXXXXX)"
trap 'rm -f -- "$bootstrap_script"' EXIT

if ! command -v curl >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get install -y ca-certificates curl
fi

curl --fail --location --retry 3 --silent --show-error --proto '=https' --tlsv1.2 \
  https://raw.githubusercontent.com/ecourn/ubuntu-dev-environment/main/01-preparation-utilisateur/setup-ubuntu-user.sh \
  --output "$bootstrap_script"
chmod 700 "$bootstrap_script"
sudo "$bootstrap_script"
```

Reconnectez-vous ensuite avec le compte `ubuntu`, clonez le dépôt et passez à l’étape suivante :

```bash
git clone https://github.com/ecourn/ubuntu-dev-environment.git ~/ubuntu-dev-environment
cd ~/ubuntu-dev-environment
```

Consultez le [README de préparation du compte](01-preparation-utilisateur/README.md) pour savoir précisément ce que le script installe et modifie.

### 2. Configurer le serveur

Suivez le [README de configuration du serveur](02-configuration-serveur/README.md). Cette étape règle la locale et le fuseau horaire, puis sécurise SSH et configure UFW et Fail2ban. Gardez votre première session SSH ouverte et vérifiez une deuxième connexion avant de confirmer les changements.

### 3. Installer l’environnement de développement

Depuis le compte `ubuntu`, lancez l’installateur sans `sudo` :

```bash
python3 ~/ubuntu-dev-environment/03-environnement-de-developpement/install_dev_environment.py
```

Il vérifie les prérequis, puis installe les outils de développement documentés dans le [README de cette étape](03-environnement-de-developpement/README.md).

## À savoir

La configuration du pare-feu applique une politique entrante restrictive. Vérifiez les ports de vos services et ceux autorisés par le fournisseur avant de terminer l’étape 2. Les scripts peuvent installer des paquets et modifier des réglages système; gardez un accès de secours pendant la configuration.

La [politique de sécurité](SECURITY.md) décrit les effets sensibles et les limites des contrôles automatisés. La CI vérifie le code et la documentation; elle n’exécute pas les installateurs sur un vrai serveur.
