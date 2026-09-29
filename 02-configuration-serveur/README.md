# Étape 2 — Configurer le serveur

Cette étape règle les paramètres système et protège l’accès SSH. Exécutez-la après avoir préparé le compte `ubuntu` et cloné le dépôt. Gardez votre première session SSH ouverte jusqu’à la fin.

> **Prérequis SSH : l’étape 2 ne crée pas et n’installe pas la clé SSH initiale.** Elle suppose que `/home/ubuntu/.ssh/authorized_keys` a été préparé par l’étape 1 et qu’une connexion réelle depuis votre poste client avec la clé privée correspondante a déjà réussi. Si ce test n’a pas encore été fait, revenez au [README de l’étape 1](../01-preparation-utilisateur/README.md).

Le durcissement applique notamment `AuthenticationMethods publickey`, `PubkeyAuthentication yes` et `PasswordAuthentication no`. La clé doit donc fonctionner depuis PuTTY **avant** de lancer cette étape. Ne désactivez pas l’accès initial et ne fermez pas votre session de secours avant validation.

Avant toute modification, le script vous demande de confirmer que ce premier test PuTTY a réussi. Cette confirmation ne remplace pas le test : si vous ne l’avez pas fait, répondez autrement que `oui` et revenez à l’étape 1.

## Ordre d’exécution

Le point d’entrée est `configuration-serveur.sh`. Il vérifie le compte administrateur et le port SSH, puis appelle les deux scripts suivants dans l’ordre :

1. `configuration-locale.sh` installe `systemd-timesyncd` si nécessaire, puis configure la locale française, le fuseau horaire et la synchronisation de l’horloge.
2. `durcir-ssh.sh` configure SSH, UFW et Fail2ban. Il installe les paquets qui leur sont nécessaires.

Lancez le point d’entrée depuis le compte `ubuntu`, à la racine du dépôt :

```bash
cd ~/ubuntu-dev-environment
sudo env SSH_CONNECTION="${SSH_CONNECTION:-}" \
  bash ./02-configuration-serveur/configuration-serveur.sh \
  --user ubuntu
```

Sans `--port`, le script choisit un port aléatoire libre dans la plage `49152–65535`. Pour en fixer un vous-même, ajoutez `--port PORT`, par exemple `--port 54321`. Le précontrôle affiche le port choisi. Avant le durcissement, le script vous demande de confirmer qu'il est autorisé dans le pare-feu réseau de votre fournisseur, si celui-ci en utilise un. Gardez aussi l'ancien port ouvert. Le script ouvre d'abord le nouveau port dans UFW en conservant l'ancien, puis demande de vérifier une deuxième connexion PuTTY sur le port choisi. Après cette confirmation, il installe la configuration SSH finale et retire normalement les anciennes règles UFW SSH gérées, sauf avec `--keep-old-port`.

## Vérification de l’accès SSH

Avant de finaliser la configuration, le script vous demande de tester une **deuxième connexion SSH depuis votre poste client** sur le port indiqué. Gardez la première session ouverte. Si la nouvelle connexion fonctionne, revenez dans la première session et tapez `oui`.

Toute autre réponse lance la restauration des configurations SSH, UFW et Fail2ban gérées par le script. Les paquets déjà installés restent en place. Les sauvegardes sont conservées dans `/var/backups/ssh-hardening-*`.

Ce test pendant le durcissement est un contrôle supplémentaire de la nouvelle politique. Le script génère aussi une clé Ed25519 temporaire pour vérifier une connexion et un tunnel SSH locaux; cette clé de test interne ne valide pas la clé privée `.ppk` de votre poste. Le test PuTTY effectué après l’étape 1 reste obligatoire.

## Basculement définitif vers le nouveau port

Le test PuTTY demandé par le script intervient **pendant la transition**, lorsque l'ancien et le nouveau port sont configurés. Après votre confirmation, le script installe la configuration finale, vérifie localement le nouveau port et retire normalement les anciennes règles UFW SSH gérées. Avec `--keep-old-port`, ces anciennes règles UFW sont conservées, mais cette option ne garantit pas que SSH écoute encore sur l'ancien port.

À la fin de la commande, la ligne `Port SSH : PORT` indique le **port SSH définitif**. Conservez ce numéro. Lorsque la commande est totalement terminée, **gardez la session actuelle ouverte** et ouvrez depuis votre poste une **nouvelle session PuTTY fraîche** avec le compte `ubuntu`, la même clé privée `.ppk` restée sur Windows et le port final affiché. Vérifiez que cette nouvelle connexion fonctionne, puis enregistrez ce port dans votre session PuTTY. Fermez l'ancienne session seulement après cette vérification. Toutes les connexions SSH suivantes doivent utiliser le port final, y compris si vous avez choisi `--keep-old-port`.

Si la connexion fraîche échoue, gardez toute session encore ouverte et utilisez si nécessaire la console de secours de l'hébergeur. Ne commencez pas l'étape 3 avant d'avoir rétabli et vérifié l'accès sur le port final.

## Changements apportés au serveur

- Installe `locales`, `language-pack-fr` et `systemd-timesyncd`, génère `fr_FR.UTF-8`, la définit comme locale système, règle le fuseau sur `Europe/Paris` et active la synchronisation NTP.
- Installe `openssh-client`, `ufw`, `fail2ban`, `python3` et `python3-systemd`.
- Ajoute une configuration SSH et une règle Fail2ban pour protéger le service SSH.
- Active UFW avec les connexions entrantes refusées par défaut et les connexions sortantes autorisées. Il ajoute une règle pour le port SSH choisi.
- Active ou recharge les services SSH et Fail2ban.

UFW peut bloquer les autres services hébergés sur le serveur. Ajoutez leurs règles avant de poursuivre. Les ports publiés par Docker peuvent également nécessiter une configuration réseau supplémentaire.

## Prévisualisation et options

Pour prévisualiser la détection SSH sans modifier le serveur, ajoutez `--dry-run`. Ce mode saute la configuration de la locale.

Options disponibles : `--keep-old-port` conserve les anciennes règles UFW SSH gérées, sans garantir une écoute SSH sur l'ancien port; `--skip-locale` garde la locale et le fuseau actuels; `--skip-hardening` ne modifie ni SSH, ni UFW, ni Fail2ban. Elles servent aux configurations particulières; le parcours standard n’en a pas besoin.

## Étape suivante

Après la fin complète du script, la récupération du port SSH final et la validation d'une connexion PuTTY fraîche sur ce port, installez les outils de développement depuis cette nouvelle session selon le [README de l’étape 3](../03-environnement-de-developpement/README.md).
