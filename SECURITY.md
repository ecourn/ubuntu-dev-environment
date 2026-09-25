# Politique de sécurité

Ce dépôt contient un workflow serveur qui peut modifier SSH et, sur demande, des règles UFW, ainsi qu'un installateur d'outils de développement. **Une erreur peut couper l'accès SSH ou modifier l'état du système.** Examinez le code avant de l'exécuter et gardez un accès console/de secours lors de la configuration serveur.

## Versions et périmètre

Le travail de maintenance porte sur la branche courante du dépôt ; il n'y a pas de garantie de correctifs pour les anciennes révisions. L'installateur Python vérifie le statut de support d'Ubuntu à l'exécution et limite les architectures à `amd64`/`arm64`. La CI effectue des contrôles statiques et des tests unitaires sur les runners GitHub `ubuntu-22.04` et `ubuntu-24.04` ; cette matrice **n'est pas** une validation d'installation de bout en bout ni une liste exhaustive des versions Ubuntu compatibles. `ubuntu-26.04` n'est pas ajouté tant qu'un runner hébergé portant ce label n'est pas disponible et vérifié.

## Signaler une vulnérabilité

Ne publiez pas de clés, jetons, journaux sensibles ni de preuve d'exploitation dans une issue publique. Utilisez de préférence [« Report a vulnerability » / GitHub Private Vulnerability Reporting](https://github.com/ecourn/ubuntu-dev-environment/security/advisories/new) **si cette fonctionnalité est activée**. Si elle ne l'est pas, ouvrez une issue sans détails techniques sensibles pour demander un canal privé aux mainteneurs. Incluez dans le signalement privé les fichiers et révisions touchés, les prérequis, l'impact, des étapes minimales de reproduction expurgées de tout secret et, si possible, une proposition de correction. Aucun délai de réponse ou de publication n'est garanti ici.

## Mesures de prudence et limites

- N'exécutez pas le script serveur sur un hôte distant sans accès de secours. Vérifiez séparément la nouvelle connexion SSH et les règles du pare-feu avant de fermer votre session actuelle.
- Exécutez l'installateur de développement avec le compte utilisateur prévu, sans préfixer la commande avec `sudo`. Examinez la sortie de `--dry-run` avant l'installation ; ce mode n'est pas une preuve qu'une installation réelle réussira.
- Ne stockez ni clé privée ni secret dans le dépôt ou les rapports CI. Révoquez et remplacez immédiatement tout secret exposé, puis signalez l'incident en privé.
- La CI ne lance ni installateur, ni UFW, ni modification APT/SSH/systemd. ShellCheck, `bash -n`, les tests unitaires et Gitleaks ne remplacent pas des essais sur une machine ou VM Ubuntu jetable. Le scan Gitleaks porte sur l'arborescence de travail du checkout, **pas sur tout l'historique Git**.

Les garanties effectives doivent être vérifiées sur la révision utilisée ; des modifications du guide et de l'installateur peuvent encore être en cours. Aucune certification ni audit de sécurité indépendant n'est revendiqué.
