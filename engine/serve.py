#!/usr/bin/env python3
"""
SV Cockpit — interface locale de contrôle de l'usine sociale.
Lancement : double-clic sur "SV Cockpit.command" (ou python3 engine/serve.py)
puis http://localhost:8765
"""
import json
import sys
import re
import os
import shutil
import subprocess
import threading
import urllib.parse
from http.server import HTTPServer, SimpleHTTPRequestHandler

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENGINE = os.path.join(ROOT, "engine")
Q = lambda name: os.path.join(ROOT, "queue", name)
for name in ("pending", "approved", "published", "rejected"):
    os.makedirs(Q(name), exist_ok=True)

PORT = 8765
_gen_lock = threading.Lock()
_gen_running = False


def list_state(state):
    out = []
    base = Q(state)
    for it in sorted(os.listdir(base)):
        d = os.path.join(base, it)
        mp = os.path.join(d, "meta.json")
        if not os.path.isdir(d) or not os.path.exists(mp):
            continue
        meta = json.load(open(mp))
        media = sorted(f for f in os.listdir(d)
                       if f.startswith(("media", "slide")))  # les brouillons cand-* restent sur disque mais ne s'affichent plus
        retirees = sorted(f for f in os.listdir(d) if f.startswith("retiree-"))  # slides retirées par Laurie (07/09)
        out.append({
            "id": it, "state": state, "type": meta.get("type", "?"),
            "caption": meta.get("caption", ""),
            "alerte_da": meta.get("alerte_da"),
            "recette": meta.get("recette"), "plans": meta.get("plans"),
            "media": [f"/queue/{state}/{it}/{m}?v={int(os.path.getmtime(os.path.join(d, m)))}" for m in media],
            "media_files": media,
            "retirees": [{"file": r, "url": f"/queue/{state}/{it}/{r}?v={int(os.path.getmtime(os.path.join(d, r)))}"} for r in retirees],
        })
    return out


def run_generate():
    global _gen_running
    with _gen_lock:
        if _gen_running:
            return
        _gen_running = True
    try:
        env = dict(os.environ)
        if "FFMPEG" not in env:
            for cand in (os.path.join(ROOT, "bin", "ffmpeg"),
                         shutil.which("ffmpeg") or "", "/opt/homebrew/bin/ffmpeg"):
                if cand and os.path.exists(cand):
                    env["FFMPEG"] = cand
                    break
        script = "generate_ai_lot.py" if getattr(run_generate, "ai_mode", False) else "generate.py"
        if getattr(run_generate, "produit", ""):
            env["GEN_PRODUIT"] = run_generate.produit
            run_generate.produit = ""
        subprocess.run(["python3", os.path.join(ENGINE, script)],
                       env=env, cwd=ROOT, timeout=3600)
        # l'équipe se réunit automatiquement après chaque lot
        subprocess.run(["python3", os.path.join(ENGINE, "committee.py")],
                       env=env, cwd=ROOT, timeout=600)
        _git_sync("génération locale")
    finally:
        _gen_running = False


def _dispatch_workflow(name):
    """Déclenche un robot GitHub (workflow_dispatch). Renvoie (ok, erreur)."""
    import urllib.request as _ur
    r = subprocess.run(["git", "remote", "get-url", "origin"],
                       cwd=ROOT, capture_output=True, text=True)
    m = re.search(r"x-access-token:([^@]+)@github\.com/([^/]+/[^/.]+)", r.stdout.strip())
    if not m:
        return False, "accès GitHub introuvable"
    req = _ur.Request(
        f"https://api.github.com/repos/{m.group(2)}/actions/workflows/{name}/dispatches",
        data=json.dumps({"ref": "main"}).encode(),
        headers={"Authorization": f"token {m.group(1)}",
                 "Accept": "application/vnd.github+json"})
    try:
        _ur.urlopen(req, timeout=30)
        return True, None
    except Exception:
        return False, "GitHub injoignable — vérifier la connexion internet"


_git_lock = threading.Lock()


def _date_paris():
    """Date du jour en heure de Paris — la même que publish.py pour le verrou « 1 publication / jour »."""
    from datetime import datetime as _d
    from zoneinfo import ZoneInfo as _Z
    return _d.now(_Z("Europe/Paris")).strftime("%Y-%m-%d")

# 06/09/2026 : « Actualiser depuis Instagram » semblait ne rien faire (Laurie) — la chaîne GitHub →
# autopull (2 min) marchait mais restait muette 2 à 6 min. Désormais : suivi d'étapes + pull rapide.
FEEDSYNC = {"en_cours": False, "etape": "", "debut": 0, "fin": 0, "resultat": ""}


def _feedsync_suivi(maj_avant, head_avant):
    import time as _tm
    fp = os.path.join(ROOT, "engine", "feed-instagram.json")
    FEEDSYNC.update(en_cours=True, etape="1/3 · GitHub interroge Instagram", debut=_tm.time(), fin=0, resultat="")
    deadline = _tm.time() + 420
    n = 0
    while _tm.time() < deadline:
        _tm.sleep(15)
        n += 1
        if n >= 3:
            FEEDSYNC["etape"] = "2/3 · récupération du feed"
        try:
            with _git_lock:
                subprocess.run(["git", "pull", "--rebase", "--autostash"],
                               cwd=ROOT, timeout=90, capture_output=True, text=True)
            head = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
            msg = subprocess.run(["git", "log", "-1", "--format=%s"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
        except Exception:
            head, msg = head_avant, ""
        try:
            maj = json.load(open(fp)).get("maj", "")
        except Exception:
            maj = ""
        if maj and maj != maj_avant:
            FEEDSYNC.update(en_cours=False, etape="3/3 · feed à jour", fin=_tm.time(), resultat="ok")
            return
        if head != head_avant and msg.startswith("inspection"):
            FEEDSYNC.update(en_cours=False, etape="3/3 · rien de nouveau", fin=_tm.time(), resultat="identique")
            return
    FEEDSYNC.update(en_cours=False, etape="", fin=_tm.time(), resultat="timeout")


def _git_sync(message):
    """Chaque action Cockpit (approve/reject/swap/...) est poussée sur GitHub immédiatement.
    Sans ça, les clics de Laurie restent locaux et les workflows GitHub travaillent sur un
    état périmé (ex: post approuvé localement mais toujours pending côté robot)."""
    with _git_lock:
        subprocess.run(["git", "add", "-A", "queue", "engine/feedback.jsonl", "engine/plan-semaine.json",
                        "engine/ordre-historique.json"],
                       cwd=ROOT, timeout=60, capture_output=True)
        diff = subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT, timeout=60)
        if diff.returncode == 0:
            return
        subprocess.run(["git", "commit", "-m", f"cockpit: {message}"],
                       cwd=ROOT, timeout=60, capture_output=True)
        for _ in range(3):
            subprocess.run(["git", "pull", "--rebase", "--autostash"],
                           cwd=ROOT, timeout=120, capture_output=True)
            push = subprocess.run(["git", "push"], cwd=ROOT, timeout=120, capture_output=True)
            if push.returncode == 0:
                return


def _renumeroter(state):
    """Donne à chaque dossier d'une file un horodatage UNIQUE et croissant (ordre actuel conservé).
    Renvoie {ancien_nom: nouveau_nom}. Retire aussi les suffixes « 2 » que macOS ajoute aux doublons."""
    base = Q(state)
    noms = sorted(d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d)) and d.count("_") >= 2)
    if not noms:
        return {}
    date = noms[0].split("_", 1)[0]
    ren = {}
    for i, old in enumerate(noms, 1):
        reste = old.split("_", 2)[2].strip()
        reste = re.sub(r" \d+$", "", reste)  # « …gown 2 » → « …gown »
        new = f"{date}_{i * 100 + 100000:06d}_{reste}"
        if new != old:
            if os.path.exists(os.path.join(base, new)):
                new = f"{date}_{i * 100 + 100000 + 1:06d}_{reste}"
            os.rename(os.path.join(base, old), os.path.join(base, new))
        ren[old] = new
    return ren


def _git_sync_bg(message):
    threading.Thread(target=_git_sync, args=(message,), daemon=True).start()


def _autopull():
    """Le Cockpit reste TOUJOURS synchronisé avec le vrai état (GitHub) : pull toutes les 2 min."""
    import time
    # 03/09/2026 : sans try/except, un seul timeout réseau tuait ce thread en silence et le Cockpit
    # restait des jours en retard (feed Instagram non actualisé après un post). Désormais : on
    # journalise et on continue, quoi qu'il arrive.
    log_path = os.path.join(ROOT, "engine", "autopull.log")
    while True:
        time.sleep(120)
        try:
            with _git_lock:
                r = subprocess.run(["git", "pull", "--rebase", "--autostash"],
                                   cwd=ROOT, timeout=120, capture_output=True, text=True)
            if r.returncode != 0:
                with open(log_path, "a") as lf:
                    lf.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} pull rc={r.returncode} {(r.stderr or '')[-300:].strip()}\n")
        except Exception as e:
            try:
                with open(log_path, "a") as lf:
                    lf.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} autopull exception: {type(e).__name__}: {e}\n")
            except Exception:
                pass


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *a, **kw):
        super().__init__(*a, directory=ROOT, **kw)

    def end_headers(self):
        # jamais de cache : Laurie doit toujours voir la dernière version du Cockpit
        self.send_header("Cache-Control", "no-store, must-revalidate")
        self.send_header("Expires", "0")
        super().end_headers()

    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/" or self.path.startswith("/index"):
            self.path = "/engine/cockpit.html"
            return super().do_GET()
        if self.path == "/api/programme":
            from datetime import datetime as _dt, timedelta as _td
            NOMS = ["lundi", "mardi", "mercredi", "jeudi", "vendredi", "samedi", "dimanche"]
            MOIS = ["", "janvier", "février", "mars", "avril", "mai", "juin", "juillet",
                    "août", "septembre", "octobre", "novembre", "décembre"]
            # la file complète (approved puis pending), avec vignette + horaire personnalisé éventuel
            file_posts = []
            for st in ("approved", "pending"):
                for it in sorted(os.listdir(Q(st))):
                    d = os.path.join(Q(st), it)
                    if os.path.isdir(d) and os.path.exists(os.path.join(d, "meta.json")) and "_reel_" not in it:
                        media = os.path.join(d, "media.jpg")
                        if not os.path.exists(media):
                            sl = sorted(f for f in os.listdir(d) if f.startswith("slide"))
                            media = os.path.join(d, sl[0]) if sl else None
                        if media:
                            meta = json.load(open(os.path.join(d, "meta.json")))
                            file_posts.append({
                                "state": st, "id": it,
                                "thumb": f"/queue/{st}/{urllib.parse.quote(it)}/{os.path.basename(media)}?v={int(os.path.getmtime(media))}",
                                "programme": meta.get("programme", ""),
                                "attente": st == "pending"})
            today = _dt.now().date()
            programmes = [p for p in file_posts if p["programme"]]
            defauts = [p for p in file_posts if not p["programme"]]
            rows, slot = [], 0
            psp = os.path.join(ENGINE, "publish-state.json")
            deja_pub = ""
            if os.path.exists(psp):
                deja_pub = json.load(open(psp)).get("derniere_publication", "")
            for i in range(14):
                day = today + _td(days=i)
                wd = day.weekday()
                label = ("AUJOURD'HUI" if i == 0 else "demain" if i == 1 else "")
                date_fr = f"{NOMS[wd]} {day.day} {MOIS[day.month]}"
                # posts avec horaire choisi par Laurie ce jour-là
                for p in sorted(programmes, key=lambda x: x["programme"]):
                    if p["programme"][:10] == day.strftime("%Y-%m-%d"):
                        rows.append({"date": date_fr, "badge": label,
                                     "quoi": f"Publication à {p['programme'][11:16]} — horaire choisi par toi" + (" (à valider !)" if p["attente"] else ""),
                                     "thumb": p["thumb"], "type": "post", "state": p["state"], "id": p["id"],
                                     "heure": p["programme"][11:16], "custom": True})
                if wd in (0, 2, 4):  # créneau auto lun/mer/ven 19h23
                    if i == 0 and deja_pub == day.strftime("%Y-%m-%d"):
                        rows.append({"date": date_fr, "badge": label, "quoi": "✅ Publication du jour déjà partie", "type": "machine"})
                    else:
                        p = defauts[slot] if slot < len(defauts) else None
                        rows.append({"date": date_fr, "badge": label,
                                     "quoi": "Publication automatique 19h23" + ((" (à valider !)" if p["attente"] else "") if p else ""),
                                     "thumb": p["thumb"] if p else None, "type": "post",
                                     "state": p["state"] if p else "", "id": p["id"] if p else "",
                                     "heure": "19h23", "custom": False, "vide": p is None})
                        slot += 1
            restants = defauts[slot:]
            if restants:
                rows.append({"date": "ensuite", "badge": "", "type": "machine",
                             "quoi": f"+ {len(restants)} contenu(s) en réserve pour les créneaux suivants"})
            return self._json({"rows": rows})

        if self.path == "/api/inspection":
            rp = os.path.join(ENGINE, "rapport-inspectrice.json")
            if os.path.exists(rp):
                return self._json(json.load(open(rp)))
            return self._json({"tout_va_bien": True, "alertes": []})

        if self.path == "/api/rapport":
            rp = os.path.join(ENGINE, "rapport-equipe.json")
            if os.path.exists(rp):
                return self._json(json.load(open(rp)))
            return self._json({"vide": True})

        if self.path == "/api/stories":
            base = os.path.join(ROOT, "queue", "stories")
            os.makedirs(base, exist_ok=True)
            kits = []
            for it in sorted(os.listdir(base), reverse=True):
                d = os.path.join(base, it)
                kp = os.path.join(d, "kit.json")
                mp2 = os.path.join(d, "media.jpg")
                if not os.path.exists(mp2):
                    f1 = os.path.join(d, "frame-1.jpg")
                    mp2 = f1 if os.path.exists(f1) else os.path.join(d, "media.mp4")
                if os.path.isdir(d) and os.path.exists(mp2):
                    kit = json.load(open(kp)) if os.path.exists(kp) else {}
                    frames = sorted(f for f in os.listdir(d) if f.startswith("frame-"))
                    kits.append({"id": it,
                                 "media": f"/queue/stories/{urllib.parse.quote(it)}/{os.path.basename(mp2)}?v={int(os.path.getmtime(mp2))}",
                                 "frames_urls": [f"/queue/stories/{urllib.parse.quote(it)}/{f}?v={int(os.path.getmtime(os.path.join(d, f)))}" for f in frames],
                                 **kit})
            return self._json({"kits": kits})

        if self.path == "/api/library":
            base = os.path.join(ROOT, "queue", "bibliotheque")
            os.makedirs(base, exist_ok=True)
            files = sorted((f for f in os.listdir(base) if f.lower().endswith((".jpg", ".png", ".mp4"))), reverse=True)
            return self._json({"files": [
                {"name": f, "url": f"/queue/bibliotheque/{urllib.parse.quote(f)}?v={int(os.path.getmtime(os.path.join(base, f)))}"}
                for f in files]})

        if self.path == "/api/queue":
            feed, feed_maj = [], ""
            fp = os.path.join(ENGINE, "feed-instagram.json")
            if os.path.exists(fp):
                try:
                    fd = json.load(open(fp))
                    feed, feed_maj = fd.get("posts", []), fd.get("maj", "")
                except Exception:
                    pass
            prog = None
            try:
                import time as _tm
                pp = os.path.join(ENGINE, "progression.json")
                if os.path.exists(pp):
                    _p = json.load(open(pp))
                    if _p.get("texte") and _tm.time() - _p.get("ts", 0) < 180:
                        prog = _p
            except Exception:
                pass
            return self._json({
                "generating": _gen_running,
                "progression": prog,
                "pending": list_state("pending"),
                "approved": list_state("approved"),
                "published": list_state("published"),
                "feed": feed, "feed_maj": feed_maj, "feedsync": FEEDSYNC,
            })
        return super().do_GET()

    def do_POST(self):
        ln = int(self.headers.get("Content-Length", 0))
        data = json.loads(self.rfile.read(ln) or b"{}")

        if self.path == "/api/generate_week":
            # bouton manuel de Laurie : demander au robot cloud le lot de la semaine
            subprocess.run(["git", "pull", "--rebase", "--autostash"],
                           cwd=ROOT, timeout=60, capture_output=True)
            ok, err = _dispatch_workflow("generate.yml")
            if ok:
                return self._json({"ok": True})
            return self._json({"error": err}, 502)

        if self.path == "/api/caption":
            demande = (data.get("texte") or "").strip()
            if not demande:
                return self._json({"erreur": "décris d'abord ton contenu"}, 400)
            r = subprocess.run(["python3", os.path.join(ENGINE, "legende.py"), demande],
                               cwd=ROOT, capture_output=True, timeout=120)
            try:
                return self._json(json.loads(r.stdout.decode().strip().splitlines()[-1]))
            except Exception:
                return self._json({"erreur": "le rédacteur n'a pas répondu — réessaie"}, 500)

        if self.path == "/api/publish_now":
            # secours si la publication automatique a sauté : déclenche le robot
            # publieur sur GitHub (mêmes garde-fous : verrou 1/jour, pas_avant).
            import urllib.request
            subprocess.run(["git", "pull", "--rebase", "--autostash"],
                           cwd=ROOT, timeout=60, capture_output=True)
            psp = os.path.join(ENGINE, "publish-state.json")
            if os.path.exists(psp):
                if json.load(open(psp)).get("derniere_publication") == _date_paris():
                    return self._json({"error": "déjà publié aujourd'hui (règle 1/jour)"}, 409)
            items = [it for it in sorted(os.listdir(Q("approved")))
                     if os.path.isdir(os.path.join(Q("approved"), it)) and "_reel_" not in it]
            if not items:
                return self._json({"error": "rien dans la file « prêts à publier »"}, 400)
            r = subprocess.run(["git", "remote", "get-url", "origin"],
                               cwd=ROOT, capture_output=True, text=True)
            m = re.search(r"x-access-token:([^@]+)@github\.com/([^/]+/[^/.]+)", r.stdout.strip())
            if not m:
                return self._json({"error": "accès GitHub introuvable"}, 500)
            token, repo = m.group(1), m.group(2)
            req = urllib.request.Request(
                f"https://api.github.com/repos/{repo}/actions/workflows/publish.yml/dispatches",
                data=json.dumps({"ref": "main"}).encode(),
                headers={"Authorization": f"token {token}",
                         "Accept": "application/vnd.github+json"})
            try:
                urllib.request.urlopen(req, timeout=30)
            except urllib.error.HTTPError as e:
                return self._json({"error": f"GitHub a refusé ({e.code})"}, 502)
            except Exception:
                return self._json({"error": "GitHub injoignable — vérifier la connexion internet"}, 502)
            return self._json({"ok": True})

        if self.path == "/api/generate":
            run_generate.ai_mode = False
            threading.Thread(target=run_generate, daemon=True).start()
            return self._json({"ok": True})

        if self.path == "/api/generate_ai":
            produit = (data.get("produit") or "").strip()
            if produit:
                # 07/09/2026 (Laurie) : on vérifie les noms contre le catalogue AVANT de lancer quoi que ce soit
                try:
                    sys.path.insert(0, ENGINE)
                    import generate as _core
                    prods = [p for p in _core.fetch_products() if p.get("images")]
                    trouves, introuvables, sugg = _core.resoudre_produits(prods, produit)
                except Exception as e:
                    return self._json({"ok": False, "error": f"catalogue injoignable ({type(e).__name__})"}, 500)
                if introuvables:
                    return self._json({"ok": False, "introuvables": introuvables, "suggestions": sugg,
                                       "trouves": [_core.first_name(p["title"]) for p in trouves]})
                produit = ", ".join(_core.first_name(p["title"]) for p in trouves)
                data["trouves"] = [p["title"] for p in trouves]
            run_generate.ai_mode = True
            run_generate.produit = produit
            threading.Thread(target=run_generate, daemon=True).start()
            return self._json({"ok": True, "trouves": data.get("trouves", [])})

        if self.path == "/api/committee":
            def _run():
                global _gen_running
                with _gen_lock:
                    if _gen_running:
                        return
                    _gen_running = True
                try:
                    subprocess.run(["python3", os.path.join(ENGINE, "committee.py")],
                                   cwd=ROOT, timeout=600)
                    _git_sync("committee")
                finally:
                    _gen_running = False
            threading.Thread(target=_run, daemon=True).start()
            return self._json({"ok": True})

        if self.path == "/api/library_delete":
            name = os.path.basename(data.get("name", ""))
            fp = os.path.join(ROOT, "queue", "bibliotheque", name)
            if name and os.path.isfile(fp):
                os.remove(fp)
                _git_sync_bg(f"library_delete {name}")
                return self._json({"ok": True})
            return self._json({"error": "introuvable"}, 404)

        if self.path == "/api/story_regen":
            kit = os.path.basename(data.get("id", ""))
            kd = os.path.join(ROOT, "queue", "stories", kit)
            if kit and os.path.isdir(kd):
                shutil.rmtree(kd)
            r = subprocess.run(["python3", os.path.join(ENGINE, "story.py")],
                               cwd=ROOT, timeout=180, capture_output=True)
            _git_sync_bg("story_regen")
            return self._json({"ok": r.returncode == 0})

        if self.path == "/api/curate":
            r = subprocess.run(["python3", os.path.join(ENGINE, "committee.py"), "curate"],
                               cwd=ROOT, capture_output=True, timeout=180)
            _git_sync_bg("curate")
            try:
                out = r.stdout.decode().strip().splitlines()[-1]
                return self._json(json.loads(out))
            except Exception:
                return self._json({"raison": "la curatrice n'a pas répondu", "applique": False})

        if self.path == "/api/order_undo":
            import datetime as _dt
            hp = os.path.join(ENGINE, "ordre-historique.json")
            hist = json.load(open(hp)) if os.path.exists(hp) else []
            if not hist:
                return self._json({"error": "aucune version précédente"}, 404)
            snap = hist.pop()
            rest_map, current = {}, []
            for state in ("approved", "pending"):
                base = Q(state)
                for it in sorted(os.listdir(base)):
                    d = os.path.join(base, it)
                    if os.path.isdir(d) and os.path.exists(os.path.join(d, "meta.json")):
                        rest = it.split("_", 2)[2] if it.count("_") >= 2 else it
                        rest_map[rest] = (state, it)
                        current.append(rest)
            ordered = [r for r in snap["order"] if r in rest_map] + [r for r in current if r not in snap["order"]]
            stamp = _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%d_%H")
            for i, rest in enumerate(ordered, start=1):
                state, it = rest_map[rest]
                new = f"{stamp}{i:02d}00_{rest}"
                src, dst = os.path.join(Q(state), it), os.path.join(Q(state), new)
                try:
                    if src != dst and not os.path.exists(dst):
                        os.rename(src, dst)
                except OSError:
                    continue
            json.dump(hist, open(hp, "w"), indent=2, ensure_ascii=False)
            _git_sync_bg("order_undo")
            return self._json({"ok": True, "restaure": snap.get("date", ""), "restantes": len(hist)})

        if self.path == "/api/archive":
            base = Q("published")
            os.makedirs(os.path.join(ROOT, "queue", "archive"), exist_ok=True)
            items = sorted(it for it in os.listdir(base) if os.path.isdir(os.path.join(base, it)))
            moved = 0
            for it in items[:-30]:
                shutil.move(os.path.join(base, it), os.path.join(ROOT, "queue", "archive", it))
                moved += 1
            if moved:
                _git_sync_bg(f"archive {moved} posts")
            return self._json({"ok": True, "archives": moved})

        if self.path == "/api/reorder":
            # 07/09/2026 (Laurie) : déplacement à la souris dans l'aperçu — on reçoit l'ordre complet voulu d'une file
            state, ids = data.get("state"), data.get("ids") or []
            base = Q(state)
            if not ids or any("/" in i or ".." in i or not os.path.isdir(os.path.join(base, i)) for i in ids):
                return self._json({"error": "la grille vient de changer — recharge la page"}, 400)
            existants = sorted(d for d in os.listdir(base) if os.path.isdir(os.path.join(base, d)) and d.count("_") >= 2)
            if sorted(ids) != existants:
                return self._json({"error": "la grille vient de changer — recharge la page"}, 409)
            date = ids[0].split("_", 1)[0]
            tmp = {}
            for i, old in enumerate(ids):  # passage par des noms temporaires : aucune collision possible
                t = os.path.join(base, f"__reorder_{i}_{old.split('_', 2)[2]}")
                os.rename(os.path.join(base, old), t); tmp[i] = (t, old.split("_", 2)[2].strip())
            for i, (t, reste) in tmp.items():
                reste = re.sub(r" \d+$", "", reste)
                os.rename(t, os.path.join(base, f"{date}_{i * 100 + 100100:06d}_{reste}"))
            _git_sync_bg(f"reorder {state}")
            return self._json({"ok": True})

        if self.path == "/api/swap":
            a, sa, b, sb = data.get("a"), data.get("stateA"), data.get("b"), data.get("stateB")
            # 07/09/2026 : deux dossiers avec le MÊME horodatage (ou un suffixe « 2 » Finder) faisaient échouer l'échange
            # → on renumérote la file avant d'échanger, et on renvoie les nouveaux noms
            ren = _renumeroter(sa) if sa == sb else {**_renumeroter(sa), **_renumeroter(sb)}
            a, b = ren.get(a, a), ren.get(b, b)
            pa, pb = os.path.join(Q(sa), a), os.path.join(Q(sb), b)
            if not (os.path.isdir(pa) and os.path.isdir(pb)):
                return self._json({"error": "introuvable"}, 404)
            if a.count("_") < 2 or b.count("_") < 2 or "swap_tmp" in a or "swap_tmp" in b:
                return self._json({"error": "élément non échangeable — recharge la page"}, 400)
            prefa, resta = a.split("_", 2)[0] + "_" + a.split("_", 2)[1], a.split("_", 2)[2]
            prefb, restb = b.split("_", 2)[0] + "_" + b.split("_", 2)[1], b.split("_", 2)[2]
            import uuid as _uuid
            tmp = os.path.join(Q(sa), f"__swap_{_uuid.uuid4().hex[:8]}")
            try:
                os.rename(pa, tmp)
                os.rename(pb, os.path.join(Q(sb), prefa + "_" + restb))
                os.rename(tmp, os.path.join(Q(sa), prefb + "_" + resta))
            except OSError as e:
                # rollback : on remet A à sa place si possible, jamais de dossier orphelin
                try:
                    if os.path.isdir(tmp) and not os.path.isdir(pa):
                        os.rename(tmp, pa)
                except OSError:
                    pass
                return self._json({"error": f"échange impossible ({e.__class__.__name__}) — réessaie"}, 500)
            _git_sync_bg(f"swap {resta} <-> {restb}")
            return self._json({"ok": True})

        if self.path == "/api/feed_refresh":
            # Laurie vient de poster depuis son mobile : on demande au robot cloud
            # (feedsync) une photo fraîche du vrai compte ; l'autopull la ramènera.
            if FEEDSYNC["en_cours"]:
                return self._json({"ok": True, "deja": True})
            ok, err = _dispatch_workflow("inspection.yml")
            if ok:
                fp = os.path.join(ENGINE, "feed-instagram.json")
                try:
                    maj_avant = json.load(open(fp)).get("maj", "")
                except Exception:
                    maj_avant = ""
                head_avant = subprocess.run(["git", "rev-parse", "HEAD"], cwd=ROOT, capture_output=True, text=True).stdout.strip()
                threading.Thread(target=_feedsync_suivi, args=(maj_avant, head_avant), daemon=True).start()
                return self._json({"ok": True})
            return self._json({"error": err}, 502)

        if self.path == "/api/programmer":
            state, item = data.get("state"), os.path.basename(data.get("id", ""))
            quand = (data.get("quand") or "").strip()
            mp = os.path.join(Q(state), item, "meta.json")
            if not os.path.exists(mp):
                return self._json({"error": "introuvable"}, 404)
            if quand and not re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$", quand):
                return self._json({"error": "format attendu : AAAA-MM-JJ HH:MM"}, 400)
            meta = json.load(open(mp))
            if quand:
                meta["programme"] = quand
            else:
                meta.pop("programme", None)
            json.dump(meta, open(mp, "w"), indent=2, ensure_ascii=False)
            _git_sync_bg(f"programme {item} -> {quand or 'auto'}")
            return self._json({"ok": True})

        if self.path == "/api/action":
            state, item = data.get("state"), data.get("id")
            src = os.path.join(Q(state), item)
            if not os.path.isdir(src) or "/" in item or ".." in item:
                return self._json({"error": "introuvable"}, 404)

            action = data.get("action")
            if action == "recaption":
                # 07/09/2026 (Laurie) : « un bouton pour générer une autre légende si celle-là ne me plaît pas » — texte seul, quasi gratuit
                mp = os.path.join(src, "meta.json")
                meta = json.load(open(mp)) if os.path.exists(mp) else {}
                titre = item.split("_")[-1].replace("-", " ")
                try:  # vrai titre Shopify = le produit dont le handle termine le nom du dossier (…_tour_<handle>)
                    import generate as _core
                    for _p in sorted(_core.fetch_products(), key=lambda x: -len(x.get("handle", ""))):
                        if _p.get("handle") and item.endswith(_p["handle"]):
                            titre = _p["title"]
                            break
                except Exception:
                    pass
                genre = "carrousel" if meta.get("type") == "carousel" else "post"
                demande = (f"{genre} Instagram pour la pièce EXACTE « {titre} » — c'est le SEUL produit à nommer, jamais un autre. "
                           f"Contexte de l'image : {meta.get('description', '')}. "
                           f"Écris une légende DIFFÉRENTE de celle-ci (autre angle, autre accroche) : « {meta.get('caption', '')} ». "
                           f"Termine par 2 à 4 hashtags de la maison.")
                r = subprocess.run(["python3", os.path.join(ENGINE, "legende.py"), demande],
                                   cwd=ROOT, capture_output=True, timeout=120)
                try:
                    out = json.loads(r.stdout.decode().strip().splitlines()[-1])
                except Exception:
                    out = {"erreur": "le rédacteur n'a pas répondu — réessaie"}
                if not out.get("legende"):
                    return self._json({"error": out.get("erreur", "échec")}, 500)
                meta["caption_precedente"] = meta.get("caption", "")
                meta["caption"] = out["legende"]
                json.dump(meta, open(mp, "w"), indent=2, ensure_ascii=False)
                _git_sync_bg(f"recaption {item}")
                return self._json({"ok": True, "caption": out["legende"]})
            if action in ("slide_off", "slide_on"):
                # 07/09/2026 (Laurie) : retirer UNE slide d'un carrousel sans jeter les bonnes (retiree-… = cachée, jamais publiée, réversible)
                f = os.path.basename(data.get("file") or "")
                fp = os.path.join(src, f)
                if not f or not os.path.exists(fp):
                    return self._json({"error": "slide introuvable"}, 404)
                if action == "slide_off":
                    restantes = [x for x in os.listdir(src) if x.startswith(("slide", "media")) and x != f]
                    if len(restantes) < 1:
                        return self._json({"error": "il doit rester au moins une image"}, 400)
                    os.rename(fp, os.path.join(src, "retiree-" + f))
                else:
                    os.rename(fp, os.path.join(src, f.replace("retiree-", "", 1)))
                _git_sync_bg(f"{action} {item} {f}")
                return self._json({"ok": True})
            if action == "approve":
                shutil.move(src, os.path.join(Q("approved"), item))
            elif action == "reject":
                reason = (data.get("reason") or "").strip()
                mp = os.path.join(src, "meta.json")
                if os.path.exists(mp):
                    meta = json.load(open(mp))
                    meta["rejet"] = reason or "sans raison donnée"
                    json.dump(meta, open(mp, "w"), indent=2, ensure_ascii=False)
                import datetime
                with open(os.path.join(ROOT, "engine", "feedback.jsonl"), "a") as fb:
                    fb.write(json.dumps({
                        "date": datetime.datetime.now().isoformat(timespec="minutes"),
                        "item": item, "raison": reason or "sans raison donnée",
                    }, ensure_ascii=False) + "\n")
                shutil.move(src, os.path.join(Q("rejected"), item))
                if reason:
                    threading.Thread(target=lambda: subprocess.run(
                        ["python3", os.path.join(ENGINE, "apprendre.py"), item, reason],
                        cwd=ROOT, timeout=120), daemon=True).start()
                if data.get("regen"):
                    def _regen(it=item):
                        global _gen_running
                        with _gen_lock:
                            if _gen_running:
                                return
                            _gen_running = True
                        try:
                            subprocess.run(["python3", os.path.join(ENGINE, "regenerate.py"), it],
                                           cwd=ROOT, timeout=1800)
                        finally:
                            _gen_running = False
                    threading.Thread(target=_regen, daemon=True).start()
            elif action == "save":
                mp = os.path.join(src, "meta.json")
                meta = json.load(open(mp))
                meta["caption"] = data.get("caption", meta["caption"])
                json.dump(meta, open(mp, "w"), indent=2, ensure_ascii=False)
            else:
                return self._json({"error": "action inconnue"}, 400)
            _git_sync_bg(f"{action} {item}")
            return self._json({"ok": True})

        return self._json({"error": "not found"}, 404)


if __name__ == "__main__":
    subprocess.run(["git", "pull", "--rebase", "--autostash"], cwd=ROOT, timeout=60, capture_output=True)
    threading.Thread(target=_autopull, daemon=True).start()
    print(f"Inaya Paris Cockpit → http://localhost:{PORT}")
    subprocess.Popen(["open", f"http://localhost:{PORT}"])
    HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
