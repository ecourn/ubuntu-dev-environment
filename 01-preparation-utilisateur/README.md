# Étape 1 — Créer le compte `ubuntu`

Depuis la session administrateur initiale, après avoir cloné le dépôt comme indiqué dans le [parcours principal](../README.md) :

```bash
cd ~/ubuntu-dev-environment
sudo bash ./01-preparation-utilisateur/setup-ubuntu-user.sh
```

Le script crée ou vérifie `ubuntu` et installe une clé publique OpenSSH dans `authorized_keys`. Si une clé valide existe déjà pour le compte initial, il peut la reprendre. Sinon, collez le champ « Public key for pasting into OpenSSH authorized_keys file » de PuTTYgen quand il le demande. **La clé privée `.ppk` reste sur votre PC.**

Le nouveau compte n’a pas de mot de passe de connexion : utilisez sa clé. Gardez la session initiale ouverte et testez une nouvelle connexion PuTTY depuis votre PC avec `ubuntu` et la `.ppk` correspondante. Si elle fonctionne, continuez avec l’[étape 2](../02-configuration-serveur/README.md).
