# Étape 2 — Sécuriser le serveur

Prérequis : une connexion PuTTY **réussie** depuis votre PC avec `ubuntu` et sa clé privée. Gardez cette session ouverte et conservez l’accès à la console de secours.

Dans cette session `ubuntu` :

```bash
cd ~/.ubuntu-dev-environment
sudo env SSH_CONNECTION="${SSH_CONNECTION:-}" \
  bash ./02-configuration-serveur/configuration-serveur.sh --user ubuntu
```

1. Le script affiche le nouveau port SSH (choisi aléatoirement entre `49152` et `65535`). Si l’hébergeur filtre les ports, autorisez celui-ci dans son pare-feu et gardez l’ancien port ouvert. Confirmez le test de connexion de l’étape 1 uniquement si vous l’avez réalisé.
2. Quand le script le demande, ouvrez depuis votre PC **une autre connexion PuTTY** avec `ubuntu`, la même `.ppk` et le nouveau port. Revenez à la première session et tapez `oui` seulement si la connexion fonctionne. Toute autre réponse déclenche la restauration des configurations gérées.
3. Après la fin du script, vérifiez encore l’accès sur le port final avant de lancer l’[étape 3](../03-environnement-de-developpement/README.md).

## Basculement définitif vers le nouveau port

Le test demandé pendant la transition précède la configuration finale. Conservez le numéro `Port SSH : PORT` affiché à la fin. Gardez la session actuelle ouverte et lancez une nouvelle connexion PuTTY sur ce port final avec la même `.ppk`. Fermez l’ancienne session uniquement après ce test. L’option `--keep-old-port` conserve des règles UFW, sans garantir que SSH écoute encore sur l’ancien port.

Le script règle la locale sur `fr_FR.UTF-8`, le fuseau sur `Europe/Paris` et la synchronisation horaire en préservant le backend NTP existant (`chrony` ou `systemd-timesyncd`). Si aucun backend n'est installé, il installe `chrony`. Il configure ensuite SSH, UFW et Fail2ban. UFW refuse par défaut les connexions entrantes : prévoyez les règles des autres services hébergés. L’accès SSH par mot de passe est désactivé.

Pour choisir le port vous-même, ajoutez `--port 54321` à la commande. `--dry-run` prévisualise les contrôles SSH sans modifier le serveur. Les autres options sont affichées avec `bash ./02-configuration-serveur/configuration-serveur.sh --help`. Les limites de la restauration sont décrites dans [SECURITY.md](../SECURITY.md).
