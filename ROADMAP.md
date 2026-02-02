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

# Roadmap

## Scope actuel (V1)
- Pilotage opérationnel
- CRA & capacités
- Chatbot KPI
- RBAC strict

## Évolutions rapides (V1.1)
- Pages Streamlit multipages
- Export PDF / Excel
- Alertes email internes

## Structurant (V2)
- LLM local (Ollama)
- PostgreSQL
- Planning avancé
- Validation CRA

## Hors scope
- SaaS multi-client
- Facturation
- Paie
