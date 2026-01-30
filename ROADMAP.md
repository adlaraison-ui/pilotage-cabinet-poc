
---

# 📄 ARCHITECTURE.md

```md
# Architecture — V1

## Vue d’ensemble
- Application Streamlit mono-instance
- Base SQLite locale (fichier)
- Accès navigateur via LAN

## Composants
- app.py : UI + navigation + RBAC
- src/services : logique métier, DB, chatbot
- data/app.db : base locale
- data/sample : données démo

## Flux
Consultant → saisie CRA → SQLite
Lead → vues missions (sans finance)
Board → vues globales + finance
Chatbot → lecture des vues KPI uniquement

## Choix techniques
- SQLite : zéro coût, zéro infra
- Streamlit : rapidité, lisibilité
- RBAC applicatif (SQLite ne gère pas les GRANT)

## Volontairement écarté
- Cloud
- Micro-services
- ORM complexe
- Auth externe
