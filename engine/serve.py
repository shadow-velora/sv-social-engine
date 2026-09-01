#!/usr/bin/env python3
"""
SV Cockpit — interface locale de contrôle de l'usine sociale.
Lancement : double-clic sur "SV Cockpit.command" (ou python3 engine/serve.py)
puis http://localhost:8765
"""
import json
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
        out.append({
            "id": it, "state": state, "type": meta.get("type", "?"),
            "caption": meta.get("caption", ""),
            "alerte_da": meta.get("alerte_da"),
            "media": [f"/queue/{state}/{it}/{m}?v={int(os.path.getmtime(os.path.join(d, m)))}" for m in media],
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
    import re as _re
    import urllib.request as _ur
    r = subprocess.run(["git", "remote", "get-url", "origin"],
                       cwd=ROOT, capture_output=True, text=True)
    m = _re.search(r"x-access-token:([^@]+)@github\.com/([^/]+/[^/.]+)", r.stdout.strip())
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


def _git_sync(message):
    """Chaque action Cockpit (approve/reject/swap/...) est poussée sur GitHub immédiatement.
    Sans ça, les clics de Laurie restent locaux et les workflows GitHub travaillent sur un
    état périmé (ex: post approuvé localement mais toujours pending côté robot)."""
    with _git_lock:
        subprocess.run(["git", "add", "-A", "queue", "engine/feedback.jsonl",
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


def _git_sync_bg(message):
    threading.Thread(target=_git_sync, args=(message,), daemon=True).start()


def _autopull():
    """Le Cockpit reste TOUJOURS synchronisé avec le vrai état (GitHub) : pull toutes les 2 min."""
    import time
    while True:
        time.sleep(120)
        with _git_lock:
            subprocess.run(["git", "pull", "--rebase", "--autostash"],
                           cwd=ROOT, timeout=120, capture_output=True)


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
                "feed": feed, "feed_maj": feed_maj,
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
            import re
            import urllib.request
            subprocess.run(["git", "pull", "--rebase", "--autostash"],
                           cwd=ROOT, timeout=60, capture_output=True)
            psp = os.path.join(ENGINE, "publish-state.json")
            if os.path.exists(psp):
                import datetime as _dt
                if json.load(open(psp)).get("derniere_publication") == _dt.datetime.utcnow().strftime("%Y-%m-%d"):
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
            run_generate.ai_mode = True
            threading.Thread(target=run_generate, daemon=True).start()
            return self._json({"ok": True})

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
            stamp = _dt.datetime.utcnow().strftime("%Y-%m-%d_%H")
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

        if self.path == "/api/swap":
            a, sa, b, sb = data.get("a"), data.get("stateA"), data.get("b"), data.get("stateB")
            pa, pb = os.path.join(Q(sa), a), os.path.join(Q(sb), b)
            if not (os.path.isdir(pa) and os.path.isdir(pb)):
                return self._json({"error": "introuvable"}, 404)
            prefa, resta = a.split("_", 2)[0] + "_" + a.split("_", 2)[1], a.split("_", 2)[2]
            prefb, restb = b.split("_", 2)[0] + "_" + b.split("_", 2)[1], b.split("_", 2)[2]
            tmp = os.path.join(Q(sa), "__swap_tmp")
            os.rename(pa, tmp)
            os.rename(pb, os.path.join(Q(sb), prefa + "_" + restb))
            os.rename(tmp, os.path.join(Q(sa), prefb + "_" + resta))
            _git_sync_bg(f"swap {resta} <-> {restb}")
            return self._json({"ok": True})

        if self.path == "/api/programmer":
            state, item = data.get("state"), os.path.basename(data.get("id", ""))
            quand = (data.get("quand") or "").strip()
            mp = os.path.join(Q(state), item, "meta.json")
            if not os.path.exists(mp):
                return self._json({"error": "introuvable"}, 404)
            import re as _re
            if quand and not _re.match(r"^\d{4}-\d{2}-\d{2} \d{2}:\d{2}$", quand):
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
