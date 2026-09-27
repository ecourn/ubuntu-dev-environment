# Politique de sécurité

Les scripts modifient des comptes, paquets et paramètres SSH. Gardez toujours une session SSH initiale et un accès console de secours pendant les opérations sensibles.

## Clés SSH

Le serveur ne doit recevoir que des **clés publiques OpenSSH**. Une clé privée (`.ppk`, `id_rsa`, `id_ed25519` privé, PEM/OpenSSH private key) reste exclusivement sur le poste client. Le workflow SSH refuse les formats manifestement privés et valide la clé publique avec `ssh-keygen`.

Avec PuTTY/PuTTYgen, générez la paire sur Windows, gardez `.ppk` sur Windows, exportez/copiez la partie publique au format OpenSSH et transférez uniquement cette partie publique au serveur. La seconde connexion de validation utilise la `.ppk` directement dans PuTTY, jamais sur le serveur.

## Changement SSH transactionnel

`01-configuration-serveur/bin/ssh-workflow.sh` est l’unique méthode supportée. `prepare` conserve l’ancien port et les méthodes d’authentification existantes. `finalize` n’applique le durcissement qu’après validation d’une session SSH externe réelle sur le nouveau port, avec origine non-loopback, socket TCP correspondante et ascendance `sshd` cohérente.

Les connexions `localhost`, `127.0.0.1`, `::1` ou initiées depuis le serveur ne prouvent jamais l’accessibilité externe. Aucun mot de passe n’est stocké ni transmis en argument par ce workflow.

L’état transactionnel est écrit atomiquement. Les phases interrompues `incomplete` et `finalizing` sont considérées comme non saines et nécessitent `status`/`rollback`. Un rollback n’écrase pas silencieusement une modification manuelle d’un fichier géré.

## UFW et Docker

`prepare` n’active pas UFW, ne change pas ses politiques et ne supprime aucune règle existante. Si UFW est déjà actif, seules les règles nécessaires sont ajoutées et tracées afin de pouvoir retirer uniquement celles créées par le workflow. Un nouveau port déjà occupé ou publié par Docker est refusé lorsqu’il peut être détecté.

## CI

La CI exécute `bash -n` sur tous les scripts shell, ShellCheck, les tests fonctionnels de `ssh-workflow.sh`, les tests de locale, les tests Python existants et le scan de secrets. Elle ne remplace pas un test réel sur une machine Ubuntu avec accès console de secours.

## Signaler une vulnérabilité

Ne publiez jamais de clé, jeton ou journal sensible dans une issue publique. Utilisez GitHub Private Vulnerability Reporting si disponible, sinon demandez un canal privé sans divulguer les détails sensibles publiquement.
