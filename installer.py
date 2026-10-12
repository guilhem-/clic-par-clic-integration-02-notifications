"""Prévenir l'utilisateur quand son rendu est prêt : compte robot, notificateur, en-tête USER_ID.

Fait ce que le module 2 de la série « Intégrations IA » montre à l'écran, sur l'état laissé par le
module 1 (connexion MCP « Studio 3D » et modèle « Studio 3D » déjà installés) :
  1. le compte robot « Studio 3D » dans Open WebUI : un compte utilisateur ordinaire, mot de passe
     long et généré (jamais affiché) ;
  2. le secret Kubernetes « studio-robot » (adresse et mot de passe du robot), puis le notificateur
     et ses règles réseau (studio/k8s-notifications.yaml) ;
  3. l'en-tête X-Utilisateur-Id: {{USER_ID}} ajouté à la connexion « Studio 3D » ;
  4. la phrase « Si le studio annonce que l'utilisateur sera prévenu, dis-lui qu'il recevra une notification dans Open WebUI. » ajoutée aux
     consignes du modèle « Studio 3D ».

Usage :
  python installer.py              # installe (ou remet le mot de passe du robot et redéploie)
  python installer.py --retirer    # retire le notificateur, le secret, le robot et l'en-tête

Réglages : fichier .env (voir .env.example) ; les variables d'environnement l'emportent.
Prérequis : Python 3.10 ou plus récent (bibliothèque standard), kubectl relié au cluster du studio.
"""
import json
import os
import pathlib
import secrets
import subprocess
import sys
import urllib.error
import urllib.request

ICI = pathlib.Path(__file__).resolve().parent
ROBOT = {"nom": "Studio 3D", "email": "studio-3d@example.com"}
ENTETE = {"X-Utilisateur-Id": "{{USER_ID}}"}
PHRASE = "Si le studio annonce que l'utilisateur sera prévenu, dis-lui qu'il recevra une notification dans Open WebUI."


def reglages() -> dict:
    """Lit .env (clé=valeur), puis l'environnement, qui l'emporte."""
    r = {"ESPACE": "studio3d", "KUBE_CONTEXTE": "", "ROBOT_EMAIL": ROBOT["email"]}
    env = ICI / ".env"
    if env.exists():
        for ligne in env.read_text(encoding="utf-8").splitlines():
            ligne = ligne.strip()
            if ligne and not ligne.startswith("#") and "=" in ligne:
                cle, valeur = ligne.split("=", 1)
                r[cle.strip()] = valeur.strip()
    for cle in ("OWUI_URL", "OWUI_JETON", "ESPACE", "KUBE_CONTEXTE", "ROBOT_EMAIL"):
        if os.environ.get(cle):
            r[cle] = os.environ[cle]
    return r


R = reglages()


def api(methode: str, chemin: str, corps=None):
    """Appel de l'API d'Open WebUI avec le jeton administrateur."""
    req = urllib.request.Request(R["OWUI_URL"].rstrip("/") + chemin, method=methode,
                                 data=None if corps is None else json.dumps(corps).encode(),
                                 headers={"Content-Type": "application/json", "Authorization": f"Bearer {R['OWUI_JETON']}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as rep:
            texte = rep.read().decode()
    except urllib.error.HTTPError as e:
        sys.exit(f"{methode} {chemin} : {e.code} {e.read().decode()[:300]}")
    return json.loads(texte) if texte else None


def kubectl(*args: str, entree: str | None = None) -> str:
    contexte = ["--context", R["KUBE_CONTEXTE"]] if R["KUBE_CONTEXTE"] else []
    return subprocess.run(["kubectl", *contexte, *args], input=entree, capture_output=True, text=True, check=True).stdout


# ---------------------------------------------------------------- 1. Le compte robot

def robot() -> str:
    """Crée le robot (ou remet son mot de passe) ; renvoie le mot de passe, jamais affiché."""
    mot_de_passe = secrets.token_urlsafe(24) + "A1"
    existant = next((u for u in api("GET", "/api/v1/users/?page=1")["users"] if u["email"] == R["ROBOT_EMAIL"]), None)
    if existant is None:
        api("POST", "/api/v1/auths/add", {"name": ROBOT["nom"], "email": R["ROBOT_EMAIL"], "password": mot_de_passe, "role": "user"})
        print(f"compte robot « {ROBOT['nom']} » créé (rôle : utilisateur)")
    else:
        api("POST", f"/api/v1/users/{existant['id']}/update", {"name": ROBOT["nom"], "email": R["ROBOT_EMAIL"], "role": "user",
                                                                "profile_image_url": existant.get("profile_image_url") or "/user.png",
                                                                "password": mot_de_passe})
        print(f"compte robot « {ROBOT['nom']} » : mot de passe renouvelé")
    return mot_de_passe


# ---------------------------------------------------------------- 2. Le notificateur dans le cluster

def notificateur(mot_de_passe: str) -> None:
    """Secret « studio-robot » (passé à kubectl par l'entrée standard, jamais en argument), puis le manifeste."""
    secret = {"apiVersion": "v1", "kind": "Secret", "type": "Opaque",
              "metadata": {"name": "studio-robot", "namespace": R["ESPACE"]},
              "stringData": {"email": R["ROBOT_EMAIL"], "mot_de_passe": mot_de_passe}}
    print(kubectl("apply", "-f", "-", entree=json.dumps(secret)).strip())
    print(kubectl("apply", "-f", str(ICI / "studio" / "k8s-notifications.yaml")).strip())
    print(kubectl("rollout", "restart", "deployment/studio-notificateur", "-n", R["ESPACE"]).strip())
    print(kubectl("rollout", "status", "deployment/studio-notificateur", "-n", R["ESPACE"], "--timeout=120s").strip())


# ---------------------------------------------------------------- 3 et 4. Connexion et consignes

def entete(ajouter: bool) -> None:
    conf = api("GET", "/api/v1/configs/tool_servers")
    for c in conf["TOOL_SERVER_CONNECTIONS"]:
        if c.get("info", {}).get("id") == "studio":
            entetes = {k: v for k, v in (c.get("headers") or {}).items() if k not in ENTETE}
            c["headers"] = {**entetes, **ENTETE} if ajouter else entetes
            api("POST", "/api/v1/configs/tool_servers", conf)
            print("en-tête X-Utilisateur-Id", "ajouté" if ajouter else "retiré")
            return
    sys.exit("connexion « Studio 3D » absente : installez d'abord le module 1")


def consignes(ajouter: bool) -> None:
    m = api("GET", "/api/v1/models/model?id=studio-3d")
    texte = "\n".join(l for l in m["params"].get("system", "").splitlines() if l.strip() != PHRASE)
    m["params"]["system"] = f"{texte}\n{PHRASE}" if ajouter else texte
    api("POST", "/api/v1/models/model/update", m)
    print("consigne de notification", "ajoutée" if ajouter else "retirée")


def retirer() -> None:
    kubectl("delete", "deployment/studio-notificateur", "networkpolicy/notificateur-vers-redis-et-open-webui",
            "networkpolicy/redis-notificateur", "secret/studio-robot", "-n", R["ESPACE"], "--ignore-not-found")
    for u in api("GET", "/api/v1/users/?page=1")["users"]:
        if u["email"] == R["ROBOT_EMAIL"]:
            api("DELETE", f"/api/v1/users/{u['id']}")
    entete(False)
    consignes(False)
    print("notificateur, secret et compte robot retirés")


if __name__ == "__main__":
    for cle in ("OWUI_URL", "OWUI_JETON"):
        if not R.get(cle):
            sys.exit(f"réglage manquant : {cle} (voir .env.example)")
    if "--retirer" in sys.argv:
        retirer()
    else:
        notificateur(robot())
        entete(True)
        consignes(True)
