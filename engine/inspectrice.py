#!/usr/bin/env python3
"""
L'INSPECTRICE — contrôle qualité quotidien du système (gratuit, aucun appel IA).
Vérifie ce que Laurie ne devrait JAMAIS avoir à vérifier elle-même :
trous de programme, carrousel hebdo manquant, monotonie couleur, kits absents,
échecs des robots. Écrit engine/rapport-inspectrice.json (bandeau Cockpit)
et répare seule ce qu'elle peut réparer.
"""
import json
import os
import subprocess
import sys
from datetime import datetime, timedelta, timezone

ENGINE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(ENGINE)
ALERTES, ACTIONS = [], []


def contenus(dossier):
    base = os.path.join(ROOT, "queue", dossier)
    if not os.path.isdir(base):
        return []
    return sorted(d for d in os.listdir(base)
                  if os.path.isdir(os.path.join(base, d)))


def budget():
    bp = os.path.join(ENGINE, "budget.json")
    b = json.load(open(bp)) if os.path.exists(bp) else {}
    mois = datetime.now(timezone.utc).strftime("%Y-%m")
    return b.get("gen_calls", 0) if b.get("gen_month") == mois else 0


def teinte_moyenne(media):
    from PIL import Image
    im = Image.open(media).convert("HSV").resize((60, 75))
    px = list(im.getdata())
    hs = [p[0] for p in px if p[1] > 40 and p[2] > 40]
    return (sum(hs) / len(hs)) if hs else None


def main():
    import shutil
    now = datetime.now(timezone.utc)
    pending = contenus("pending")
    approved = contenus("approved")
    publies = contenus("published")

    # 0. dossiers incomplets dans pending (sans meta.json) → écartés (cause du crash publish du 02/08)
    for it in list(pending):
        dd = os.path.join(ROOT, "queue", "pending", it)
        if not os.path.exists(os.path.join(dd, "meta.json")):
            os.makedirs(os.path.join(ROOT, "queue", "rejected"), exist_ok=True)
            shutil.move(dd, os.path.join(ROOT, "queue", "rejected", it))
            pending.remove(it)
            ALERTES.append(f"Dossier incomplet écarté de la file : {it}")
            ACTIONS.append(f"{it} déplacé en rejected (meta.json manquant)")

    # 0 bis. lundi : le lot hebdo est-il bien parti ? (les crons GitHub sautent parfois)
    if now.weekday() == 0:
        tokp = os.path.join(ROOT, ".github-token")
        tok = open(tokp).read().strip() if os.path.exists(tokp) else os.environ.get("GH_PAT", "")
        if tok:
            try:
                out = subprocess.check_output(
                    ["curl", "-s", "-H", f"Authorization: token {tok}",
                     "https://api.github.com/repos/shadow-velora/sv-social-engine/actions/workflows/generate.yml/runs?per_page=1"],
                    timeout=30)
                runs = json.loads(out).get("workflow_runs", [])
                if not runs or runs[0]["created_at"][:10] != now.strftime("%Y-%m-%d"):
                    subprocess.check_output(
                        ["curl", "-s", "-X", "POST", "-H", f"Authorization: token {tok}",
                         "https://api.github.com/repos/shadow-velora/sv-social-engine/actions/workflows/generate.yml/dispatches",
                         "-d", '{"ref":"main"}'], timeout=30)
                    ALERTES.append("Lot du lundi jamais parti (cron GitHub sauté) — relancé automatiquement")
                    ACTIONS.append("generate.yml déclenché à la main par l'inspectrice")
            except Exception as e:
                ALERTES.append(f"Impossible de vérifier le lot du lundi : {str(e)[:80]}")

    # 1. la file couvre-t-elle les 3 prochains créneaux ?
    posts_dispo = [p for p in pending + approved if "_reel_" not in p]
    if len(posts_dispo) < 2:
        ALERTES.append(f"File courte : {len(posts_dispo)} post(s) prêt(s) pour les prochains créneaux")
        if budget() < 80:  # au moins 20% du plafond 100 restant
            r = subprocess.run([sys.executable, os.path.join(ENGINE, "generate_ai_lot.py")],
                               cwd=ROOT, timeout=3000, capture_output=True)
            ACTIONS.append("lot de complément lancé" if r.returncode == 0 else "lot de complément ÉCHOUÉ")

    # 2. deux carrousels cette semaine (publiés ou en file) ? — règle fondatrice 03/08
    semaine = (now - timedelta(days=7)).strftime("%Y-%m-%d")
    caro = [p for p in pending + approved + publies
            if "carousel" in p and p[:10] >= semaine]
    if len(caro) < 2:
        ALERTES.append(f"{len(caro)} carrousel(s) cette semaine (règle : 2/semaine, 1 post + 2 carrousels)")
        if budget() < 95:  # le carrousel hebdo est une règle DURE, priorité sur la prudence (plafond 100)
            r = subprocess.run([sys.executable, os.path.join(ENGINE, "make_carousel_week.py")],
                               cwd=ROOT, timeout=3000, capture_output=True)
            ACTIONS.append("carrousel muse lancé" if r.returncode == 0 else "carrousel tenté mais recalé (fidélité robes)")

    # 3. monotonie couleur sur les 6 prochains posts
    teintes = []
    for p in (approved + pending)[:6]:
        m = os.path.join(ROOT, "queue", "pending", p, "media.jpg")
        if not os.path.exists(m):
            m = os.path.join(ROOT, "queue", "approved", p, "media.jpg")
        if os.path.exists(m):
            t = teinte_moyenne(m)
            if t is not None:
                teintes.append(t)
    if len(teintes) >= 3 and (max(teintes) - min(teintes)) < 18:
        ALERTES.append("Monotonie couleur : les prochains posts sont tous dans les mêmes tons — varier les mondes couleur")

    # 4. échecs récents des robots GitHub
    try:
        tokp = os.path.join(ROOT, ".github-token")
        tok = open(tokp).read().strip() if os.path.exists(tokp) else os.environ.get("GH_PAT", "")
        if tok:
            out = subprocess.run(["curl", "-s", "-H", f"Authorization: Bearer {tok}",
                                  "https://api.github.com/repos/shadow-velora/sv-social-engine/actions/runs?per_page=6"],
                                 capture_output=True, timeout=30).stdout
            for r in json.loads(out).get("workflow_runs", []):
                if r["conclusion"] == "failure" and r["created_at"] > (now - timedelta(hours=36)).isoformat():
                    ALERTES.append(f"Robot en échec : « {r['name']} » ({r['created_at'][5:16]} UTC) — voir logs GitHub")
    except Exception:
        pass

    # 5. budget
    b = budget()
    if b >= 110:
        ALERTES.append(f"Budget presque épuisé : {b}/120 générations ce mois")

    rapport = {"date": now.isoformat(timespec="minutes"),
               "alertes": ALERTES, "actions_reparation": ACTIONS,
               "tout_va_bien": not ALERTES}
    json.dump(rapport, open(os.path.join(ENGINE, "rapport-inspectrice.json"), "w"),
              indent=2, ensure_ascii=False)
    print(json.dumps(rapport, ensure_ascii=False))


if __name__ == "__main__":
    main()
