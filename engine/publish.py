#!/usr/bin/env python3
"""
SV Social Engine — Publieur Instagram (API officielle Meta Graph).
Prend le plus ancien contenu de queue/approved/, le publie sur Instagram,
puis le déplace dans queue/published/.

Secrets attendus (variables d'environnement — GitHub Secrets) :
  IG_USER_ID      id du compte Instagram professionnel
  IG_ACCESS_TOKEN jeton longue durée (auto-prolongé à chaque run)
  ASSET_BASE_URL  URL publique brute du repo (raw.githubusercontent.com/...)
"""
import json
import os
import shutil
import sys
import time
import urllib.parse
import urllib.request

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
APPROVED = os.path.join(ROOT, "queue", "approved")
PUBLISHED = os.path.join(ROOT, "queue", "published")
GRAPH = "https://graph.instagram.com/v21.0"  # API Instagram Login (jamais graph.facebook.com avec ce token)

IG_USER = os.environ["IG_USER_ID"]
TOKEN = os.environ["IG_ACCESS_TOKEN"]
BASE = os.environ["ASSET_BASE_URL"].rstrip("/")


def api(path, params, method="POST"):
    params = {**params, "access_token": TOKEN}
    data = urllib.parse.urlencode(params).encode()
    url = f"{GRAPH}/{path}"
    if method == "GET":
        url += "?" + urllib.parse.urlencode(params)
        req = urllib.request.Request(url)
    else:
        req = urllib.request.Request(url, data=data)
    try:
        return json.loads(urllib.request.urlopen(req, timeout=60).read())
    except urllib.error.HTTPError as e:
        body = e.read().decode()
        raise RuntimeError(f"Graph API {e.code}: {body}") from e


def wait_ready(container_id, tries=40):
    for _ in range(tries):
        st = api(container_id, {"fields": "status_code"}, "GET")
        if st.get("status_code") == "FINISHED":
            return
        if st.get("status_code") == "ERROR":
            raise RuntimeError(f"container en erreur: {st}")
        time.sleep(5)
    raise RuntimeError("container jamais prêt")


def publish_item(folder):
    with open(os.path.join(folder, "meta.json")) as f:
        meta = json.load(f)
    rel = os.path.relpath(folder, ROOT).replace(os.sep, "/")
    caption = meta["caption"]
    kind = meta["type"]

    if kind == "reel":
        c = api(f"{IG_USER}/media", {
            "media_type": "REELS",
            "video_url": f"{BASE}/{urllib.parse.quote(rel)}/media.mp4",
            "caption": caption,
            "like_and_view_counts_disabled": "true",
        })
        wait_ready(c["id"])
        return api(f"{IG_USER}/media_publish", {"creation_id": c["id"]})

    if kind == "carousel":
        slides = sorted(f for f in os.listdir(folder) if f.startswith("slide-"))
        children = []
        for s in slides:
            c = api(f"{IG_USER}/media", {
                "image_url": f"{BASE}/{urllib.parse.quote(rel)}/{s}",
                "is_carousel_item": "true",
            })
            children.append(c["id"])
        c = api(f"{IG_USER}/media", {
            "media_type": "CAROUSEL",
            "children": ",".join(children),
            "caption": caption,
            "like_and_view_counts_disabled": "true",
        })
        wait_ready(c["id"])
        return api(f"{IG_USER}/media_publish", {"creation_id": c["id"]})

    # image simple
    c = api(f"{IG_USER}/media", {
        "image_url": f"{BASE}/{urllib.parse.quote(rel)}/media.jpg",
        "caption": caption,
        "like_and_view_counts_disabled": "true",
    })
    wait_ready(c["id"])
    return api(f"{IG_USER}/media_publish", {"creation_id": c["id"]})


def refresh_token():
    """Prolonge le jeton Instagram Login (60 j glissants) à chaque run,
    et met à jour le secret GitHub si GH_PAT est fourni."""
    try:
        r = api("refresh_access_token",
                {"grant_type": "ig_refresh_token"}, "GET")
    except Exception as e:
        print("refresh token: ignoré,", e)
        return
    new_tok = r.get("access_token")
    if not new_tok:
        return
    print("::add-mask::" + new_tok)
    print(f"jeton prolongé ({r.get('expires_in', 0) // 86400} jours)")
    pat, repo = os.environ.get("GH_PAT"), os.environ.get("GITHUB_REPOSITORY")
    if not (pat and repo) or new_tok == TOKEN:
        return
    try:
        import base64
        from nacl import encoding, public
        def gh(method, path, payload=None):
            req = urllib.request.Request(
                f"https://api.github.com{path}",
                data=json.dumps(payload).encode() if payload else None,
                method=method,
                headers={"Authorization": f"Bearer {pat}",
                         "Accept": "application/vnd.github+json"})
            body = urllib.request.urlopen(req, timeout=30).read()
            return json.loads(body) if body else {}
        pk = gh("GET", f"/repos/{repo}/actions/secrets/public-key")
        box = public.SealedBox(public.PublicKey(pk["key"].encode(), encoding.Base64Encoder()))
        enc = base64.b64encode(box.encrypt(new_tok.encode())).decode()
        gh("PUT", f"/repos/{repo}/actions/secrets/IG_ACCESS_TOKEN",
           {"encrypted_value": enc, "key_id": pk["key_id"]})
        print("secret IG_ACCESS_TOKEN mis à jour — rotation automatique OK")
    except Exception as e:
        print("rotation secret: ignorée,", e)


def final_check(folder):
    """Garde-fou : contrôle qualité IA avant de publier un contenu NON validé par Laurie.
    Ne bloque que les défauts graves. En cas de doute ou d'indisponibilité : laisse passer."""
    key = os.environ.get("GEMINI_API_KEY")
    if not key:
        return True, "pas de clé, contrôle sauté"
    media = os.path.join(folder, "media.jpg")
    if not os.path.exists(media):
        slides = sorted(f for f in os.listdir(folder) if f.startswith("slide"))
        if not slides:
            return False, "aucun média dans le dossier"
        media = os.path.join(folder, slides[0])
    import base64
    img = base64.b64encode(open(media, "rb").read()).decode()
    prompt = ("Tu es le contrôle qualité FINAL d'Inaya Paris (robes de soirée, élégance parisienne, "
              "tons profonds et lumières chaudes). Cette image va être publiée sur Instagram SANS validation humaine. "
              "Bloque UNIQUEMENT en cas de défaut grave : rendu peinture/illustration/3D évident, peau "
              "plastique de poupée, anomalie anatomique (mains, membres), gros texte plaqué au centre de "
              "l'image, image floue ou granuleuse, robe visiblement déformée ou incohérente. Un style "
              "éditorial propre, un packshot studio ou un lettrage de marque sur un objet (sac, boîte) "
              "sont NORMAUX et publiables. Réponds UNIQUEMENT en JSON : "
              '{"publiable": true/false, "raison": "1 phrase"}')
    body = json.dumps({"contents": [{"parts": [
        {"text": prompt},
        {"inline_data": {"mime_type": "image/jpeg", "data": img}}]}]}).encode()
    req = urllib.request.Request(
        "https://generativelanguage.googleapis.com/v1beta/models/gemini-3.6-flash:generateContent?key=" + key,
        data=body, headers={"Content-Type": "application/json"})
    try:
        r = json.loads(urllib.request.urlopen(req, timeout=90).read())
        txt = r["candidates"][0]["content"]["parts"][0]["text"]
        v = json.loads(txt[txt.find("{"):txt.rfind("}") + 1])
        return bool(v.get("publiable", True)), v.get("raison", "")
    except Exception as e:
        return True, f"contrôle indisponible ({e}), publication par défaut"


def main():
    os.makedirs(PUBLISHED, exist_ok=True)
    # RÈGLE DURE : maximum 1 publication par jour (védé du 29/07 — double post évité)
    from datetime import datetime as _dt
    psp = os.path.join(ROOT, "engine", "publish-state.json")
    ps = json.load(open(psp)) if os.path.exists(psp) else {}
    aujourdhui = _dt.utcnow().strftime("%Y-%m-%d")
    if ps.get("derniere_publication") == aujourdhui:
        print("Déjà publié aujourd'hui — règle 1/jour, on ne double pas.")
        sys.exit(0)
    # Modèle VETO : les posts validés par Laurie partent tels quels.
    # Un post NON validé passe d'abord le contrôle qualité IA (garde-fou) ;
    # s'il est bloqué, il part en rejected avec la raison et on essaie le suivant.
    pending = os.path.join(ROOT, "queue", "pending")
    rejected = os.path.join(ROOT, "queue", "rejected")
    os.makedirs(pending, exist_ok=True)
    os.makedirs(rejected, exist_ok=True)
    os.makedirs(APPROVED, exist_ok=True)

    approved = sorted(d for d in os.listdir(APPROVED)
                      if os.path.isdir(os.path.join(APPROVED, d)))
    candidates = ([(APPROVED, it, False) for it in approved]
                  + [(pending, it, True) for it in sorted(os.listdir(pending))
                     if os.path.isdir(os.path.join(pending, it))])
    # les réels sont des kits manuels (Laurie les poste avec un son tendance) : jamais auto-publiés
    candidates = [c for c in candidates if "_reel_" not in c[1]]
    if not candidates:
        print("File vide — rien à publier.")
        sys.exit(0)

    # ---- horaires : heure de Paris, personnalisés prioritaires, défauts sur créneau lun/mer/ven 19h ----
    from zoneinfo import ZoneInfo as _ZI
    _now_paris = _dt.now(_ZI("Europe/Paris"))
    maintenant = _now_paris.strftime("%Y-%m-%d %H:%M")
    _manuel = os.environ.get("GITHUB_EVENT_NAME", "") != "schedule"
    _creneau_defaut = _manuel or (_now_paris.weekday() in (0, 2, 4) and _now_paris.hour == 19)

    def _prog(c):
        try:
            return json.load(open(os.path.join(c[0], c[1], "meta.json"))).get("programme", "")
        except Exception:
            return ""
    _dus = [c for c in candidates if _prog(c) and _prog(c) <= maintenant]
    _defauts = [c for c in candidates if not _prog(c)]
    if _dus:
        candidates = sorted(_dus, key=_prog) + (_defauts if _creneau_defaut else [])
    elif _creneau_defaut:
        candidates = _defauts
    else:
        print(f"Hors créneau ({maintenant} Paris) et aucun horaire personnalisé dû — on ne publie pas.")
        sys.exit(0)
    if not candidates:
        print("Rien d'éligible sur ce créneau.")
        sys.exit(0)

    for source, item, needs_check in candidates:
        folder = os.path.join(source, item)
        if not os.path.exists(os.path.join(folder, "meta.json")):
            print(f"⚠️ {item} : meta.json manquant (dossier incomplet) — on passe au suivant.")
            continue
        _m = json.load(open(os.path.join(folder, "meta.json")))
        if _m.get("programme", "") and _m["programme"] > maintenant:
            print(f"🕐 {item} : programmé par Laurie pour le {_m['programme']} — on passe au suivant.")
            continue
        if _m.get("pas_avant", "") > aujourdhui:
            print(f"⏳ {item} : réservé pour le {_m['pas_avant']} — on passe au suivant.")
            continue
        if needs_check:
            ok, why = final_check(folder)
            if not ok:
                print(f"⛔ garde-fou : {item} bloqué — {why}")
                mp = os.path.join(folder, "meta.json")
                try:
                    meta = json.load(open(mp))
                    meta["rejet"] = f"Garde-fou automatique : {why}"
                    json.dump(meta, open(mp, "w"), indent=2, ensure_ascii=False)
                    with open(os.path.join(ROOT, "engine", "feedback.jsonl"), "a") as fb:
                        fb.write(json.dumps({"item": item,
                                             "raison": f"Garde-fou auto : {why}"},
                                            ensure_ascii=False) + "\n")
                except Exception:
                    pass
                shutil.move(folder, os.path.join(rejected, item))
                continue
            print(f"✅ garde-fou : ok — {why}")
        print("Publication de", item, "(source:", os.path.basename(source) + ")")
        res = publish_item(folder)
        print("OK, media id:", res.get("id"))
        shutil.move(folder, os.path.join(PUBLISHED, item))
        json.dump({"derniere_publication": aujourdhui}, open(psp, "w"))
        break
    else:
        print("Aucun contenu publiable aujourd'hui (tout bloqué par le garde-fou).")
    refresh_token()


if __name__ == "__main__":
    main()
