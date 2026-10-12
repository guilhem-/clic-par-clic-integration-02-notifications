# Prévenir l'utilisateur quand son rendu est prêt — notificateur, compte robot, en-tête USER_ID

Code et configuration de la démonstration du module 2 de la série « Clic par clic » **Intégrations IA**
(Open WebUI relié au studio 3D de l'organisation).

Vidéo : {{video}}

## But

Au module 1, l'utilisatrice devait demander à l'assistant si son rendu était prêt. Ici, le studio la
**prévient lui-même** : à la fin d'un rendu, un composant dédié, le **notificateur**, écrit un **message
direct** dans Open WebUI au nom d'un **compte robot** « Studio 3D ». Open WebUI affiche aussitôt une
notification ; le message contient l'image, le lien de l'objet 3D (GLB) et celui de la scène Blender.

```
worker Blender ──numéro──▶ Redis « studio:notifications » ──▶ notificateur ──API, au nom du robot──▶ Open WebUI ──▶ notification
```

Contenu, à l'état montré en fin de vidéo :

| Fichier | Rôle |
|---|---|
| `studio/notificateur.py` | Le notificateur : connexion du robot (`jeton`), message direct créé une fois par utilisateur et gardé dans Redis (`conversation`), texte avec les liens signés du studio (`message`), boucle qui attend un numéro (`brpop`) et le remet dans Redis si Open WebUI ne répond pas (nouvel essai dix secondes plus tard) |
| `studio/k8s-notifications.yaml` | Déploiement (une seule copie, image du studio, autre commande, compte du robot lu dans le secret `studio-robot`) et règles réseau : le notificateur, et lui seul, joint Open WebUI ; les workers restent limités à Redis |
| `open-webui/connexion-studio.json` | La connexion MCP du module 1, avec le second en-tête `X-Utilisateur-Id: {{USER_ID}}` |
| `open-webui/consignes-modele.txt` | Les consignes du module 1, plus une phrase : « Si le studio annonce que l'utilisateur sera prévenu, dis-lui qu'il recevra une notification dans Open WebUI. » |
| `installer.py` | Tout ce qui précède, par l'API d'Open WebUI et `kubectl` (`--retirer` pour revenir en arrière) |

## Pourquoi un composant à part ?

Les workers exécutent du code écrit par un modèle de langage : ils ne joignent que Redis. Leur ouvrir
Open WebUI, ce serait offrir une porte à ce code. Le notificateur n'exécute que son propre code ; il est
le seul à pouvoir joindre Open WebUI (règle réseau `notificateur-vers-redis-et-open-webui`).

## À qui écrire ?

Le studio connaît l'adresse de l'utilisateur (`X-Utilisateur-Email`, module 1), pas son identifiant
dans Open WebUI, qu'il faut pour ouvrir un message direct. La connexion le transmet avec l'en-tête
`X-Utilisateur-Id: {{USER_ID}}`, qu'Open WebUI remplit à chaque appel. Le studio du module 6 de la série
Studio 3D le lit déjà (`api.py` : champ `utilisateur_id` de la demande) et son worker ajoute le numéro
à `studio:notifications` à la fin du rendu, après le dépôt de la scène dans le stockage S3. Une demande
faite sans cet en-tête n'est pas notifiée.

## Prérequis

- L'état de fin du **module 1** : dépôt [clic-par-clic-integration-01-brancher-studio](https://github.com/guilhem-/clic-par-clic-integration-01-brancher-studio)
  (connexion MCP « Studio 3D », modèle « Studio 3D », groupe « Graphistes »).
- Le studio 3D dans sa dernière version, déployé dans le même cluster qu'Open WebUI : dépôt
  [clic-par-clic-studio3d-06-stockage-s3](https://github.com/guilhem-/clic-par-clic-studio3d-06-stockage-s3)
  (image `studio3d:5`, Redis, workers, stockage S3).
- Open WebUI 0.11 (tourné sur 0.11.3) avec les canaux activés (Réglages d'administration › Général ›
  Canaux), un compte **administrateur** et son jeton.
- `kubectl` relié au cluster, Python 3.10 ou plus récent (bibliothèque standard seulement).

## Installation pas à pas

### 1. Le notificateur dans l'image du studio

Le notificateur tourne dans l'image du studio, avec une autre commande. Dans le dépôt du module 6 de
la série Studio 3D, copiez `studio/notificateur.py` dans le dossier `studio/`, ajoutez-le à la ligne
`COPY` du `Dockerfile`, puis reconstruisez l'image :

```bash
cp studio/notificateur.py ../clic-par-clic-studio3d-06-stockage-s3/studio/
# Dockerfile : COPY commun.py api.py travailleur.py … lire_scene.py notificateur.py ./
docker build -t studio3d:5 ../clic-par-clic-studio3d-06-stockage-s3/studio
k3d image import studio3d:5 -c <votre cluster>     # avec k3d ; sinon, poussez l'image dans votre registre
```

### 2. À la main, comme dans la vidéo

1. **Panneau d'administration › Utilisateurs › Ajouter un utilisateur** : rôle « utilisateur », nom
   « Studio 3D », adresse `studio-3d@example.com`, un mot de passe long et généré.
2. Le secret et le notificateur (le mot de passe est lu dans une variable, jamais écrit dans un fichier) :
   ```bash
   kubectl create secret generic studio-robot -n studio3d \
     --from-literal=email=studio-3d@example.com --from-literal=mot_de_passe="$ROBOT_MOT_DE_PASSE"
   kubectl apply -f studio/k8s-notifications.yaml
   kubectl logs -n studio3d deploy/studio-notificateur     # « notificateur prêt »
   ```
3. **Réglages d'administration › Intégrations** › roue de la connexion « Studio 3D » › **Avancé** ›
   En-têtes HTTP : `{"X-Utilisateur-Email": "{{USER_EMAIL}}", "X-Utilisateur-Id": "{{USER_ID}}"}` ;
   **Enregistrer**.
4. **Espace de travail › Modèles › Studio 3D** : à la fin du prompt système, la phrase « Si le studio
   annonce que l'utilisateur sera prévenu, dis-le-lui. » ; **Enregistrer & Mettre à jour**.

### 3. Ou par le script

```bash
cp .env.example .env          # OWUI_URL, OWUI_JETON, ESPACE, KUBE_CONTEXTE
python installer.py           # robot, secret, notificateur, en-tête, consigne
python installer.py --retirer # revient à l'état du module 1
```

Le script génère le mot de passe du robot, le passe à `kubectl` par l'entrée standard et ne l'affiche
jamais.

## Essai

Avec le compte d'un membre du groupe « Graphistes », modèle « Studio 3D » :

1. « Crée une scène de salon avec le fauteuil de la bibliothèque, sous l'éclairage salon bois. » :
   demande numéro 1, l'assistant annonce que vous serez prévenu.
2. « Et un vase en céramique sur une table, sous le même éclairage. » : demande numéro 2.
3. Passez à autre chose (nouvelle conversation) : une notification « Studio 3D » apparaît ; un clic
   ouvre le message direct, avec l'image, l'objet 3D et le lien « Télécharger la scène Blender ».

Si Open WebUI est indisponible, le journal du notificateur affiche « échec de la notification…,
nouvel essai dans 10 s » et le numéro reste dans Redis jusqu'à l'envoi.

## Sécurité

- Le robot est un compte **utilisateur ordinaire**, sans droits d'administration : il ne peut
  qu'écrire des messages directs.
- Son mot de passe n'est connu que du secret `studio-robot`, lu par le seul notificateur.
- Les liens du message sont ceux du studio, signés ; la scène Blender passe par un lien présigné d'une
  minute, demandé à chaque clic.
- Aucun secret dans ce dépôt (`.env` est ignoré par git).

## Série

- Module précédent : **Brancher le studio 3D de l'organisation à Open WebUI** —
  [clic-par-clic-integration-01-brancher-studio](https://github.com/guilhem-/clic-par-clic-integration-01-brancher-studio).
- Module suivant : **Un widget de suivi dans la conversation** —
  [clic-par-clic-integration-03-widget-suivi](https://github.com/guilhem-/clic-par-clic-integration-03-widget-suivi).
- Construire le studio : série « Créer un service d'IA compatible agentique : le studio 3D »
  (dépôts `clic-par-clic-studio3d-01` à `06`).

## Licence

MIT (voir `LICENSE`).
