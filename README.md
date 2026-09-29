# Ubuntu Dev Environment

Ce dépôt prépare un serveur Ubuntu neuf en trois étapes : préparer le compte `ubuntu`, configurer le serveur et durcir SSH, puis installer l’environnement de développement.

## Prérequis

- Un serveur Ubuntu avec un accès console de secours disponible chez l’hébergeur.
- Une première session SSH réelle ouverte depuis votre poste client avec un compte capable d’utiliser `sudo`.
- PuTTY et PuTTYgen sous Windows si vous utilisez cet écosystème.

> [!IMPORTANT]
> **Lors de la saisie du mot de passe sudo, Linux n'affiche aucun caractère, pas même des astérisques. Tapez normalement le mot de passe puis appuyez sur Entrée.**

Pour un compte `ubuntu` nouvellement créé à l'étape 1, le script désactive la connexion par mot de passe avec `--disabled-password` : aucun mot de passe n'est généré ni affiché. Il faut utiliser la clé privée correspondant à la clé publique installée.

Avant toute mutation, vérifiez immédiatement l’accès administrateur :

```bash
sudo -v
```

Si cette commande échoue, arrêtez-vous : ne lancez aucun script de ce dépôt et ne modifiez ni SSH ni UFW.

## Windows — PuTTY/PuTTYgen : la clé privée ne quitte jamais le PC

1. Ouvrez **PuTTYgen** et générez une paire Ed25519.
2. Enregistrez la **clé privée `.ppk` uniquement sur Windows**, idéalement protégée par une passphrase. Ne la copiez jamais sur le serveur.
3. Dans PuTTYgen, copiez/exportez la **clé publique au format OpenSSH** (`Public key for pasting into OpenSSH authorized_keys file`) dans un fichier, par exemple `ubuntu-dev.pub`.
4. Transférez **uniquement `ubuntu-dev.pub`** vers le serveur, ou collez uniquement son contenu OpenSSH lorsque le script de préparation le demande. Un fichier `.ppk`, `id_rsa`, `id_ed25519` privé ou un bloc `BEGIN OPENSSH PRIVATE KEY` ne doit jamais être envoyé au serveur.
5. Après création du compte `ubuntu`, configurez PuTTY avec le fichier `.ppk` resté sur Windows et ouvrez une deuxième connexion réelle pour valider l’authentification par clé.

## 1. Préparer le compte `ubuntu`

L’ancien bootstrap temporaire/monolithique n’est plus supporté. Clonez le dépôt et exécutez uniquement le script versionné présent dans le checkout :

```bash
if ! sudo -v; then
  echo "Authentification sudo impossible; aucune modification effectuée." >&2
  exit 1
fi

if ! command -v git >/dev/null 2>&1; then
  sudo apt-get update
  sudo apt-get upgrade -y
  sudo apt-get install -y git ca-certificates unzip vim-gtk3
fi

git clone https://github.com/ecourn/ubuntu-dev-environment.git ~/ubuntu-dev-environment
cd ~/ubuntu-dev-environment
sudo bash ./01-preparation-utilisateur/setup-ubuntu-user.sh
```

ensuite (toujours sur root) :

```bash
cd
rm -fr -- ~/ubuntu-dev-environment
```

Le script installe uniquement des **clés publiques OpenSSH** dans `authorized_keys`. Gardez la session initiale ouverte. Ouvrez ensuite une deuxième fenêtre PuTTY avec l’utilisateur `ubuntu` et la clé privée `.ppk` conservée sur Windows. Ne poursuivez que si cette connexion réussit réellement depuis le poste client.

## 2. Configurer le serveur et durcir SSH

Après avoir vérifié une connexion PuTTY par clé avec le compte `ubuntu`, lancez le point d’entrée de l’étape 2 depuis la racine du dépôt :

```bash
git clone https://github.com/ecourn/ubuntu-dev-environment.git ~/ubuntu-dev-environment
cd ~/ubuntu-dev-environment
```

```bash
sudo env SSH_CONNECTION="${SSH_CONNECTION:-}" \
  bash ./02-configuration-serveur/configuration-serveur.sh \
  --user ubuntu
```

Cette étape règle la locale et le fuseau horaire, puis configure SSH, UFW et Fail2ban. Gardez la première session ouverte et suivez le [guide détaillé de l’étape 2](02-configuration-serveur/README.md) : il demande une deuxième connexion depuis votre poste avant de confirmer les changements. UFW applique une politique entrante restrictive ; vérifiez les ports de vos autres services et les règles du fournisseur.

## 3. Installer l’environnement de développement

Après confirmation de l’étape 2 :

```bash
python3 ~/ubuntu-dev-environment/03-environnement-de-developpement/install_dev_environment.py
```

Voir le [README de l’environnement de développement](03-environnement-de-developpement/README.md).

## Sécurité et récupération

Gardez un accès console de secours pendant le durcissement. En cas de refus de la deuxième connexion SSH, ne confirmez pas les changements : le script restaure les configurations SSH, UFW et Fail2ban qu’il gère. Consultez [SECURITY.md](SECURITY.md) pour les effets et limites de cette restauration.
