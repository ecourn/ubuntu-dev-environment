# Étape 2 — Configurer le serveur

Cette étape règle les paramètres système et protège l’accès SSH. Exécutez-la après avoir préparé le compte `ubuntu` et cloné le dépôt. Gardez votre première session SSH ouverte jusqu’à la fin.

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

Le port de la session SSH actuelle est conservé par défaut; depuis une console locale, le port 22 est utilisé. Vérifiez que ce port est aussi autorisé par le pare-feu de votre fournisseur. Pour choisir un autre port, ajoutez `--port PORT`.

## Vérification de l’accès SSH

Avant de finaliser la configuration, le script vous demande de tester une **deuxième connexion SSH depuis votre poste client** sur le port indiqué. Gardez la première session ouverte. Si la nouvelle connexion fonctionne, revenez dans la première session et tapez `oui`.

Toute autre réponse lance la restauration des configurations SSH, UFW et Fail2ban gérées par le script. Les paquets déjà installés restent en place. Les sauvegardes sont conservées dans `/var/backups/ssh-hardening-*`.

## Changements apportés au serveur

- Installe `locales`, `language-pack-fr` et `systemd-timesyncd`, génère `fr_FR.UTF-8`, la définit comme locale système, règle le fuseau sur `Europe/Paris` et active la synchronisation NTP.
- Installe `openssh-client`, `ufw`, `fail2ban`, `python3` et `python3-systemd`.
- Ajoute une configuration SSH et une règle Fail2ban pour protéger le service SSH.
- Active UFW avec les connexions entrantes refusées par défaut et les connexions sortantes autorisées. Il ajoute une règle pour le port SSH choisi.
- Active ou recharge les services SSH et Fail2ban.

UFW peut bloquer les autres services hébergés sur le serveur. Ajoutez leurs règles avant de poursuivre. Les ports publiés par Docker peuvent également nécessiter une configuration réseau supplémentaire.

## Prévisualisation et options

Pour prévisualiser la détection SSH sans modifier le serveur, ajoutez `--dry-run`. Ce mode saute la configuration de la locale.

Options disponibles : `--keep-old-port` conserve les anciennes règles UFW SSH gérées; `--skip-locale` garde la locale et le fuseau actuels; `--skip-hardening` ne modifie ni SSH, ni UFW, ni Fail2ban. Elles servent aux configurations particulières; le parcours standard n’en a pas besoin.

## Étape suivante

Après confirmation de la nouvelle connexion SSH, installez les outils de développement selon le [README de l’étape 3](../03-environnement-de-developpement/README.md).
