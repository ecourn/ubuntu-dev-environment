# Préparer le compte `ubuntu`

Cette étape crée/vérifie le compte `ubuntu` et installe uniquement des clés **publiques** OpenSSH dans son `authorized_keys`.

> [!IMPORTANT]
> **Lors de la saisie du mot de passe sudo, Linux n'affiche aucun caractère, pas même des astérisques. Tapez normalement le mot de passe puis appuyez sur Entrée.**

Avant de lancer le script :

```bash
sudo -v
```

Si l’authentification sudo échoue, n’effectuez aucune modification.

Exécutez ensuite le script versionné depuis le checkout local :

```bash
sudo bash ./01-preparation-utilisateur/setup-ubuntu-user.sh
```

Ne téléchargez/exécutez pas de script temporaire généré. L’ancienne méthode de bootstrap temporaire n’est plus supportée.

## PuTTY/PuTTYgen

Générez la paire sur Windows. Conservez la clé privée `.ppk` sur Windows, exportez/copiez uniquement la clé publique OpenSSH et fournissez uniquement cette partie publique au serveur. Le script refuse les chemins/fichiers SSH initiaux suspects et valide les clés avec `ssh-keygen`.

Après succès, gardez la session initiale ouverte et ouvrez une deuxième connexion PuTTY comme `ubuntu` avec la `.ppk` restée sur Windows. Cette preuve client doit réussir avant de passer au [workflow SSH transactionnel](../01-configuration-serveur/README.md).
