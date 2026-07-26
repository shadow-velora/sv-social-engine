# SV Social Engine

Usine Instagram autonome de Shadow Velora. Tourne dans le cloud (GitHub Actions),
PC éteint, sans intervention.

## Comment ça marche
- **Lundi 06:00 UTC** — `generate.yml` : fabrique 6 contenus (2 photos studio 4:5,
  1 carrousel, 1 bande éditoriale, 2 reels Ken Burns) à partir des vraies photos
  produits de la boutique, avec légendes tirées du réservoir `engine/captions.json`.
  Dépose tout dans `queue/approved/`.
- **Lundi/mercredi/vendredi 17:00 UTC** — `publish.yml` : publie le plus ancien
  contenu de la file sur Instagram via l'API officielle Meta, puis l'archive dans
  `queue/published/`.

Aucune IA générative dans le circuit : uniquement les vraies photos de la boutique
(jamais de fausse robe possible).

## Secrets à configurer (Settings → Secrets and variables → Actions)
| Secret | Quoi |
|---|---|
| `IG_USER_ID` | id du compte Instagram professionnel |
| `IG_ACCESS_TOKEN` | jeton longue durée Meta |
| `META_APP_ID` / `META_APP_SECRET` | app Meta (pour prolonger le jeton) |

## Setup unique (avec Claude, ~1h)
1. Instagram en compte **professionnel**, lié à une **Page Facebook**
2. [developers.facebook.com](https://developers.facebook.com) → créer une app → produit *Instagram Graph API*
3. Générer le jeton longue durée, récupérer l'`IG_USER_ID`
4. Créer ce repo **privé** sur GitHub, pousser ces fichiers, renseigner les 4 secrets
5. Lancer `generate.yml` puis `publish.yml` à la main (onglet Actions → Run workflow) pour le test

⚠️ Repo **privé** : la file d'attente est servie via `raw.githubusercontent.com` —
pour un repo privé il faut passer `ASSET_BASE_URL` sur un token raw ou rendre le
repo public sans données sensibles (il n'y a que des photos déjà publiques de la
boutique + des légendes). Recommandé : **repo public**, c'est le plus simple et
rien de confidentiel n'y transite.

## Ajuster
- Légendes / hashtags : `engine/captions.json` (validées par Laurie)
- Cadence : les `cron` dans `.github/workflows/*.yml`
- Composition du lot : fonction `main()` de `engine/generate.py`
