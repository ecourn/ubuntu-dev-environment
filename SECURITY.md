# Politique de sécurité

Les scripts de ce dépôt modifient des comptes, les droits administrateur, des paquets système et la configuration SSH/UFW/Fail2ban. Une erreur peut interrompre l’accès au serveur. Utilisez-les sur une version Ubuntu prise en charge et gardez un accès de secours pendant la configuration.

## Compte et accès SSH

Le bootstrap crée ou configure le compte `ubuntu`, valide avec OpenSSH les clés autorisées du compte initial et les installe dans `/home/ubuntu/.ssh/authorized_keys`. Si aucune clé exploitable n’existe, il demande une clé publique OpenSSH dans le terminal. Si une clé existe déjà, il permet aussi d’ajouter la clé PuTTYgen que vous venez de créer ou de conserver uniquement les clés détectées. Pour PuTTYgen, fournissez uniquement le champ **Public key for pasting into OpenSSH authorized_keys file**. La clé privée, notamment le fichier `.ppk`, doit rester sur le poste client et ne doit jamais être collée ni copiée sur le serveur. Toute personne ou tout processus utilisant le compte `ubuntu` peut obtenir les privilèges administrateur; n’y autorisez que des clés de confiance.

Après l’étape 1, gardez la session initiale ouverte et vérifiez une nouvelle connexion PuTTY avec l’utilisateur `ubuntu` et la clé privée correspondante. Ce test réel depuis le client est requis avant l’étape 2, qui désactive l’authentification par mot de passe; le script demande de confirmer qu’il a réussi avant toute modification. Le durcissement fait également un autotest local avec une clé Ed25519 temporaire; ce test ne prouve pas que la clé du poste client fonctionne. Il demande ensuite un deuxième test PuTTY avant de finaliser. Le mécanisme de restauration couvre les fichiers de configuration SSH, UFW et Fail2ban qu’il gère; il ne peut pas restaurer le pare-feu externe du fournisseur ni annuler l’installation des paquets APT.

La configuration UFW applique par défaut une politique entrante restrictive. Vérifiez les ports des services déjà hébergés et les règles du fournisseur. Docker peut créer des règles réseau qui contournent UFW; contrôlez les ports publiés et la politique réseau adaptée à votre hôte.

## Versions et vérifications

L’installateur Python accepte les versions Ubuntu reconnues comme officiellement prises en charge par les métadonnées Ubuntu, nécessite Python 3.10 ou supérieur et limite les architectures à `amd64` et `arm64`. La CI vérifie le code sur les runners GitHub Ubuntu 22.04 et 24.04. Elle n’exécute pas les installateurs et ne constitue pas une validation complète d’installation pour chaque version admise.

La CI lance les tests automatisés du traitement des clés SSH et de l’installateur, les contrôles statiques Python et Shell, vérifie les liens et les blocs de code Markdown, et scanne le checkout avec Gitleaks. Elle ne scanne pas tout l’historique Git.

## Signaler une vulnérabilité

Ne publiez pas de clés, jetons, journaux sensibles ni de preuve d’exploitation dans une issue publique. Utilisez de préférence [GitHub Private Vulnerability Reporting](https://github.com/ecourn/ubuntu-dev-environment/security/advisories/new) si cette fonctionnalité est activée. Sinon, ouvrez une issue sans détail technique sensible pour demander un canal privé. Incluez en privé les révisions concernées, les prérequis, l’impact et des étapes minimales de reproduction expurgées de tout secret. Aucun délai de réponse ou de publication n’est garanti.
