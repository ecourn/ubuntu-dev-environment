# Workflow SSH serveur : deux connexions, aucune clé privée sur le serveur

L'ancien script monolithique intégré ici est **retiré** : il demandait une clé privée au serveur, la supprimait, testait localhost, activait UFW et changeait ses politiques. Ne le recopiez pas. Les scripts maintenus sont dans [`bin/`](bin/) : [`ssh-workflow.sh`](bin/ssh-workflow.sh) et [`locale.sh`](bin/locale.sh).

## Avant de commencer

- Préparez **sur votre poste client** une clé SSH privée, son fichier public et une empreinte de clé d'hôte déjà vérifiée par un canal indépendant. Ne transférez **que la clé publique** vers le serveur (par exemple `~/admin.pub`). Ne copiez ni n'exécutez la clé privée côté serveur ; le script ne l'ouvre, ne la modifie et ne la supprime jamais.
- Gardez l'ancienne session SSH ouverte. Vérifiez l'accès à la console de secours du fournisseur, les règles de pare-feu réseau externe, les ports publiés par Docker, les services TCP existants et le port visé. Faites une sauvegarde et planifiez une fenêtre de maintenance.
- Le script exige une session SSH distante avec `SSH_CONNECTION` transmis explicitement à `sudo`, un compte administrateur non-root existant et un service `ssh.service` ou `sshd.service` actif. Il refuse les unités socket SSH installées, **même désactivées**, et les sockets actifs/activés ; examinez et migrez leur activation séparément. Il revérifie ces conditions avant `finalize`. Vérifiez aussi les unités SSH personnalisées, les fichiers `sshd_config` avec `Match`, `Include` et `AuthorizedKeysFile`, ainsi que l'interaction des règles `Port` déjà présentes.
- Le fichier public doit être un fichier régulier lisible côté serveur. Aucun paquet n'est installé automatiquement. Les outils `sshd`, `systemctl`, `ss`, `flock`, `ssh-keygen`, `getent` et `python3` sont requis ; si `docker` est présent, son inventaire doit être accessible.

## Étape 1 — préparer depuis la session actuelle

Depuis la session distante ouverte sur l'ancien port :

```bash
SCRIPT=./01-configuration-serveur/bin/ssh-workflow.sh
ADMIN_USER="$(id -un)"
sudo env SSH_CONNECTION="$SSH_CONNECTION" "$SCRIPT" prepare \
  --user "$ADMIN_USER" --public-key "$HOME/admin.pub" --port 62238
```

`--ufw` est **facultatif** : uniquement si UFW est déjà actif. Il affiche son état et ses règles avant d'ajouter `62238/tcp` ; il ne l'active pas, ne change pas les politiques par défaut, ne supprime aucune règle existante et ne configure pas les autres services. En cas de relance de `prepare`, réutilisez exactement le même choix `--ufw` : le script compare les règles gérées visibles à leur inventaire enregistré et refuse une règle supprimée ou modifiée (récupération manuelle, sans toucher aux autres règles). Si le pare-feu amont filtre ce port, autorisez-le séparément **avant** la connexion suivante. Le script refuse un port TCP publié par un conteneur Docker en cours d'exécution, même sans listener visible par `ss` : la publication par DNAT peut **contourner UFW**. Vérifiez également les règles Docker et du pare-feu amont.

L'état et les copies de sauvegarde des clés publiques sont conservés sous `/var/lib/hermes-ssh-workflow` (root, mode 0700). Le groupe et le mode du fichier `authorized_keys` existant sont conservés pour l'installation et la restauration ; les ACL et attributs étendus sur le fichier ou `.ssh` exigent un examen manuel et sont refusés avant modification. Le script préserve l'ancien port durant la transition et vérifie `sshd -t` et la politique effective avant de recharger le service. Il ne redémarre ni socket ni service par défaut.

## Étape 2 — preuve indépendante et finalisation

**Sur votre client**, lancez une *nouvelle* session SSH vers l'adresse publique et le nouveau port avec votre clé privée locale ; vérifiez la clé d'hôte avec votre empreinte connue. N'utilisez ni tunnel local ni `localhost` comme preuve :

```bash
ssh -o StrictHostKeyChecking=yes -o IdentitiesOnly=yes \
  -o PreferredAuthentications=publickey -o PasswordAuthentication=no \
  -o KbdInteractiveAuthentication=no -i ~/.ssh/ma-cle \
  -p 62238 admin@MON_SERVEUR
```

Remplacez `admin` par le compte choisi à l'étape 1 et `MON_SERVEUR` par l'adresse publique ; les variables de la première session serveur ne sont **pas** disponibles sur le client ni dans cette nouvelle session.

Dans **cette nouvelle session**, vérifiez `id -un` (doit être le compte administrateur choisi) et `printf '%s\n' "$SSH_CONNECTION"` (quatrième champ : `62238`). Retrouvez ensuite le répertoire du dépôt sur le serveur :

```bash
cd /chemin/vers/linux_install
SCRIPT=./01-configuration-serveur/bin/ssh-workflow.sh
sudo env SSH_CONNECTION="$SSH_CONNECTION" "$SCRIPT" finalize
```

La finalisation refuse une session dont le port serveur n'est pas le nouveau port, ainsi qu'une origine loopback. Elle désactive l'ancien port dans son fichier SSH géré, valide la configuration effective, puis recharge SSH. Les anciennes règles UFW restent intactes ; gérez leur retrait ultérieurement après inventaire de tous les services et accès. Vérifiez la reconnexion depuis une troisième session et gardez une console de secours.

## Statut / récupération

```bash
sudo ./01-configuration-serveur/bin/ssh-workflow.sh status
sudo ./01-configuration-serveur/bin/ssh-workflow.sh rollback
```

`rollback` est disponible avant finalisation et enlève uniquement le drop-in SSH inchangé créé par ce workflow. Il restaure `authorized_keys` à partir de la sauvegarde si son contenu n'a pas changé depuis l'installation ; si le fichier était absent auparavant ou si une interruption empêche de prouver son contenu installé (snapshot incomplet), la clé est conservée par prudence pour examen manuel. La règle UFW facultative reste en place. Si le drop-in ou les fichiers d'état ont été modifiés, le script refuse de les remplacer. Une transition finalisée nécessite une récupération manuelle via console, car un rollback automatique pourrait couper la connexion active. Les métadonnées sont écrites avant les sauvegardes/clés ; les fichiers critiques et répertoires sont synchronisés avant les mutations suivantes. Une erreur après mutation peut laisser l'état `preparing` ou `finalizing` : examinez `status`, conservez la session existante, puis relancez `finalize` depuis la nouvelle session si elle existe, ou utilisez `rollback` depuis l'ancienne. `phase=incomplete` signale des artefacts sans métadonnées fiables : **ne les supprimez pas automatiquement** ; inspectez-les et récupérez manuellement depuis la console. Une relance de `prepare` conforme sur l'état `prepared` et de `finalize` sur l'état `finalized` ne répète pas la mutation ; après un rollback réussi, l'état est archivé dans le répertoire root et une nouvelle préparation est possible.

**Secours console uniquement :** `sudo ./01-configuration-serveur/bin/ssh-workflow.sh finalize --console-override` est un choix explicite, réservé au terminal interactif de la console fournisseur sans `SSH_CONNECTION`. Ce mode ne prouve **pas** la connectivité externe ni la clé client ; n'y recourez qu'après vérification indépendante et plan de récupération.

**Limites :** `SSH_CONNECTION` est recoupé avec une socket TCP établie, l'ascendance `sshd` et `SUDO_USER`, mais n'est pas une attestation cryptographique contre un administrateur root hostile. La politique SSH effective est vérifiée pour les contextes du client observé, de root et des deux ports ; elle ne couvre pas tous les clients/adresses possibles. Le script ne gère pas les sockets SSH installés, les ports multiples hérités, `AuthorizedKeysFile` non standard, les variantes d'unité personnalisées ni les pare-feu cloud. L'inventaire Docker couvre les publications TCP des conteneurs actifs connus du démon interrogé, pas les règles NAT externes/autres moteurs ou un conteneur démarré après le contrôle (course possible). Un crash pendant un `cp` de sauvegarde avant sa synchronisation impose l'examen manuel des artefacts ; ne restaurez jamais un fichier partiel. Aucun test de bascule sur hôte réel n'est inclus.

## Locale indépendante

Facultatif, sans lien avec la migration SSH : `sudo ./01-configuration-serveur/bin/locale.sh --apply --locale en_US.UTF-8 --timezone UTC` configure les valeurs indiquées si les outils de locale sont déjà installés. `--preset-fr` sélectionne explicitement `fr_FR.UTF-8` et `Europe/Paris` ; `--language-pack` demande en plus l'installation APT du paquet de langue. Il ne configure pas NTP et n'installe aucun paquet sans cette dernière option. Aucun pays ni fuseau n'est choisi implicitement.

Tests sans modification de l'hôte :

```bash
bash 01-configuration-serveur/bin/tests/test_ssh_workflow.sh
bash 01-configuration-serveur/bin/tests/test_locale.sh
```
