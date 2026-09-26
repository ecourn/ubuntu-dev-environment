# Étape 1 — Préparer le compte Ubuntu

Cette étape prépare le compte Linux `ubuntu`, utilisé pour la suite de l’installation. Elle se lance avant de cloner le dépôt.

## Exécution

Depuis la session ouverte avec le compte initial qui dispose de `sudo`, lancez le bloc **1. Préparer le compte `ubuntu`** du [README principal](../README.md). Il télécharge ce script depuis GitHub, lui applique les droits d’exécution et l’exécute avec `sudo`.

Une fois le script terminé :

1. Gardez la session actuelle ouverte.
2. Reconnectez-vous au serveur avec le compte `ubuntu`.
3. Clonez le dépôt comme indiqué dans le README principal.
4. Passez au [README de configuration du serveur](../02-configuration-serveur/README.md).

Après le clone, le script peut aussi être relancé depuis son chemin local :

```bash
sudo /home/ubuntu/ubuntu-dev-environment/01-preparation-utilisateur/setup-ubuntu-user.sh
```

## Ce que le script modifie

- Installe ou vérifie les outils de base : `curl`, `git`, `iproute2`, Python 3, `sudo`, le client et le serveur OpenSSH, et quelques utilitaires.
- Démarre et active le service SSH s’il n’est pas déjà actif.
- Crée le compte `ubuntu` avec `/home/ubuntu` et Bash, ou vérifie le compte existant.
- Si nécessaire, copie vers `/home/ubuntu/.ssh/authorized_keys` la clé publique autorisée du compte initial. Il ne copie jamais de clé privée.
- Corrige le propriétaire et les permissions du dossier SSH et du fichier `authorized_keys`.
- Ajoute `ubuntu` au groupe `sudo` et crée une règle `sudo` sans mot de passe afin que les étapes suivantes puissent installer les composants système.

Le script exige une clé publique SSH existante pour pouvoir donner au nouveau compte un accès utilisable. Il s’arrête si ce prérequis manque ou si le compte existant présente un chemin de home inattendu. L’installation et le démarrage d’OpenSSH Server rendent possible la reconnexion avec `ubuntu` si le paquet était absent; le durcissement du service et du pare-feu reste à l’étape 2.
