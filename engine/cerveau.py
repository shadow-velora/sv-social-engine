#!/usr/bin/env python3
"""
LE CERVEAU — source unique des règles de la marque.
Tout module qui génère du contenu (image, texte, story, réel, légende) DOIT
appeler ces fonctions. Sinon il travaille en aveugle et sort du hors-sujet.
"""
import json
import os

ENGINE = os.path.dirname(os.path.abspath(__file__))


def _charge(nom, defaut):
    p = os.path.join(ENGINE, nom)
    try:
        return json.load(open(p))
    except Exception:
        return defaut


def playbook(role=""):
    """Le savoir-faire du métier concerné (smm, da, curatrice, producer, cm, editor)."""
    pb = _charge("playbooks.json", {})
    if role and pb.get(role):
        return pb[role]
    return " ".join(v for v in pb.values() if isinstance(v, str))


def lecons(handle=""):
    """Les règles apprises des rejets de Laurie — globales + propres à une robe."""
    lec = _charge("lecons.json", {"global": [], "par_robe": {}})
    regles = list(lec.get("global", []))
    if handle:
        regles += lec.get("par_robe", {}).get(handle, [])
    return regles


def rejets_recents(n=8):
    """Les dernières raisons de rejet, mot pour mot."""
    p = os.path.join(ENGINE, "feedback.jsonl")
    if not os.path.exists(p):
        return []
    out = []
    for ligne in open(p).read().strip().splitlines()[-n:]:
        try:
            r = json.loads(ligne).get("raison", "").strip()
            if r and r != "sans raison donnée":
                out.append(r)
        except Exception:
            continue
    return out


def contexte(role="", handle="", pour_texte=False):
    """Bloc à injecter dans TOUT prompt de génération.
    pour_texte=True → version orientée rédaction (légendes, stories)."""
    parties = []
    pb = playbook(role)
    if pb:
        parties.append("MÉTIER — " + pb)
    reg = lecons(handle)
    if reg:
        parties.append("RÈGLES APPRISES DES REJETS DE LA FONDATRICE (non négociables) : " + " ".join(reg))
    rej = rejets_recents()
    if rej:
        parties.append("CE QU'ELLE A REFUSÉ RÉCEMMENT, DANS SES MOTS : " + " · ".join(rej))
    if pour_texte:
        parties.append("TON DE MARQUE : Inaya Paris, élégance parisienne, robes de soirée et pièces fortes. "
                       "Anglais, phrases courtes, aucun emoji, jamais de superlatif creux, "
                       "jamais de promesse invérifiable. Signature possible : ~")
    return "\n".join(parties)


if __name__ == "__main__":
    print(contexte(role="smm", pour_texte=True)[:1500])
