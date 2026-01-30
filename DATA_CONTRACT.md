# Contrat de données

## Temps (time_entries)
- entry_date : YYYY-MM-DD
- hours : 1 | 4 | 8 (normalisé en heures)
- category :
  - billable
  - non_billable_client
  - internal

## Règles
- 1 jour = 8h
- 1 entrée max / jour / mission / catégorie / user
- mission obligatoire sauf internal

## Import CSV
Les CSV doivent respecter strictement les schémas fournis dans data/sample/.
