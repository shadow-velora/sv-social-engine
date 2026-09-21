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


def _token():
    tokp = os.path.join(ROOT, ".github-token")
    return open(tokp).read().strip() if os.path.exists(tokp) else os.environ.get("GH_PAT", "")


def runs_generate(tok, n=5):
    """Derniers runs de generate.yml (tous déclencheurs), du plus récent au plus ancien."""
    out = subprocess.check_output(
        ["curl", "-s", "-H", f"Authorization: token {tok}",
         f"https://api.github.com/repos/shadow-velora/sv-social-engine/actions/workflows/generate.yml/runs?per_page={n}"],
        timeout=30)
    return json.loads(out).get("workflow_runs", [])


def lot_deja_parti(runs, now, fenetre_h=40):
    """Le lot hebdo compte comme parti s'il existe un run de generate.yml (cron OU manuel) créé dans les
    `fenetre_h` dernières heures et qui n'a pas échoué (réussi, ou encore en cours).
    07/09/2026 (audit) : le cron tourne le DIMANCHE 16:23 UTC depuis le 10/08 ; comparer la date du dernier
    run à « aujourd'hui lundi » relançait un lot complet CHAQUE lundi → deux lots par semaine, budget doublé."""
    for r in runs:
        try:
            created = datetime.fromisoformat(str(r.get("created_at", "")).replace("Z", "+00:00"))
        except ValueError:
            continue
        if now - created > timedelta(hours=fenetre_h):
            continue
        if r.get("conclusion") == "success" or r.get("status") in ("in_progress", "queued", "waiting", "pending"):
            return True
    return False


def dispatch_generate(tok):
    subprocess.check_output(
        ["curl", "-s", "-X", "POST", "-H", f"Authorization: token {tok}",
         "https://api.github.com/repos/shadow-velora/sv-social-engine/actions/workflows/generate.yml/dispatches",
         "-d", '{"ref":"main"}'], timeout=30)


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

    # 0 pré. budget génération : ne JAMAIS s'arrêter en silence (incident 21/08→01/09)
    _gen = budget()
    _MAX = 200
    if _gen >= _MAX:
        ALERTES.append(f"🔴 PLAFOND BUDGET ATTEINT ({_gen}/{_MAX} générations) : la génération est BLOQUÉE jusqu'au 1er du mois — prévenir Laurie (recharge crédit Gemini ou attendre le reset)")
    elif _gen >= int(_MAX*0.8):
        ALERTES.append(f"⚠️ Budget génération à {_gen}/{_MAX} ce mois : le plafond approche")

    # 0 bis. crédit Gemini épuisé (sentinelle posée par generate_ai.gemini au 1er échec "credits depleted")
    if os.path.exists(os.path.join(ENGINE, "alerte-credit.json")):
        ALERTES.append("💳 CRÉDIT GEMINI ÉPUISÉ : plus aucune image ne peut être générée — recharge ~10£ sur aistudio.google.com/apikey (compte habituel), l'alerte s'efface seule au premier succès")

    # 0. dossiers incomplets dans pending (sans meta.json) → écartés (cause du crash publish du 02/08)
    for it in list(pending):
        dd = os.path.join(ROOT, "queue", "pending", it)
        if not os.path.exists(os.path.join(dd, "meta.json")):
            os.makedirs(os.path.join(ROOT, "queue", "rejected"), exist_ok=True)
            shutil.move(dd, os.path.join(ROOT, "queue", "rejected", it))
            pending.remove(it)
            ALERTES.append(f"Dossier incomplet écarté de la file : {it}")
            ACTIONS.append(f"{it} déplacé en rejected (meta.json manquant)")

    # 0 bis. lundi : le lot hebdo (cron du DIMANCHE soir) est-il bien parti ? (les crons GitHub sautent parfois)
    runs = None
    if now.weekday() == 0:
        tok = _token()
        if tok:
            try:
                runs = runs_generate(tok)
                if not lot_deja_parti(runs, now):
                    dispatch_generate(tok)
                    ALERTES.append("Lot hebdo du dimanche jamais parti (cron GitHub sauté) — relancé automatiquement")
                    ACTIONS.append("generate.yml déclenché à la main par l'inspectrice")
            except Exception as e:
                ALERTES.append(f"Impossible de vérifier le lot hebdo : {str(e)[:80]}")

    # 0 quater bis. contenus écartés par le publieur (déjà publié, carrousel incomplet, produit disparu) — 07/09/2026
    try:
        pp = os.path.join(ENGINE, "alertes-publication.json")
        if os.path.exists(pp):
            a = json.load(open(pp))
            if a.get("date", "") >= (now - timedelta(days=8)).strftime("%Y-%m-%d"):
                for x in a.get("alertes", [])[-5:]:
                    ALERTES.append(f"Publieur : {x.get('item', '?')[:45]} — {x.get('motif', '')}")
    except Exception:
        pass

    # 0 quater. alertes laissées par le dernier lot (briefs ignorés, lot de secours) — 07/09/2026
    try:
        ap = os.path.join(ENGINE, "alertes-generation.json")
        if os.path.exists(ap):
            a = json.load(open(ap))
            if a.get("date", "") >= (now - timedelta(days=8)).strftime("%Y-%m-%d"):
                for ligne in a.get("alertes", []):
                    ALERTES.append(f"Dernier lot ({a.get('date', '')[:16]}) : {ligne}")
    except Exception:
        pass

    # 0 ter. soir de publication (lundi/vendredi après 17h UTC) : le post est-il parti ?
    if now.weekday() in (0, 4) and now.hour >= 17:  # 21/09/2026 : lundi et vendredi
        psp = os.path.join(ENGINE, "publish-state.json")
        ps = json.load(open(psp)) if os.path.exists(psp) else {}
        if ps.get("derniere_publication") != now.strftime("%Y-%m-%d"):
            tok = _token()
            if tok:
                try:
                    subprocess.check_output(
                        ["curl", "-s", "-X", "POST", "-H", f"Authorization: token {tok}",
                         "https://api.github.com/repos/shadow-velora/sv-social-engine/actions/workflows/publish.yml/dispatches",
                         "-d", '{"ref":"main"}'], timeout=30)
                    ALERTES.append("Publication du soir jamais partie (cron sauté) — relancée automatiquement")
                    ACTIONS.append("publish.yml déclenché par l'inspectrice")
                except Exception as e:
                    ALERTES.append(f"Impossible de relancer la publication : {str(e)[:80]}")

    # 1. la file couvre-t-elle les 3 prochains créneaux ?
    posts_dispo = [p for p in pending + approved if "_reel_" not in p]
    if len(posts_dispo) < 2:
        ALERTES.append(f"File courte : {len(posts_dispo)} post(s) prêt(s) pour les prochains créneaux")
        if budget() < 80:  # seuil de prudence hérité du plafond 100 (le plafond réel est 200) — décision Laurie
            # relancer via le workflow generate.yml : environnement complet garanti
            # (l'exécution inline échouait ici — clé Magnific et ffmpeg absents du job inspection)
            tok = _token()
            try:
                if runs is None:
                    runs = runs_generate(tok)
                if lot_deja_parti(runs, now, fenetre_h=6):
                    ACTIONS.append("lot de complément NON demandé : un lot a tourné il y a moins de 6 h (pas d'empilement)")
                else:
                    dispatch_generate(tok)
                    ACTIONS.append("lot de complément demandé (génération complète dans ~15 min)")
            except Exception as e:
                ACTIONS.append(f"lot de complément ÉCHOUÉ : {str(e)[:60]}")

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
        tok = _token()
        if tok:
            out = subprocess.run(["curl", "-s", "-H", f"Authorization: Bearer {tok}",
                                  "https://api.github.com/repos/shadow-velora/sv-social-engine/actions/runs?per_page=6"],
                                 capture_output=True, timeout=30).stdout
            for r in json.loads(out).get("workflow_runs", []):
                if r["conclusion"] == "failure" and r["created_at"] > (now - timedelta(hours=36)).isoformat():
                    ALERTES.append(f"Robot en échec : « {r['name']} » ({r['created_at'][5:16]} UTC) — voir logs GitHub")
    except Exception:
        pass

    # 5. budget (+ appels juge, comptés depuis le 07/09 mais non plafonnés)
    b = budget()
    if b >= 170:
        ALERTES.append(f"Budget presque épuisé : {b}/200 générations ce mois — prévoir la recharge Gemini (~10 GBP)")
    try:
        _cc = json.load(open(os.path.join(ENGINE, "budget.json"))).get("check_calls")
        if _cc:
            ACTIONS.append(f"info : {_cc} appels au modèle juge ce mois (non plafonnés, facturés)")
    except Exception:
        pass

    rapport = {"date": now.isoformat(timespec="minutes"),
               "alertes": ALERTES, "actions_reparation": ACTIONS,
               "tout_va_bien": not ALERTES}
    json.dump(rapport, open(os.path.join(ENGINE, "rapport-inspectrice.json"), "w"),
              indent=2, ensure_ascii=False)
    print(json.dumps(rapport, ensure_ascii=False))


if __name__ == "__main__":
    main()
