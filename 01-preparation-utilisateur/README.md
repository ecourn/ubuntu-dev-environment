# Étape 1 — Préparer le compte Ubuntu

Cette étape prépare le compte Linux `ubuntu`, utilisé pour la suite de l’installation. Elle se lance avant de cloner le dépôt.

## Exécution

Depuis la session ouverte avec le compte initial qui dispose de `sudo`, lancez le bloc **1. Préparer le compte `ubuntu`** du [README principal](../README.md). Il télécharge ce script depuis GitHub, lui applique les droits d’exécution et l’exécute avec `sudo`.

Une fois le script terminé :

1. Gardez la session actuelle ouverte comme accès de secours.
2. Ouvrez une deuxième fenêtre PuTTY vers le serveur avec le nom d’utilisateur `ubuntu` et le fichier privé `.ppk` correspondant.
3. Vérifiez que l’authentification par clé fonctionne réellement. Ne fermez pas la session initiale avant ce test.
4. Seulement après le test, clonez le dépôt comme indiqué dans le README principal et passez au [README de configuration du serveur](../02-configuration-serveur/README.md).

Après le clone, le script peut aussi être relancé depuis son chemin local :

```bash
sudo /home/ubuntu/ubuntu-dev-environment/01-preparation-utilisateur/setup-ubuntu-user.sh
```

## Ce que le script modifie

- Installe ou vérifie les outils de base : `curl`, `git`, `iproute2`, Python 3, `sudo`, le client et le serveur OpenSSH, et quelques utilitaires.
- Démarre et active le service SSH s’il n’est pas déjà actif.
- Crée le compte `ubuntu` avec `/home/ubuntu` et Bash, ou vérifie le compte existant.
- Valide avec `ssh-keygen` le contenu du fichier de clés autorisées du compte initial, puis l’ajoute à `/home/ubuntu/.ssh/authorized_keys` sans écraser les clés existantes ni ajouter de doublon.
- Si aucune clé exploitable n’est déjà disponible, demande une clé publique OpenSSH dans le terminal et la valide avant installation.
- Corrige le propriétaire et applique le mode `0700` au dossier SSH et le mode `0600` au fichier `authorized_keys`.
- Ajoute `ubuntu` au groupe `sudo` et crée une règle `sudo` sans mot de passe afin que les étapes suivantes puissent installer les composants système.

## Préparer la clé publique

### Le compte initial utilise déjà une clé SSH

Le script examine `<home_du_compte_initial>/.ssh/authorized_keys`. Il tolère les commentaires et lignes vides, mais exige qu’OpenSSH y reconnaisse au moins une clé publique. Il installe les clés valides pour `ubuntu` sans supprimer un fichier déjà présent ni recopier une clé déjà installée. Si vous avez créé une nouvelle paire PuTTYgen, collez sa clé publique lorsque le script vous le propose; il l’ajoutera sans doublon. Appuyez sur Entrée pour utiliser uniquement les clés déjà détectées.

### Le compte initial utilise encore un mot de passe

Si aucune clé exploitable n’est trouvée, le script vous demande dans le terminal de coller **une clé publique OpenSSH**. Avec PuTTYgen, copiez le champ **Public key for pasting into OpenSSH authorized_keys file**. Une clé Ed25519 ressemble à `ssh-ed25519 AAAAC3... utilisateur@poste`.

Ne collez jamais une clé privée et n’envoyez jamais le fichier privé `.ppk` au serveur. La clé privée reste uniquement sur votre poste Windows. Si le script ne dispose pas de terminal interactif, il s’arrête sans créer un accès SSH par mot de passe; relancez l’étape 1 depuis votre session PuTTY interactive.

Après l’installation, configurez PuTTY avec l’utilisateur `ubuntu` et le fichier `.ppk` correspondant, puis testez une nouvelle connexion dans une deuxième fenêtre. Gardez la session initiale ouverte jusqu’à ce que ce test réussisse. Cette validation depuis le vrai poste client est obligatoire avant le clonage et l’étape 2; l’autotest local du durcissement ne vérifie pas votre fichier `.ppk`.
