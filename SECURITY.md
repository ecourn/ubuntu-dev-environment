# Politique de sécurité

Les scripts modifient des comptes, des paquets, la locale et les paramètres SSH, UFW et Fail2ban. Une erreur peut interrompre l’accès au serveur. Gardez une session SSH initiale et un accès console de secours pendant les opérations sensibles.

## Clés SSH

Le serveur ne doit recevoir que des **clés publiques OpenSSH**. Une clé privée (`.ppk`, `id_rsa`, `id_ed25519` privé, PEM/OpenSSH private key) reste exclusivement sur le poste client. Le script de préparation du compte valide la clé publique avec `ssh-keygen` et refuse les clés privées.

Avec PuTTY/PuTTYgen, générez la paire sur Windows, gardez `.ppk` sur Windows, exportez/copiez la partie publique au format OpenSSH et transférez uniquement cette partie publique au serveur. Les connexions de validation utilisent la `.ppk` directement dans PuTTY, jamais sur le serveur.

## Durcissement SSH

L’étape 2 suppose qu’une connexion réelle par clé avec le compte `ubuntu` a déjà réussi depuis le poste client. Avant toute modification, le script demande de confirmer ce test. Il désactive ensuite l’authentification SSH par mot de passe et demande une deuxième connexion PuTTY sur le port configuré. Gardez la première session ouverte jusqu’à la fin.

Le script effectue aussi un test SSH local avec une clé Ed25519 temporaire ; ce test interne ne prouve pas que la clé privée du poste client fonctionne. Le test client demandé pendant la transition précède la configuration finale. Après la fin du script, conservez le port SSH final affiché et vérifiez une nouvelle connexion client fraîche sur ce port avant de fermer toute session encore fonctionnelle. Avec `--keep-old-port`, les anciennes règles UFW SSH gérées restent en place, mais l'écoute SSH sur l'ancien port n'est pas garantie.

Si la deuxième connexion n’est pas confirmée, le script restaure les configurations SSH, UFW et Fail2ban qu’il gère. Les paquets déjà installés restent en place ; les sauvegardes sont conservées dans `/var/backups/ssh-hardening-*`. Cette restauration ne peut pas annuler une règle du pare-feu externe du fournisseur.

## UFW et services hébergés

L’étape 2 active UFW avec les connexions entrantes refusées par défaut et les connexions sortantes autorisées, puis autorise le port SSH choisi. Ajoutez les règles nécessaires aux autres services avant de poursuivre. Les ports publiés par Docker peuvent nécessiter une configuration réseau supplémentaire.

## CI

La CI exécute `bash -n` et ShellCheck sur les scripts shell, les tests Python existants, les contrôles de documentation et le scan de secrets. Elle ne remplace pas une validation sur un vrai serveur Ubuntu avec accès console de secours.

## Signaler une vulnérabilité

Ne publiez jamais de clé, jeton ou journal sensible dans une issue publique. Utilisez GitHub Private Vulnerability Reporting si disponible, sinon demandez un canal privé sans divulguer les détails sensibles publiquement.
