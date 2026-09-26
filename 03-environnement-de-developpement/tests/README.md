# Tests unitaires

Ces tests vérifient la logique de l’installateur Python, par exemple la détection des prérequis, la sélection des versions et le traitement des erreurs. Ils ne font pas partie des étapes d’installation du serveur et n’installent ni paquets ni outils sur la machine.

La CI compile le code Python puis exécute ces tests. Pour les lancer depuis ce dossier sur une machine de développement :

```bash
python3 -m unittest discover -s tests -v
```
