# Ubuntu Dev Environment

Prépare un serveur Ubuntu en trois étapes : créer le compte `ubuntu`, sécuriser SSH, puis installer les outils de développement. Suivez les étapes dans l’ordre. Gardez la console de secours de l’hébergeur accessible.

## Avant de commencer (sur votre PC)

Avec PuTTYgen, créez une clé Ed25519. Enregistrez la clé privée `.ppk` **sur votre PC uniquement**. Gardez ouverte une connexion SSH au serveur avec un compte administrateur (`sudo` ou `root`). Le serveur ne doit recevoir que la clé **publique OpenSSH**, affichée par PuTTYgen dans le champ « Public key for pasting into OpenSSH authorized_keys file ».

> Quand `sudo` demande un mot de passe, rien ne s’affiche pendant la saisie : tapez-le puis appuyez sur Entrée.

Le dépôt est cloné dans `~/.ubuntu-dev-environment`, un dossier caché pour garder le répertoire personnel dégagé. Si vous avez déjà installé le dépôt dans `~/ubuntu-dev-environment`, suivez les [instructions de migration](03-environnement-de-developpement/README.md#migrer-un-dépôt-existant).

## 1. Créer le compte `ubuntu` (session administrateur initiale)

Copiez ce bloc dans la session SSH initiale. Si `sudo -v` échoue, les autres commandes ne seront pas lancées.

```bash
if sudo -v; then
  sudo apt-get update &&
  sudo apt-get install -y git ca-certificates &&
  git clone https://github.com/ecourn/ubuntu-dev-environment.git ~/.ubuntu-dev-environment &&
  cd ~/.ubuntu-dev-environment &&
  sudo bash ./01-preparation-utilisateur/setup-ubuntu-user.sh &&
  cd ~ &&
  rm -rf -- ~/.ubuntu-dev-environment
fi
```

Quand le script le demande, collez **la clé publique OpenSSH**, jamais la `.ppk`. Après une exécution réussie, le bloc supprime le dépôt cloné dans le répertoire personnel de l’administrateur initial. Gardez cette session ouverte. Depuis votre PC, ouvrez une nouvelle connexion PuTTY avec l’utilisateur `ubuntu` et votre `.ppk`. Continuez seulement si elle fonctionne. [Détails de l’étape 1](01-preparation-utilisateur/README.md).

## 2. Sécuriser le serveur (nouvelle session `ubuntu`)

Dans la session `ubuntu` que vous venez de tester :

```bash
git clone https://github.com/ecourn/ubuntu-dev-environment.git ~/.ubuntu-dev-environment
cd ~/.ubuntu-dev-environment
sudo env SSH_CONNECTION="${SSH_CONNECTION:-}" \
  bash ./02-configuration-serveur/configuration-serveur.sh --user ubuntu
```

Le script affiche le port SSH choisi. Si votre hébergeur a un pare-feu réseau, autorisez ce port **avant de confirmer** et gardez l’ancien ouvert. Lorsque le script le demande, testez depuis votre PC une deuxième connexion PuTTY sur ce port ; tapez `oui` uniquement si elle fonctionne.

Il configure aussi la locale `fr_FR.UTF-8`, le fuseau `Europe/Paris` et la synchronisation horaire en préservant le backend NTP existant (`chrony` ou `systemd-timesyncd`). Si aucun backend n'est installé, il installe `chrony`.

> [!IMPORTANT]
> Conservez le `Port SSH : ...` final. Sans fermer la session actuelle, ouvrez une nouvelle connexion PuTTY avec la même `.ppk` sur ce port. Fermez l’ancienne session seulement après ce test. Passez à l’étape 3 après cette vérification.

[Détails de l’étape 2](02-configuration-serveur/README.md).

## 3. Installer les outils (connexion `ubuntu` sur le port final)

Lancez l’installateur **sans `sudo`** :

```bash
python3 ~/.ubuntu-dev-environment/03-environnement-de-developpement/install_dev_environment.py
```

Suivez les demandes pour l’identité Git et l’accès GitHub. La configuration Bash est intégrée automatiquement. Exécutez ensuite `exec bash` pour prendre en compte les modifications. Pour les mises à jour ultérieures, lancez `dev-shell-update`, puis `exec bash`. [Outils et options de l’étape 3](03-environnement-de-developpement/README.md).

En cas d’échec SSH, gardez toute session encore ouverte et utilisez la console de secours. Voir [sécurité et récupération](SECURITY.md).
