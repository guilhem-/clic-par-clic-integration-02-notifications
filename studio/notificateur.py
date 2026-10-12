"""Studio 3D — notificateur : prévient l'utilisateur dans Open WebUI quand son rendu est fini.

Les workers restent isolés : à la fin d'un rendu, ils ajoutent seulement le numéro de la demande à
la liste Redis « studio:notifications ». Le notificateur, seul composant autorisé à joindre Open WebUI,
la lit et envoie un message direct au nom du compte robot « Studio 3D » : une notification apparaît
aussitôt chez l'utilisateur, avec l'image, l'objet 3D et la scène Blender du rendu.
"""
import json
import os
import time
import urllib.error
import urllib.request

import commun as c

# Réglages : adresse interne d'Open WebUI, compte robot (secret Kubernetes « studio-robot »).
OWUI = os.environ.get("OWUI_URL", "http://open-webui.terminals.svc.cluster.local:8080").rstrip("/")
EMAIL = os.environ["ROBOT_EMAIL"]
MOT_DE_PASSE = os.environ["ROBOT_MOT_DE_PASSE"]
REESSAI = 10  # secondes entre deux essais quand Open WebUI ne répond pas
r = c.connexion()
_jeton = {"valeur": None, "expire": 0.0}

TEXTES = {
    "FR": {"pret": "Votre rendu **n° {numero} — {titre}** est prêt.", "objet": "Objet 3D (GLB)",
           "sans_scene": "scène Blender indisponible",
           "suite": "Pour le manipuler en 3D, demandez « ouvre le rendu {numero} en 3D » à l'assistant Studio 3D.",
           "erreur": "Le rendu **n° {numero} — {titre}** a échoué. Demandez à l'assistant Studio 3D de le corriger."},
    "EN": {"pret": "Your render **#{numero} — {titre}** is ready.", "objet": "3D object (GLB)",
           "sans_scene": "Blender scene unavailable",
           "suite": "To handle it in 3D, ask the Studio 3D assistant to \"open render {numero} in 3D\".",
           "erreur": "Render **#{numero} — {titre}** failed. Ask the Studio 3D assistant to fix it."},
    "DE": {"pret": "Ihr Rendering **Nr. {numero} — {titre}** ist fertig.", "objet": "3D-Objekt (GLB)",
           "sans_scene": "Blender-Szene nicht verfügbar",
           "suite": "Um es in 3D zu bewegen, bitten Sie den Assistenten Studio 3D: „Öffne das Rendering {numero} in 3D“.",
           "erreur": "Das Rendering **Nr. {numero} — {titre}** ist fehlgeschlagen. Bitten Sie den Assistenten Studio 3D, es zu korrigieren."},
}
T = TEXTES.get(c.LANGUE, TEXTES["FR"])


def appel(methode: str, chemin: str, corps=None, jeton=None):
    """Appel de l'API d'Open WebUI (JSON), avec le jeton du robot s'il est donné."""
    requete = urllib.request.Request(f"{OWUI}{chemin}", method=methode,
                                     data=None if corps is None else json.dumps(corps).encode(),
                                     headers={"Content-Type": "application/json", **({"Authorization": f"Bearer {jeton}"} if jeton else {})})
    with urllib.request.urlopen(requete, timeout=20) as reponse:
        return json.loads(reponse.read() or b"null")


def jeton() -> str:
    """Jeton du robot : connexion par mot de passe, comme un utilisateur, renouvelée toutes les heures."""
    if not _jeton["valeur"] or time.time() > _jeton["expire"]:
        _jeton["valeur"] = appel("POST", "/api/v1/auths/signin", {"email": EMAIL, "password": MOT_DE_PASSE})["token"]
        _jeton["expire"] = time.time() + 3600
    return _jeton["valeur"]


def conversation(utilisateur_id: str) -> str:
    """Message direct entre le robot et l'utilisateur : créé une fois, gardé dans Redis."""
    cle = f"studio:dm:{utilisateur_id}"
    existant = c.texte(r.get(cle))
    if existant:
        return existant
    dm = appel("POST", "/api/v1/channels/create", {"name": "", "type": "dm", "user_ids": [utilisateur_id]}, jeton())
    r.set(cle, dm["id"])
    return dm["id"]


def message(numero: int, d: dict) -> str:
    """Texte du robot : le statut, l'image, puis, en dernier, les liens signés du studio (objet 3D, scène Blender)."""
    if d["statut"] != "termine":
        return T["erreur"].format(numero=numero, titre=d["titre"])
    liens = [f'[{T["objet"]}]({c.lien(numero, "glb")})']
    if d.get("scene"):  # scène déposée dans le stockage S3 (série studio3d, module 6)
        liens.append(f'[{c.LIBELLE_SCENE}]({c.lien(numero, "blend")})')
    elif d.get("scene_prevue") == "1":
        liens.append(T["sans_scene"])
    return "\n\n".join([T["pret"].format(numero=numero, titre=d["titre"]),
                        f'![{d["titre"]}]({c.lien(numero, "png")})',
                        T["suite"].format(numero=numero), " · ".join(liens)])


def poster(utilisateur_id: str, texte: str) -> None:
    """Écrit dans le message direct. Canal supprimé ou jeton périmé : on le recrée, une fois."""
    try:
        appel("POST", f"/api/v1/channels/{conversation(utilisateur_id)}/messages/post", {"content": texte, "data": {}}, jeton())
    except urllib.error.HTTPError as e:
        if e.code not in (401, 403, 404):
            raise
        r.delete(f"studio:dm:{utilisateur_id}")
        _jeton["valeur"] = None
        appel("POST", f"/api/v1/channels/{conversation(utilisateur_id)}/messages/post", {"content": texte, "data": {}}, jeton())


def prevenir(numero: int) -> None:
    """Prévient l'auteur de la demande `numero`, s'il est connu par son identifiant Open WebUI."""
    d = {c.texte(k): c.texte(v) for k, v in r.hgetall(c.demande(numero)).items()}
    if not d.get("utilisateur_id"):
        print(f"demande {numero} : identifiant d'utilisateur absent (en-tête X-Utilisateur-Id), pas de notification", flush=True)
        return
    poster(d["utilisateur_id"], message(numero, d))
    r.hset(c.demande(numero), "notifie", time.time())
    print(f"demande {numero} : {d['utilisateur']} prévenu", flush=True)


def main() -> None:
    """Une notification à la fois ; si Open WebUI ne répond pas, elle retourne dans Redis."""
    print("notificateur prêt", flush=True)
    while True:
        sortie = r.brpop([c.NOTIFICATIONS], timeout=5)  # attend un numéro, sans tourner à vide
        if sortie is None:
            continue
        numero = int(sortie[1])
        try:
            prevenir(numero)
        except Exception as e:
            print(f"demande {numero} : échec de la notification ({e}), nouvel essai dans {REESSAI} s", flush=True)
            r.rpush(c.NOTIFICATIONS, numero)  # remise du côté où l'on lit : la première servie au prochain tour
            time.sleep(REESSAI)


if __name__ == "__main__":
    main()
