from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd


@dataclass(frozen=True)
class ChatContext:
    role: str
    user_id: int
    mission_ids: List[int]
    visible_user_ids: List[int]  # pour les questions de charge


def _df(conn: sqlite3.Connection, sql: str, params: Tuple[Any, ...] = ()) -> pd.DataFrame:
    return pd.read_sql_query(sql, conn, params=params)


def _sanitize_question(q: str) -> str:
    q = (q or "").strip().lower()
    q = re.sub(r"\s+", " ", q)
    return q


def _intent(q: str) -> str:
    """
    Intentions V1 (déterministes) :
    - status_global
    - projects_risk
    - who_busy
    - time_split
    - finance_summary (board/admin only)
    - help
    """
    if not q:
        return "help"

    if any(k in q for k in ["aide", "help", "que peux-tu", "exemple"]):
        return "help"

    if any(k in q for k in ["à risque", "risque", "dérive", "overrun", "near limit", "alerte"]):
        return "projects_risk"

    if any(k in q for k in ["plus chargé", "surcharg", "qui est chargé", "busy", "charge"]):
        return "who_busy"

    if any(k in q for k in ["répartition", "billable", "internal", "non billable", "catégorie"]):
        return "time_split"

    if any(k in q for k in ["marge", "margin", "coût", "cout", "ca", "chiffre", "€", "eur", "finance"]):
        return "finance_summary"

    if any(k in q for k in ["où en est", "où en est-on", "statut", "global", "cette semaine", "this week"]):
        return "status_global"

    return "status_global"

def _normalize(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower()

def _extract_mission_code(text: str) -> Optional[str]:
    # accepte M-2026-002, m2026-002, M 2026 002...
    t = text or ""
    m = re.search(r"\b(m)\s*[- ]?\s*(\d{4})\s*[- ]?\s*(\d{3})\b", t, flags=re.IGNORECASE)
    if not m:
        return None
    return f"M-{m.group(2)}-{m.group(3)}"

def _find_mission_by_name_or_code(conn, mission_ids: list[int], text: str) -> Optional[dict]:
    """
    Retourne une mission visible matching question (code exact ou fuzzy sur nom).
    """
    if not mission_ids:
        return None

    code = _extract_mission_code(text)
    params = tuple(mission_ids)

    # 1) match exact par code
    if code:
        q = f"""
        SELECT id, code, name
        FROM missions
        WHERE id IN ({",".join(["?"]*len(mission_ids))})
          AND UPPER(code) = UPPER(?)
        LIMIT 1
        """
        row = conn.execute(q, params + (code,)).fetchone()
        if row:
            return dict(row)

    # 2) match par nom (contient)
    # on ne fait pas du vrai fuzzy pour rester simple et déterministe
    t = _normalize(text)
    if len(t) < 3:
        return None

    q2 = f"""
    SELECT id, code, name
    FROM missions
    WHERE id IN ({",".join(["?"]*len(mission_ids))})
    """
    rows = conn.execute(q2, params).fetchall()
    if not rows:
        return None

    # score simple: nom présent dans la question ou mots communs
    best = None
    best_score = 0
    for r in rows:
        name = _normalize(r["name"])
        code2 = _normalize(r["code"])
        score = 0
        if name and name in t:
            score += 10
        if code2 and code2 in t:
            score += 10
        # score par mots (évite "Mission" trop générique)
        for w in [w for w in name.split(" ") if len(w) >= 4 and w not in ("mission",)]:
            if w in t:
                score += 2
        if score > best_score:
            best_score = score
            best = dict(r)

    return best if best_score >= 4 else None

def _answer_mission_status(conn, ctx, mission: dict) -> dict:
    mid = int(mission["id"])

    # KPI mission (heures)
    row = conn.execute(
        """
        SELECT client_name, mission_code, mission_name, status,
               sold_hours, consumed_hours, consumed_pct
        FROM kpi_mission_hours
        WHERE mission_id=?
        """,
        (mid,),
    ).fetchone()

    if not row:
        return {"text": "Je n’ai pas trouvé de KPI pour cette mission.", "tables": []}

    r = dict(row)

    # Répartition par catégories (billable/non_billable/internal)
    dist = conn.execute(
        """
        SELECT category, COALESCE(SUM(hours),0) AS hours
        FROM time_entries
        WHERE mission_id=?
        GROUP BY category
        """,
        (mid,),
    ).fetchall()
    dist_df = pd.DataFrame([dict(x) for x in dist]) if dist else pd.DataFrame(columns=["category","hours"])

    text = (
        f"**Focus mission : {r['mission_code']} — {r['mission_name']} ({r['client_name']})**\n\n"
        f"- Statut : **{r['status']}**\n"
        f"- Heures vendues : **{float(r['sold_hours'] or 0):.0f} h**\n"
        f"- Heures consommées : **{float(r['consumed_hours'] or 0):.0f} h**\n"
        f"- Taux de conso : **{float(r['consumed_pct'] or 0):.1f} %**\n"
    )

    # Finance seulement Board/Admin
    tables = []
    if ctx.role in ("BOARD", "ADMIN"):
        fin = conn.execute(
            """
            SELECT sold_amount_eur, cost_eur, margin_eur
            FROM kpi_finance_mission
            WHERE mission_id=?
            """,
            (mid,),
        ).fetchone()
        if fin:
            f = dict(fin)
            text += (
                "\n**Financier (Board/Admin)**\n"
                f"- CA vendu : **{float(f.get('sold_amount_eur') or 0):,.0f} €**\n"
                f"- Coûts estimés : **{float(f.get('cost_eur') or 0):,.0f} €**\n"
                f"- Marge estimée : **{float(f.get('margin_eur') or 0):,.0f} €**\n"
            ).replace(",", " ")

    if not dist_df.empty:
        tables.append({"title": "Répartition des heures saisies (catégories)", "df": dist_df})

    return {"text": text, "tables": tables}


def answer_question(conn: sqlite3.Connection, ctx: ChatContext, question: str) -> Dict[str, Any]:
    """
    Sortie :
      {
        "text": str,
        "tables": list[{"title": str, "df": DataFrame}]
      }
    Strictement READ-ONLY : aucune écriture DB.
    """
    q = _sanitize_question(question)
    intent = _intent(q)

    # 0) Focus mission si la question cible une mission visible
    m = _find_mission_by_name_or_code(conn, ctx.mission_ids, question or "")
    if m is not None:
        return _answer_mission_status(conn, ctx, m)

    # Sécurité: si aucune mission visible
    if not ctx.mission_ids and intent in ("status_global", "projects_risk", "finance_summary"):
        return {
            "text": "Je n’ai aucune mission visible pour ton profil.",
            "tables": [],
        }

    # Helpers filtres
    def in_clause(ids: List[int]) -> Tuple[str, Tuple[Any, ...]]:
        if not ids:
            return "(NULL)", tuple()
        return "(" + ",".join(["?"] * len(ids)) + ")", tuple(ids)

    mids_sql, mids_params = in_clause(ctx.mission_ids)
    uids_sql, uids_params = in_clause(ctx.visible_user_ids)

    # --- HELP
    if intent == "help":
        txt = (
            "Je réponds en lecture seule sur les KPI autorisés.\n\n"
            "Exemples :\n"
            "- « Où en est-on cette semaine ? »\n"
            "- « Quels projets sont à risque ? »\n"
            "- « Qui est le plus chargé ? »\n"
            "- « Répartition billable / internal ? »\n"
        )
        if ctx.role in ("BOARD", "ADMIN"):
            txt += "- « Synthèse finance ? »\n"
        return {"text": txt, "tables": []}

    # --- STATUS GLOBAL (simple, sans dépendre d’une période dans ce livrable)
    if intent == "status_global":
        df = _df(
            conn,
            f"""
            SELECT
              COUNT(*) AS missions_count,
              SUM(consumed_hours) AS consumed_hours,
              SUM(sold_hours) AS sold_hours
            FROM kpi_mission_hours
            WHERE mission_id IN {mids_sql}
            """,
            mids_params,
        )
        row = df.iloc[0].to_dict()
        missions_count = int(row.get("missions_count") or 0)
        consumed_hours = float(row.get("consumed_hours") or 0)
        sold_hours = float(row.get("sold_hours") or 0)
        pct = (consumed_hours / sold_hours * 100.0) if sold_hours > 0 else None

        txt = (
            f"Statut global : {missions_count} mission(s) visible(s).\n"
            f"- Heures consommées : {consumed_hours:.0f} h\n"
            f"- Heures vendues : {sold_hours:.0f} h\n"
            f"- Taux de conso : {(pct and f'{pct:.1f}%') or 'N/A'}\n"
        )

        # Mini top 5 dérives (opérationnel)
        df2 = _df(
            conn,
            f"""
            SELECT mission_code, mission_name, client_name, sold_hours, consumed_hours, variance_hours
            FROM kpi_mission_variance
            WHERE mission_id IN {mids_sql}
            ORDER BY variance_hours DESC
            LIMIT 5
            """,
            mids_params,
        )
        tables = []
        if not df2.empty:
            tables.append({"title": "Top dérives (heures)", "df": df2})

        return {"text": txt, "tables": tables}

    # --- PROJECTS RISK
    if intent == "projects_risk":
        df = _df(
            conn,
            f"""
            SELECT mission_code, mission_name, client_name, sold_hours, consumed_hours, variance_hours, risk_level
            FROM kpi_alert_missions_risk
            WHERE mission_id IN {mids_sql}
            ORDER BY
              CASE risk_level
                WHEN 'overrun' THEN 3
                WHEN 'near_limit' THEN 2
                WHEN 'no_sold_load' THEN 1
                ELSE 0
              END DESC,
              variance_hours DESC
            """,
            mids_params,
        )
        if df.empty:
            return {"text": "Aucun projet à risque détecté sur ton périmètre.", "tables": []}

        txt = f"{len(df)} projet(s) à risque / proche limite sur ton périmètre."
        return {"text": txt, "tables": [{"title": "Projets à risque", "df": df}]}

    # --- WHO BUSY (charge utilisateurs visibles)
    if intent == "who_busy":
        if not ctx.visible_user_ids:
            return {"text": "Je n’ai aucun utilisateur visible pour calculer la charge.", "tables": []}

        df = _df(
            conn,
            f"""
            SELECT user_name, SUM(logged_hours) AS logged_hours
            FROM kpi_user_load_daily
            WHERE user_id IN {uids_sql}
            GROUP BY user_name
            ORDER BY logged_hours DESC
            """,
            uids_params,
        )
        if df.empty:
            return {"text": "Aucune saisie de temps trouvée pour calculer la charge.", "tables": []}

        top = df.iloc[0].to_dict()
        txt = f"Le plus chargé (sur les données disponibles) : **{top['user_name']}** avec **{int(top['logged_hours'])} h**."
        return {"text": txt, "tables": [{"title": "Charge par personne (heures loggées)", "df": df}]}

    # --- TIME SPLIT (répartition catégories)
    if intent == "time_split":
        # NB: consultant ne voit que lui-même via visible_user_ids (déjà filtré)
        df = _df(
            conn,
            f"""
            SELECT category, SUM(hours) AS hours
            FROM time_entries
            WHERE user_id IN {uids_sql}
            GROUP BY category
            ORDER BY hours DESC
            """,
            uids_params,
        )
        if df.empty:
            return {"text": "Aucune donnée de temps pour calculer la répartition.", "tables": []}

        total = float(df["hours"].sum())
        txt = "Répartition du temps (heures) :"
        df2 = df.copy()
        df2["pct"] = (df2["hours"] / total * 100.0).round(1)
        return {"text": txt, "tables": [{"title": "Répartition par catégorie", "df": df2}]}

    # --- FINANCE SUMMARY (Board/Admin only)
    if intent == "finance_summary":
        if ctx.role not in ("BOARD", "ADMIN"):
            return {
                "text": "Je ne peux pas afficher de données financières avec ton rôle.",
                "tables": [],
            }

        df = _df(
            conn,
            f"""
            SELECT
              SUM(sold_amount_eur) AS sold_amount_eur,
              SUM(cost_eur) AS cost_eur,
              SUM(margin_eur) AS margin_eur
            FROM kpi_finance_mission
            WHERE mission_id IN {mids_sql}
            """,
            mids_params,
        )
        row = df.iloc[0].to_dict()
        sold = float(row.get("sold_amount_eur") or 0)
        cost = float(row.get("cost_eur") or 0)
        margin = float(row.get("margin_eur") or 0)

        txt = (
            "Synthèse financière (périmètre visible) :\n"
            f"- CA vendu : {sold:,.0f} €\n"
            f"- Coûts : {cost:,.0f} €\n"
            f"- Marge : {margin:,.0f} €"
        ).replace(",", " ")

        df2 = _df(
            conn,
            f"""
            SELECT client_name, mission_code, mission_name, sold_amount_eur, cost_eur, margin_eur
            FROM kpi_finance_mission
            WHERE mission_id IN {mids_sql}
            ORDER BY margin_eur ASC
            LIMIT 10
            """,
            mids_params,
        )
        return {"text": txt, "tables": [{"title": "Top 10 missions (marge la plus faible)", "df": df2}]}

    # Fallback
    return {"text": "Je n’ai pas compris. Tape « aide » pour des exemples.", "tables": []}
