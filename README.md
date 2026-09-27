# Ubuntu Dev Environment

Ce dépôt prépare un serveur Ubuntu neuf en trois étapes : préparer le compte `ubuntu`, sécuriser SSH avec un workflow transactionnel, puis installer l’environnement de développement.

## Prérequis

- Un serveur Ubuntu avec un accès console de secours disponible chez l’hébergeur.
- Une première session SSH réelle ouverte depuis votre poste client avec un compte capable d’utiliser `sudo`.
- PuTTY et PuTTYgen sous Windows si vous utilisez cet écosystème.

> [!IMPORTANT]
> **Lors de la saisie du mot de passe sudo, Linux n'affiche aucun caractère, pas même des astérisques. Tapez normalement le mot de passe puis appuyez sur Entrée.**

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
  sudo apt-get install -y git ca-certificates
fi

git clone https://github.com/ecourn/ubuntu-dev-environment.git ~/ubuntu-dev-environment
cd ~/ubuntu-dev-environment
sudo bash ./01-preparation-utilisateur/setup-ubuntu-user.sh
```

Le script installe uniquement des **clés publiques OpenSSH** dans `authorized_keys`. Gardez la session initiale ouverte. Ouvrez ensuite une deuxième fenêtre PuTTY avec l’utilisateur `ubuntu` et la clé privée `.ppk` conservée sur Windows. Ne poursuivez que si cette connexion réussit réellement depuis le poste client.

## 2. Sécuriser SSH avec `ssh-workflow.sh`

L’unique méthode supportée est `01-configuration-serveur/bin/ssh-workflow.sh`, avec les phases `prepare`, `finalize`, `status` et `rollback`. Consultez le [guide détaillé](01-configuration-serveur/README.md).

En résumé : transférez de nouveau **uniquement la clé publique OpenSSH** vers le serveur, lancez `prepare` en conservant explicitement `SSH_CONNECTION`, ouvrez une nouvelle connexion externe sur le nouveau port, puis lancez `finalize` depuis cette nouvelle session. Gardez l’ancienne session ouverte jusqu’au succès complet de `finalize`.

## 3. Installer l’environnement de développement

Après `finalize` :

```bash
python3 ~/ubuntu-dev-environment/03-environnement-de-developpement/install_dev_environment.py
```

Voir le [README de l’environnement de développement](03-environnement-de-developpement/README.md).

## Sécurité et récupération

Aucun mot de passe n’est stocké ou passé en argument par le workflow SSH. `prepare` n’active pas UFW, ne change pas ses politiques et ne supprime aucune règle existante. Un test `localhost`, `127.0.0.1`, `::1` ou initié depuis le serveur lui-même n’est jamais accepté comme preuve de connectivité externe.

Consultez aussi [SECURITY.md](SECURITY.md).
