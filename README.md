# Pilotage Cabinet — POC (Streamlit + SQLite)

POC interne de pilotage opérationnel d’un cabinet :
- suivi missions (avancement, charge consommée, alertes),
- CRA (saisie temps), vues jour / semaine / mois,
- capacités (charge vs capacité),
- synthèse + chatbot (lecture seule),
- **simulation Board** (devis + suivi) type “Excel costing”.

✅ Fonctionne en local (Windows / Linux / macOS)
✅ Aucun cloud requis
✅ Base SQLite locale (`data/app.db`)

---

## Accès & rôles (RBAC)

Rôles :
- **Admin** : gestion référentiels (clients/missions), import/export, reset démo
- **Board** : accès complet **y compris financier** + simulation Board
- **Lead** : opérationnel sur ses missions (pas de finance)
- **Consultant** : ses CRA / ses missions (pas de finance)

🔐 Finance : **uniquement Board/Admin** (UI + chatbot)

---

## Comptes de démo (premier lancement)

Ces comptes sont chargés automatiquement si la base est vide :

- Admin : `admin / admin123`
- Board : `board1 / board123`
- Lead : `lead1 / lead123`
- Consultant : `consult1 / cons123`

Fichier source démo : `data/sample/users.csv` (colonne `password_clear`)

---

## Démarrage local (Linux / macOS)

### Installation
```bash
chmod +x install.sh run.sh
./install.sh
