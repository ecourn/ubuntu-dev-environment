# Ubuntu Dev Environment

Ce dépôt prépare un serveur Ubuntu neuf en trois étapes, dans l’ordre : créer le compte `ubuntu`, configurer le serveur, puis installer les outils de développement. Chaque étape possède son propre README.

## Prérequis

- Un serveur Ubuntu avec une connexion Internet.
- Une première session SSH ouverte avec un compte initial capable d’utiliser `sudo`.
- Une clé publique OpenSSH pour se connecter avec `ubuntu`. Si le compte initial ne possède pas déjà de clé publique exploitable, l’étape 1 vous la demandera dans cette session.

### Windows : préparer la clé avec PuTTYgen

1. Ouvrez PuTTYgen et générez une clé **Ed25519**.
2. Protégez idéalement la clé privée par une passphrase.
3. Enregistrez le fichier privé `.ppk` uniquement sur votre poste Windows. Ne le copiez jamais sur le serveur et ne le collez jamais dans un terminal.
4. La valeur à fournir au serveur est le champ **Public key for pasting into OpenSSH authorized_keys file**. Elle ressemble à `ssh-ed25519 AAAAC3... utilisateur@poste`.

L’étape 1 installe cette clé publique dans `/home/ubuntu/.ssh/authorized_keys`. Elle reprend les clés autorisées du compte initial si le fichier contient déjà une clé OpenSSH exploitable ; sinon, elle vous demandera de coller la clé publique. Si elle détecte déjà une clé, elle vous permet aussi d’ajouter la clé PuTTYgen que vous venez de créer, ou d’appuyer sur Entrée pour garder les clés détectées. Le README de l’[étape 1](01-preparation-utilisateur/README.md) décrit les deux cas.

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

Après le succès de l’étape 1 :

1. Gardez la session initiale ouverte comme accès de secours.
2. Ouvrez une **deuxième fenêtre PuTTY** vers le serveur. Utilisez `ubuntu` comme nom d’utilisateur et choisissez le fichier `.ppk` correspondant dans la configuration d’authentification.
3. Vérifiez que cette nouvelle connexion fonctionne réellement par clé. Ne fermez pas la session initiale avant cette validation.

Seulement après ce test, depuis la nouvelle session `ubuntu`, clonez le dépôt :

```bash
git clone https://github.com/ecourn/ubuntu-dev-environment.git ~/ubuntu-dev-environment
cd ~/ubuntu-dev-environment
```

Consultez le [README de préparation du compte](01-preparation-utilisateur/README.md) pour savoir précisément ce que le script installe et modifie.

### 2. Configurer le serveur

Suivez le [README de configuration du serveur](02-configuration-serveur/README.md). Cette étape suppose que la connexion PuTTY par clé a déjà été testée et vous demande de le confirmer avant toute modification. Elle règle la locale et le fuseau horaire, puis sécurise SSH et configure UFW et Fail2ban. Gardez une session de secours ouverte et effectuez le deuxième test SSH demandé pendant le durcissement.

### 3. Installer l’environnement de développement

Depuis le compte `ubuntu`, lancez l’installateur sans `sudo` :

```bash
python3 ~/ubuntu-dev-environment/03-environnement-de-developpement/install_dev_environment.py
```

Il vérifie les prérequis, puis installe les outils de développement documentés dans le [README de cette étape](03-environnement-de-developpement/README.md). L’étape se termine par la configuration interactive de l’identité Git et de GitHub CLI; lancez-la depuis un terminal.

## À savoir

La configuration du pare-feu applique une politique entrante restrictive. Vérifiez les ports de vos services et ceux autorisés par le fournisseur avant de terminer l’étape 2. Les scripts peuvent installer des paquets et modifier des réglages système; gardez un accès de secours pendant la configuration.

La [politique de sécurité](SECURITY.md) décrit les effets sensibles et les limites des contrôles automatisés. La CI vérifie le code et la documentation; elle n’exécute pas les installateurs sur un vrai serveur.
