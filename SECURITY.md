# Politique de sécurité

Les scripts de ce dépôt modifient des comptes, les droits administrateur, des paquets système et la configuration SSH/UFW/Fail2ban. Une erreur peut interrompre l’accès au serveur. Utilisez-les sur une version Ubuntu prise en charge et gardez un accès de secours pendant la configuration.

## Compte et accès SSH

Le bootstrap crée ou configure le compte `ubuntu`, ajoute une clé publique à `authorized_keys` si nécessaire et accorde `sudo` sans mot de passe pour permettre une installation non interactive. Toute personne ou tout processus utilisant ce compte peut donc obtenir les privilèges administrateur. Protégez l’accès SSH au compte et n’y autorisez que des clés de confiance.

La clé privée SSH reste sur le poste client. Le script serveur n’accepte pas de clé privée en entrée. Gardez la première session ouverte et testez une deuxième connexion depuis le client avant de confirmer la suppression de l’ancien accès. Le mécanisme de restauration couvre les fichiers de configuration SSH, UFW et Fail2ban qu’il gère; il ne peut pas restaurer le pare-feu externe du fournisseur ni annuler l’installation des paquets APT.

La configuration UFW applique par défaut une politique entrante restrictive. Vérifiez les ports des services déjà hébergés et les règles du fournisseur. Docker peut créer des règles réseau qui contournent UFW; contrôlez les ports publiés et la politique réseau adaptée à votre hôte.

## Versions et vérifications

L’installateur Python accepte les versions Ubuntu reconnues comme officiellement prises en charge par les métadonnées Ubuntu, nécessite Python 3.10 ou supérieur et limite les architectures à `amd64` et `arm64`. La CI vérifie le code sur les runners GitHub Ubuntu 22.04 et 24.04. Elle n’exécute pas les installateurs et ne constitue pas une validation complète d’installation pour chaque version admise.

La CI lance les tests unitaires existants, les contrôles statiques Python et Shell, vérifie les liens et les blocs de code Markdown, et scanne le checkout avec Gitleaks. Elle ne scanne pas tout l’historique Git.

## Signaler une vulnérabilité

Ne publiez pas de clés, jetons, journaux sensibles ni de preuve d’exploitation dans une issue publique. Utilisez de préférence [GitHub Private Vulnerability Reporting](https://github.com/ecourn/ubuntu-dev-environment/security/advisories/new) si cette fonctionnalité est activée. Sinon, ouvrez une issue sans détail technique sensible pour demander un canal privé. Incluez en privé les révisions concernées, les prérequis, l’impact et des étapes minimales de reproduction expurgées de tout secret. Aucun délai de réponse ou de publication n’est garanti.
