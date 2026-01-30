# Paramètres

## Fichiers
- `.env`
- `configs/settings.example.yaml`

## Paramètres principaux
| Paramètre | Emplacement | Description |
|----|----|----|
| APP_DB_PATH | .env | Chemin SQLite |
| time.day_hours | YAML | Heures par jour (défaut 8) |
| ui.default_view | YAML | Vue CRA par défaut |
| bcrypt_rounds | YAML | Sécurité hash |
| csv_encoding | YAML | Encodage import |

## Paramètres sensibles
- mots de passe (hashés)
- montants financiers (Board only)
