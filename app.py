import io
import zipfile
from datetime import date, datetime, timedelta

import pandas as pd
import streamlit as st

from src.config import load_settings
from src.db import get_conn
from src.security import verify_password
from src.services.init_db import ensure_schema
from src.services.seed_demo import seed_demo_if_empty, reset_demo, seed_from_csv

from pathlib import Path
from src.services.chatbot import ChatContext, answer_question


# =========================
# Helpers: Auth + RBAC
# =========================

def _fetch_user(conn, username: str):
    row = conn.execute(
        "SELECT id, username, password_hash, role, full_name, is_active FROM users WHERE username=?",
        (username,),
    ).fetchone()
    return dict(row) if row else None


def login_ui(conn):
    st.sidebar.subheader("Connexion")
    username = st.sidebar.text_input("Utilisateur", key="login_username")
    password = st.sidebar.text_input("Mot de passe", type="password", key="login_password")
    if st.sidebar.button("Se connecter", use_container_width=True):
        u = _fetch_user(conn, username.strip())
        if not u or int(u["is_active"]) != 1:
            st.sidebar.error("Compte introuvable ou inactif.")
            return
        if not verify_password(password, u["password_hash"]):
            st.sidebar.error("Mot de passe incorrect.")
            return
        st.session_state.user = {
            "id": int(u["id"]),
            "username": u["username"],
            "role": u["role"],
            "full_name": u["full_name"],
        }
        st.rerun()

    if load_settings().env == "local":
        with st.sidebar.expander("Aide / Comptes démo", expanded=True):
            st.markdown(
                """
**Comptes démo (chargés automatiquement au 1er lancement)**
- Admin : `admin / admin123`
- Board : `board1 / board123`
- Lead : `lead1 / lead123`
- Consultant : `consult1 / cons123`

**Où sont stockés les comptes ?**
- Démo (en clair) : `data/sample/users.csv`
- Base (hashé) : `data/app.db` (table `users.password_hash`)

**Comment modifier les mots de passe ?**
1) Modifier `data/sample/users.csv` (colonne `password_clear`)
2) Puis : **Admin > Reset Démo** (ou supprimer `data/app.db`)

**Confidentialité (RBAC)**
- Finance : **Board/Admin uniquement**
- Lead : opérationnel sur ses missions, **sans finance**
- Consultant : ses CRA / ses missions
- Chatbot : **lecture seule**
"""
            )




def logout_ui():
    u = st.session_state.get("user")
    if not u:
        return
    st.sidebar.caption(f"Connecté : **{u['full_name']}** ({u['role']})")
    if st.sidebar.button("Se déconnecter", use_container_width=True):
        st.session_state.pop("user", None)
        st.rerun()


def require_login():
    if "user" not in st.session_state:
        st.warning("Veuillez vous connecter (barre latérale).")
        st.stop()


def role():
    u = st.session_state.get("user")
    return u.get("role") if isinstance(u, dict) else None


def is_board():
    return role() in ("BOARD", "ADMIN")


def is_admin():
    return role() == "ADMIN"


def _mission_ids_for_user(conn) -> list[int]:
    """Liste des missions visibles selon RBAC."""
    u = st.session_state["user"]
    r = u["role"]
    uid = u["id"]

    if r in ("ADMIN", "BOARD"):
        rows = conn.execute("SELECT id FROM missions WHERE is_active=1").fetchall()
        return [int(x["id"]) for x in rows]

    if r == "LEAD":
        rows = conn.execute(
            """
            SELECT m.id
            FROM missions m
            JOIN mission_leads ml ON ml.mission_id = m.id
            WHERE m.is_active=1 AND ml.user_id=?
            """,
            (uid,),
        ).fetchall()
        return [int(x["id"]) for x in rows]

    # CONSULTANT: missions assignées OU sur lesquelles il a saisi du temps
    rows = conn.execute(
        """
        SELECT DISTINCT m.id
        FROM missions m
        LEFT JOIN mission_assignments ma ON ma.mission_id=m.id
        LEFT JOIN time_entries te ON te.mission_id=m.id
        WHERE m.is_active=1
          AND (
            ma.user_id=?
            OR te.user_id=?
          )
        """,
        (uid, uid),
    ).fetchall()
    return [int(x["id"]) for x in rows]


def _visible_user_ids(conn, mission_ids: list[int]) -> list[int]:
    """Liste des users visibles dans Capacités/CRA selon rôle."""
    u = st.session_state["user"]
    r = u["role"]
    uid = u["id"]

    if r in ("ADMIN", "BOARD"):
        rows = conn.execute("SELECT id FROM users WHERE is_active=1").fetchall()
        return [int(x["id"]) for x in rows]

    if r == "LEAD":
        if not mission_ids:
            return [uid]
        q = f"""
        SELECT DISTINCT u.id
        FROM users u
        LEFT JOIN mission_assignments ma ON ma.user_id=u.id
        LEFT JOIN time_entries te ON te.user_id=u.id
        WHERE u.is_active=1
          AND (
            (ma.mission_id IN ({",".join(["?"]*len(mission_ids))}))
            OR (te.mission_id IN ({",".join(["?"]*len(mission_ids))}))
          )
        """
        rows = conn.execute(q, tuple(mission_ids + mission_ids)).fetchall()
        ids = sorted({int(x["id"]) for x in rows} | {uid})
        return ids

    # CONSULTANT
    return [uid]


# =========================
# Helpers: Dates
# =========================

def _today():
    return date.today()


def _week_bounds(d: date):
    start = d - timedelta(days=d.weekday())
    end = start + timedelta(days=6)
    return start, end


def _parse_ymd(s: str) -> date:
    return datetime.strptime(s, "%Y-%m-%d").date()


# =========================
# Data access (pandas)
# =========================

def df_query(conn, sql: str, params=()):
    return pd.read_sql_query(sql, conn, params=params)


# =========================
# UI Sections
# =========================

def section_missions(conn):
    st.header("Missions")

    mids = _mission_ids_for_user(conn)
    if not mids:
        st.info("Aucune mission visible.")
        return

    q = f"""
    SELECT *
    FROM kpi_mission_hours
    WHERE mission_id IN ({",".join(["?"]*len(mids))})
    ORDER BY client_name, mission_code
    """
    df = df_query(conn, q, tuple(mids))

    # Détails sans finance par défaut
    cols = [
        "client_name", "mission_code", "mission_name", "status",
        "start_date", "end_date",
        "sold_days", "sold_hours", "consumed_hours", "consumed_pct",
    ]

    st.dataframe(df[cols], use_container_width=True, hide_index=True)

    # Board/Admin: bloc finance
    if is_board():
        st.subheader("Financier (Board/Admin)")
        qf = f"""
        SELECT *
        FROM kpi_finance_mission
        WHERE mission_id IN ({",".join(["?"]*len(mids))})
        ORDER BY client_name, mission_code
        """
        dff = df_query(conn, qf, tuple(mids))
        st.dataframe(
            dff[["client_name","mission_code","mission_name","sold_amount_eur","cost_eur","margin_eur"]],
            use_container_width=True,
            hide_index=True,
        )
    else:
        st.caption("Financier masqué (Board uniquement).")


def section_cra(conn):
    st.header("Temps / CRA")

    mids = _mission_ids_for_user(conn)
    visible_users = _visible_user_ids(conn, mids)

    # Filtres période
    view = st.radio("Vue", ["Jour", "Semaine", "Mois"], horizontal=True)
    base = _today()

    if view == "Jour":
        d0 = st.date_input("Date", value=base)
        start, end = d0, d0
    elif view == "Semaine":
        d0 = st.date_input("Semaine (date incluse)", value=base)
        start, end = _week_bounds(d0)
    else:
        d0 = st.date_input("Mois (date incluse)", value=base)
        start = date(d0.year, d0.month, 1)
        # fin de mois
        next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        end = next_month - timedelta(days=1)

    st.caption(f"Période : {start} → {end}")

    # Saisie (Consultant uniquement sur lui-même) + Admin sur tous
    can_write = role() in ("CONSULTANT", "ADMIN")
    if can_write:
        st.subheader("Saisie")
        with st.form("time_entry_form", clear_on_submit=True):
            entry_date = st.date_input("Date", value=base)
            if role() == "ADMIN":
                # admin peut saisir pour un autre (utile demo)
                users_df = df_query(conn, "SELECT id, full_name FROM users WHERE is_active=1 ORDER BY full_name")
                user_label = st.selectbox("Utilisateur", users_df["full_name"].tolist())
                user_id = int(users_df.loc[users_df["full_name"] == user_label, "id"].iloc[0])
            else:
                user_id = st.session_state["user"]["id"]

            category = st.selectbox("Catégorie", ["billable", "non_billable_client", "internal"])
            hours = st.selectbox("Durée", [1, 4, 8], format_func=lambda h: f"{h} h")
            desc = st.text_input("Description (optionnel)")

            # missions visibles + option "Aucune" (pour internal)
            missions_df = df_query(
                conn,
                f"SELECT mission_id AS id, mission_code || ' — ' || mission_name AS label FROM kpi_mission_hours "
                f"WHERE mission_id IN ({','.join(['?']*len(mids))}) ORDER BY label",
                tuple(mids),
            )

            labels = ["(aucune)"] + missions_df["label"].tolist()
            mission_pick = st.selectbox("Mission", labels, help="Laisser (aucune) pour internal.")
            mission_id = None
            if mission_pick != "(aucune)":
                mission_id = int(missions_df.loc[missions_df["label"] == mission_pick, "id"].iloc[0])

            submitted = st.form_submit_button("Enregistrer", use_container_width=True)
            if submitted:
                if category == "internal":
                    mission_id = None
                else:
                    if mission_id is None:
                        st.error("Mission obligatoire pour billable / non_billable_client.")
                        st.stop()

                try:
                    conn.execute(
                        """INSERT OR IGNORE INTO time_entries(entry_date, user_id, mission_id, category, hours, description)
                           VALUES (?, ?, ?, ?, ?, ?)""",
                        (entry_date.isoformat(), user_id, mission_id, category, int(hours), desc or None),
                    )
                    st.success("Saisie enregistrée (ou ignorée si doublon).")
                except Exception as e:
                    st.error(f"Erreur insertion : {e}")

    # Vue liste (RBAC)
    q_users = ",".join(["?"] * len(visible_users))
    params = tuple(visible_users) + (start.isoformat(), end.isoformat())

    df = df_query(
        conn,
        f"""
        SELECT
          te.entry_date,
          u.full_name AS user_name,
          COALESCE(m.code, '') AS mission_code,
          COALESCE(m.name, '') AS mission_name,
          te.category,
          te.hours,
          te.description
        FROM time_entries te
        JOIN users u ON u.id=te.user_id
        LEFT JOIN missions m ON m.id=te.mission_id
        WHERE te.user_id IN ({q_users})
          AND te.entry_date BETWEEN ? AND ?
        ORDER BY te.entry_date DESC, user_name
        """,
        params,
    )
    st.subheader("Historique")
    st.dataframe(df, use_container_width=True, hide_index=True)


def section_capacites(conn):
    st.header("Capacités")

    mids = _mission_ids_for_user(conn)
    user_ids = _visible_user_ids(conn, mids)

    # Période: semaine courante
    view = st.radio("Vue", ["Semaine", "Mois"], horizontal=True)
    d0 = st.date_input("Date de référence", value=_today())

    if view == "Semaine":
        start, end = _week_bounds(d0)
    else:
        start = date(d0.year, d0.month, 1)
        next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
        end = next_month - timedelta(days=1)


    # Charges loggées
    q_users = ",".join(["?"] * len(user_ids))
    df_load = df_query(
        conn,
        f"""
        SELECT day, user_id, user_name, logged_hours
        FROM kpi_user_load_daily
        WHERE user_id IN ({q_users})
          AND day BETWEEN ? AND ?
        """,
        tuple(user_ids) + (start.isoformat(), end.isoformat()),
    )

    # Capacité: 8h/j par défaut + overrides
    users_df = df_query(
        conn,
        f"SELECT id AS user_id, full_name AS user_name FROM users WHERE id IN ({q_users}) ORDER BY full_name",
        tuple(user_ids),
    )

    nb_days = (end - start).days + 1
    days = [(start + timedelta(days=i)).isoformat() for i in range(nb_days)]

    cap_rows = []
    for _, u in users_df.iterrows():
        for day in days:
            ov = conn.execute(
                "SELECT capacity_h FROM capacity_overrides WHERE user_id=? AND cap_date=?",
                (int(u["user_id"]), day),
            ).fetchone()
            cap_h = int(ov["capacity_h"]) if ov else 8
            cap_rows.append({"day": day, "user_id": int(u["user_id"]), "user_name": u["user_name"], "capacity_h": cap_h})

    df_cap = pd.DataFrame(cap_rows)

    # Merge + synthèse
    dfm = df_cap.merge(df_load, on=["day", "user_id", "user_name"], how="left").fillna({"logged_hours": 0})
    dfm["delta_h"] = dfm["capacity_h"] - dfm["logged_hours"]

    st.caption(f"Période : {start} → {end}")
    st.subheader("Modifier capacité (override)")
    if role() in ("ADMIN", "LEAD"):
        users_df2 = df_query(
            conn,
            f"SELECT id AS user_id, full_name AS user_name FROM users WHERE id IN ({q_users}) ORDER BY full_name",
            tuple(user_ids),
        )
        with st.form("cap_override", clear_on_submit=True):
            u_label = st.selectbox("Utilisateur", users_df2["user_name"].tolist())
            uid = int(users_df2.loc[users_df2["user_name"] == u_label, "user_id"].iloc[0])
            cap_date = st.date_input("Date")
            cap_h = st.number_input("Capacité (heures)", min_value=0, max_value=24, value=8, step=1)
            reason = st.text_input("Raison (optionnel)")
            submitted = st.form_submit_button("Enregistrer override", use_container_width=True)
            if submitted:
                conn.execute(
                    """
                    INSERT INTO capacity_overrides(user_id, cap_date, capacity_h, reason)
                    VALUES (?, ?, ?, ?)
                    ON CONFLICT(user_id, cap_date) DO UPDATE SET
                      capacity_h=excluded.capacity_h,
                      reason=excluded.reason
                    """,
                    (uid, cap_date.isoformat(), int(cap_h), (reason.strip() if reason and reason.strip() else None)),
                )
                st.success("Override enregistré.")
                st.rerun()
    else:
        st.caption("Seuls Admin/Lead peuvent modifier les overrides de capacité.")

    st.dataframe(
        dfm.sort_values(["user_name", "day"]),
        use_container_width=True,
        hide_index=True,
    )

    st.subheader("Résumé semaine")
    summary = (
        dfm.groupby(["user_id", "user_name"], as_index=False)
        .agg(capacity_h=("capacity_h", "sum"), logged_hours=("logged_hours", "sum"), delta_h=("delta_h", "sum"))
        .sort_values("user_name")
    )
    st.dataframe(summary, use_container_width=True, hide_index=True)


def section_alertes(conn):
    st.header("Alertes")

    # périmètres
    mids = _mission_ids_for_user(conn)
    visible_user_ids = _visible_user_ids(conn, mids if mids else [])

    tab_m, tab_c = st.tabs(["Missions", "Capacités"])

    # =========================
    # TAB 1 — Alertes Missions
    # =========================
    with tab_m:
        st.subheader("Alertes missions (vendu vs réalisé)")

        if not mids:
            st.info("Aucune mission visible.")
        else:
            q = f"""
            SELECT mission_code, mission_name, client_name,
                   sold_hours, consumed_hours, variance_hours, risk_level
            FROM kpi_alert_missions_risk
            WHERE mission_id IN ({",".join(["?"]*len(mids))})
            ORDER BY
              CASE risk_level
                WHEN 'overrun' THEN 3
                WHEN 'near_limit' THEN 2
                WHEN 'no_sold_load' THEN 1
                ELSE 0
              END DESC,
              variance_hours DESC
            """
            df = df_query(conn, q, tuple(mids))

            if df.empty:
                st.success("Aucune alerte mission détectée sur votre périmètre.")
            else:
                st.dataframe(df, use_container_width=True, hide_index=True)

    # =========================
    # TAB 2 — Alertes Capacités
    # =========================
    with tab_c:
        st.subheader("Alertes capacités (charge > capacité)")

        if not visible_user_ids:
            st.info("Aucun utilisateur visible pour les alertes capacité.")
        else:
            u_sql = ",".join(["?"] * len(visible_user_ids))

            sub_d, sub_w = st.tabs(["Jour", "Semaine"])

            with sub_d:
                dfc = df_query(
                    conn,
                    f"""
                    SELECT day, user_name, capacity_h, logged_hours, over_h
                    FROM kpi_alert_capacity_daily
                    WHERE user_id IN ({u_sql})
                    ORDER BY day DESC, over_h DESC
                    """,
                    tuple(visible_user_ids),
                )
                if dfc.empty:
                    st.success("Aucune surcapacité journalière détectée.")
                else:
                    st.dataframe(dfc, use_container_width=True, hide_index=True)

            with sub_w:
                dfw = df_query(
                    conn,
                    f"""
                    SELECT year, week, week_start_day, week_end_day,
                           user_name, capacity_h, logged_hours, over_h
                    FROM kpi_alert_capacity_weekly
                    WHERE user_id IN ({u_sql})
                    ORDER BY year DESC, week DESC, over_h DESC
                    """,
                    tuple(visible_user_ids),
                )
                if dfw.empty:
                    st.success("Aucune surcapacité hebdomadaire détectée.")
                else:
                    st.dataframe(dfw, use_container_width=True, hide_index=True)



def section_synthese(conn):
    st.header("Synthèse")

    mids = _mission_ids_for_user(conn)
    if not mids:
        st.info("Aucune mission visible.")
        return

    # KPI cards (déjà existant)
    q = f"""
    SELECT
      COUNT(*) AS missions_count,
      SUM(consumed_hours) AS consumed_hours,
      SUM(sold_hours) AS sold_hours
    FROM kpi_mission_hours
    WHERE mission_id IN ({",".join(["?"]*len(mids))})
    """
    k = df_query(conn, q, tuple(mids)).iloc[0].to_dict()
    missions_count = int(k.get("missions_count") or 0)
    consumed_hours = float(k.get("consumed_hours") or 0)
    sold_hours = float(k.get("sold_hours") or 0)
    pct = (consumed_hours / sold_hours * 100.0) if sold_hours > 0 else None

    c1, c2, c3 = st.columns(3)
    c1.metric("Missions visibles", missions_count)
    c2.metric("Heures consommées", f"{consumed_hours:.0f} h")
    c3.metric("Taux conso (vendu)", (f"{pct:.1f} %" if pct is not None else "N/A"))

    if is_board():
        st.subheader("Synthèse financière (Board/Admin)")
        qf = f"""
        SELECT
          SUM(sold_amount_eur) AS sold_amount_eur,
          SUM(cost_eur) AS cost_eur,
          SUM(margin_eur) AS margin_eur
        FROM kpi_finance_mission
        WHERE mission_id IN ({",".join(["?"]*len(mids))})
        """
        fin = df_query(conn, qf, tuple(mids)).iloc[0].to_dict()
        f1, f2, f3 = st.columns(3)
        f1.metric("CA vendu", f"{float(fin.get('sold_amount_eur') or 0):,.0f} €".replace(",", " "))
        f2.metric("Coûts estimés", f"{float(fin.get('cost_eur') or 0):,.0f} €".replace(",", " "))
        f3.metric("Marge estimée", f"{float(fin.get('margin_eur') or 0):,.0f} €".replace(",", " "))

        st.subheader("Simulations liées aux missions (Board/Admin)")
        qs = f"""
        SELECT simulation_id, client_name, project_name, mission_id, status, created_at,
                revenue_total, cost_total, margin_total, margin_pct
        FROM kpi_simulation_summary
        WHERE mission_id IN ({",".join(["?"]*len(mids))})
            AND status != 'archived'
        ORDER BY datetime(created_at) DESC
        """
        sims_df = df_query(conn, qs, tuple(mids))
        if sims_df.empty:
            st.caption("Aucune simulation liée aux missions visibles.")
        else:
            st.dataframe(sims_df, use_container_width=True, hide_index=True)

    st.divider()
    st.subheader("Chatbot (lecture seule)")

    # Contexte RBAC
    visible_user_ids = _visible_user_ids(conn, mids)
    ctx = ChatContext(
        role=role(),
        user_id=st.session_state["user"]["id"],
        username=st.session_state["user"]["username"],
        mission_ids=mids,
        visible_user_ids=visible_user_ids,
    )


    # UI
    examples = [
        "Où en est-on ?",
        "Quels projets sont à risque ?",
        "Qui est le plus chargé ?",
        "Répartition billable / internal ?",
    ]
    if is_board():
        examples.append("Synthèse finance ?")

    st.caption("Exemples : " + " • ".join([f"« {e} »" for e in examples]))

    q_user = st.text_input("Votre question", key="chat_question", placeholder="Tapez une question… (ex: Quels projets sont à risque ?)")
    colA, colB = st.columns([1, 1])
    ask = colA.button("Répondre", use_container_width=True)
    helpb = colB.button("Aide", use_container_width=True)

    if helpb:
        q_user = "aide"
        st.session_state["chat_question"] = q_user
        ask = True

    if ask:
        out = answer_question(conn, ctx, q_user)
        st.markdown(out["text"])

        for t in out.get("tables", []):
            st.markdown(f"**{t['title']}**")
            st.dataframe(t["df"], use_container_width=True, hide_index=True)

def section_simulation_board(conn):
    st.header("Simulation (Board) — Devis & Suivi")

    # RBAC strict
    if role() not in ("BOARD", "ADMIN"):
        st.error("Accès réservé Board/Admin.")
        return

    # ---- Helpers DB (local à la page)
    def _df(sql: str, params=()):
        return df_query(conn, sql, params)

    def _get_simulation(sim_id: int):
        r = conn.execute("SELECT * FROM simulations WHERE id=?", (sim_id,)).fetchone()
        return dict(r) if r else None

    def _save_simulation_header(sim_id, payload: dict) -> int:
        if sim_id is None:
            cur = conn.execute(
                """
                INSERT INTO simulations(
                    mission_id, client_name, project_name, sector, start_date, end_date,
                    author_user_id, status, notes
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    payload.get("mission_id"),
                    payload["client_name"],
                    payload["project_name"],
                    payload.get("sector"),
                    payload.get("start_date"),
                    payload.get("end_date"),
                    st.session_state["user"]["id"],
                    payload.get("status", "draft"),
                    payload.get("notes"),
                ),
            )
            conn.commit()  # ✅ IMPORTANT
            return int(cur.lastrowid)

        conn.execute(
            """
            UPDATE simulations
            SET mission_id=?, client_name=?, project_name=?, sector=?,
                start_date=?, end_date=?, status=?, notes=?
            WHERE id=?
            """,
            (
                payload.get("mission_id"),
                payload["client_name"],
                payload["project_name"],
                payload.get("sector"),
                payload.get("start_date"),
                payload.get("end_date"),
                payload.get("status", "draft"),
                payload.get("notes"),
                int(sim_id),
            ),
        )
        conn.commit()  # ✅ IMPORTANT
        return int(sim_id)

    def _overwrite_lines(sim_id: int, table: str, df_lines: pd.DataFrame, cols: list[str], defaults=None):
        if sim_id is None:
            st.error("Simulation introuvable en base. Enregistre d’abord l’en-tête (Client/Projet), puis réessaie.")
            return

        exists = conn.execute(
            "SELECT 1 FROM simulations WHERE id=?",
            (int(sim_id),),
        ).fetchone()

        if not exists:
            st.error("Simulation introuvable en base (ID invalide). Recharge la page et réessaie.")
            return

        """
        POC simple : on remplace tout (delete + insert)
        + sécurisation NOT NULL / CHECK via defaults
        """
        defaults = defaults or {}

        conn.execute(f"DELETE FROM {table} WHERE simulation_id=?", (int(sim_id),))

        if df_lines is None or df_lines.empty:
            conn.commit()
            return

        clean = df_lines.copy()
        clean = clean.replace({pd.NA: None})
        clean = clean.where(pd.notna(clean), None)

        rows = []
        for _, r in clean.iterrows():
            values = []
            all_empty = True

            for c in cols:
                v = r.get(c)

                # default si vide
                if v in (None, ""):
                    v = defaults.get(c, None)

                # normaliser NaN
                if isinstance(v, float) and pd.isna(v):
                    v = defaults.get(c, None)

                if v not in (None, "", 0, 0.0):
                    all_empty = False

                values.append(v)

            if all_empty:
                continue

            rows.append([sim_id] + values)

        if not rows:
            conn.commit()
            return

        placeholders = ",".join(["?"] * (1 + len(cols)))
        sql = f"INSERT INTO {table}(simulation_id,{','.join(cols)}) VALUES ({placeholders})"
        conn.executemany(sql, rows)
        conn.commit()



    # ---- Liste simulations (résumé via vue KPI)
    st.subheader("Mes simulations (Board)")

    sims = _df(
        """
        SELECT
          simulation_id, mission_id, client_name, project_name, sector, status, created_at,
          revenue_total, cost_total, margin_total, margin_pct
        FROM kpi_simulation_summary
        ORDER BY datetime(created_at) DESC
        """
    )

    colL, colR = st.columns([2, 1], vertical_alignment="top")

    with colR:
        st.markdown("### Actions")
        if st.button("➕ Nouvelle simulation", use_container_width=True, key="btn_new_sim"):
            st.session_state["sim_selected_id"] = None
            st.session_state["sim_mode"] = "edit"
            st.rerun()

        # Sélection
        options = ["(aucune)"]
        if not sims.empty:
            options += [f"#{int(r.simulation_id)} — {r.client_name} — {r.project_name} ({r.status})" for _, r in sims.iterrows()]

        pick = st.selectbox("Ouvrir une simulation", options, key="sim_pick")
        if pick != "(aucune)":
            sim_id = int(pick.split("—")[0].strip().replace("#", ""))
            st.session_state["sim_selected_id"] = sim_id
            st.session_state["sim_mode"] = "edit"
        if st.button("🔄 Rafraîchir", use_container_width=True, key="btn_refresh_sim"):
            st.rerun()

    with colL:
        if sims.empty:
            st.info("Aucune simulation pour le moment. Clique sur “Nouvelle simulation”.")
        else:
            show = sims.copy()
            # formatting friendly
            st.dataframe(
                show[
                    ["simulation_id", "client_name", "project_name", "status", "created_at",
                     "revenue_total", "cost_total", "margin_total", "margin_pct"]
                ],
                use_container_width=True,
                hide_index=True,
            )

    st.divider()
    st.subheader("Édition")

    sim_id = st.session_state.get("sim_selected_id", None)
    sim = _get_simulation(sim_id) if sim_id else None

    # ---- Header editor
    missions = _df(
        """
        SELECT m.id, c.name AS client, m.code, m.name
        FROM missions m
        JOIN clients c ON c.id=m.client_id
        WHERE m.is_active=1
        ORDER BY c.name, m.code
        """
    )
    mission_labels = ["(devis — sans mission)"]
    mission_map = {mission_labels[0]: None}
    for _, r in missions.iterrows():
        lbl = f"{r['client']} — {r['code']} — {r['name']}"
        mission_labels.append(lbl)
        mission_map[lbl] = int(r["id"])

    default_label = mission_labels[0]
    if sim and sim.get("mission_id"):
        # find label
        mid = int(sim["mission_id"])
        for lbl, v in mission_map.items():
            if v == mid:
                default_label = lbl
                break

    with st.form("sim_header_form", clear_on_submit=False):
        c1, c2, c3 = st.columns(3)
        with c1:
            mission_pick = st.selectbox(
                "Lier à une mission",
                mission_labels,
                index=mission_labels.index(default_label),
            )
            status_val = st.selectbox(
                "Statut",
                ["draft", "validated", "archived"],
                index=(["draft", "validated", "archived"].index(sim["status"]) if sim else 0),
            )

        with c2:
            client_name = st.text_input("Client (texte)", value=(sim["client_name"] if sim else ""))
            project_name = st.text_input("Projet", value=(sim["project_name"] if sim else ""))
            sector = st.text_input("Secteur (optionnel)", value=(sim.get("sector") if sim else ""))

        with c3:
            start_date = st.text_input(
                "Date début (YYYY-MM-DD, optionnel)",
                value=(sim.get("start_date") or "" if sim else ""),
            )
            end_date = st.text_input(
                "Date fin (YYYY-MM-DD, optionnel)",
                value=(sim.get("end_date") or "" if sim else ""),
            )
            notes = st.text_area("Notes (optionnel)", value=(sim.get("notes") or "" if sim else ""), height=90)

        save_header = st.form_submit_button("💾 Enregistrer en-tête", use_container_width=True)

        if save_header:
            if not client_name.strip() or not project_name.strip():
                st.error("Client + Projet sont obligatoires.")
                st.stop()

            payload = {
                "mission_id": mission_map[mission_pick],
                "client_name": client_name.strip(),
                "project_name": project_name.strip(),
                "sector": sector.strip() if sector.strip() else None,
                "start_date": start_date.strip() if start_date.strip() else None,
                "end_date": end_date.strip() if end_date.strip() else None,
                "status": status_val,
                "notes": notes.strip() if notes and notes.strip() else None,
            }

            new_id = _save_simulation_header(sim_id, payload)

            # Important : caster + fixer l'état
            st.session_state["sim_selected_id"] = int(new_id)
            st.session_state["sim_mode"] = "edit"

            st.success(f"En-tête enregistré (simulation #{new_id}).")
            st.rerun()

    sim_id = st.session_state.get("sim_selected_id", None)
    if not sim_id:
        st.info("Crée ou sélectionne une simulation pour éditer les lignes.")
        return

    # ---- Summary KPI (from view)
    summ = _df(
        """
        SELECT revenue_total, cost_total, margin_total, margin_pct, planned_hours, billable_hours
        FROM kpi_simulation_summary
        WHERE simulation_id=?
        """,
        (int(sim_id),),
    )
    if not summ.empty:
        s = summ.iloc[0].to_dict()
        k1, k2, k3, k4, k5 = st.columns(5)
        k1.metric("CA (total)", f"{float(s.get('revenue_total') or 0):,.0f} €".replace(",", " "))
        k2.metric("Coût (total)", f"{float(s.get('cost_total') or 0):,.0f} €".replace(",", " "))
        k3.metric("Marge", f"{float(s.get('margin_total') or 0):,.0f} €".replace(",", " "))
        k4.metric("% Marge", (f"{float(s.get('margin_pct')):.1f} %" if s.get("margin_pct") is not None else "N/A"))
        k5.metric("Heures prévues (billable)", f"{float(s.get('billable_hours') or 0):.0f} h")

    st.divider()
    sim_id = st.session_state.get("sim_selected_id")

    if sim_id is None:
        st.warning("Enregistre d’abord l’en-tête de la simulation avant d’ajouter des lignes.")
        return

    # ---- Lines editors (3 blocs)
    st.markdown("### Ressources internes")
    df_int = _df(
        """
        SELECT resource_name, grade, std_rate_per_hour, std_cost_per_hour,
               planned_days, hours_per_day, billable_ratio, non_billable_hours
        FROM simulation_internal_resources
        WHERE simulation_id=?
        """,
        (int(sim_id),),
    )

    df_int_edit = st.data_editor(
        df_int,
        use_container_width=True,
        num_rows="dynamic",
        key=f"sim_int_editor_{sim_id}",
        column_config={
            "billable_ratio": st.column_config.NumberColumn("billable_ratio (0..1)", min_value=0.0, max_value=1.0, step=0.05),
            "hours_per_day": st.column_config.NumberColumn("hours_per_day", min_value=0.0, max_value=24.0, step=0.5),
        },
    )

    st.markdown("### Ressources externes (sous-traitance)")
    df_ext = _df(
        """
        SELECT provider_name, role, buy_rate_per_day, sell_rate_per_day, planned_days, hours_per_day
        FROM simulation_external_resources
        WHERE simulation_id=?
        """,
        (int(sim_id),),
    )
    df_ext_edit = st.data_editor(
        df_ext,
        use_container_width=True,
        num_rows="dynamic",
        key=f"sim_ext_editor_{sim_id}",
        column_config={
            "hours_per_day": st.column_config.NumberColumn("hours_per_day", min_value=0.0, max_value=24.0, step=0.5),
        },
    )

    st.markdown("### Frais / Refacturation")
    df_cost = _df(
        """
        SELECT cost_type, label, cost_amount, refactured_amount
        FROM simulation_costs
        WHERE simulation_id=?
        """,
        (int(sim_id),),
    )
    df_cost_edit = st.data_editor(
        df_cost,
        use_container_width=True,
        num_rows="dynamic",
        key=f"sim_cost_editor_{sim_id}",
        column_config={
            "cost_type": st.column_config.SelectboxColumn(
                "cost_type",
                options=["fees", "expenses", "non_billable", "other"],
            )
        },
    )

    colS1, colS2 = st.columns([1, 1])
    with colS1:
        if st.button("💾 Enregistrer lignes", use_container_width=True, type="primary", key=f"btn_save_lines_{sim_id}"):
            # Garde-fou FK : la simulation doit exister
            exists = conn.execute("SELECT 1 FROM simulations WHERE id=?", (int(sim_id),)).fetchone()
            if not exists:
                st.error("Simulation introuvable en base. Enregistre d’abord l’en-tête (Client/Projet), puis réessaie.")
                st.stop()

            _overwrite_lines(
                int(sim_id),
                "simulation_internal_resources",
                df_int_edit,
                ["resource_name", "grade", "std_rate_per_hour", "std_cost_per_hour", "planned_days", "hours_per_day", "billable_ratio", "non_billable_hours"],
                defaults={
                    "std_rate_per_hour": 0.0,
                    "std_cost_per_hour": 0.0,
                    "planned_days": 0.0,
                    "hours_per_day": 8.0,
                    "billable_ratio": 1.0,
                    "non_billable_hours": 0.0,
                },
            )

            _overwrite_lines(
                int(sim_id),
                "simulation_external_resources",
                df_ext_edit,
                ["provider_name", "role", "buy_rate_per_day", "sell_rate_per_day", "planned_days", "hours_per_day"],
                defaults={
                    "buy_rate_per_day": 0.0,
                    "sell_rate_per_day": 0.0,
                    "planned_days": 0.0,
                    "hours_per_day": 8.0,
                },
            )

            _overwrite_lines(
                int(sim_id),
                "simulation_costs",
                df_cost_edit,
                ["cost_type", "label", "cost_amount", "refactured_amount"],
                defaults={
                    "cost_type": "expenses",  # valeur valide du CHECK
                    "cost_amount": 0.0,
                    "refactured_amount": 0.0,
                },
            )

            st.success("Lignes enregistrées.")
            st.rerun()

    with colS2:
        if st.button("🗑️ Supprimer la simulation", use_container_width=True, key=f"btn_delete_sim_{sim_id}"):
            conn.execute("DELETE FROM simulations WHERE id=?", (int(sim_id),))
            st.session_state["sim_selected_id"] = None
            st.success("Simulation supprimée.")
            st.rerun()

    st.caption("POC: l’enregistrement des lignes remplace l’ensemble des lignes (delete+insert).")


def section_admin(conn, settings):
    st.header("Admin")

    if not is_admin():
        st.error("Accès réservé Admin.")
        return

    tab_ref, tab_data = st.tabs(["Référentiels", "Données (démo/import)"])

    # ======================
    # Référentiels: Clients & Missions
    # ======================
    with tab_ref:
        st.subheader("Clients")
        with st.form("add_client", clear_on_submit=True):
            client_name = st.text_input("Nom du client")
            submitted = st.form_submit_button("Ajouter client", use_container_width=True)
            if submitted:
                if not client_name.strip():
                    st.error("Nom client obligatoire.")
                else:
                    conn.execute(
                        "INSERT OR IGNORE INTO clients(name, is_active) VALUES (?, 1)",
                        (client_name.strip(),),
                    )
                    st.success("Client ajouté (ou déjà existant).")
                    st.rerun()

        clients_df = df_query(conn, "SELECT id, name, is_active FROM clients ORDER BY name")
        st.dataframe(clients_df, use_container_width=True, hide_index=True)

        st.divider()
        st.subheader("Missions")

        # Select client
        clients = df_query(conn, "SELECT id, name FROM clients WHERE is_active=1 ORDER BY name")
        if clients.empty:
            st.info("Crée d’abord au moins un client.")
        else:
            with st.form("add_mission", clear_on_submit=True):
                client_label = st.selectbox("Client", clients["name"].tolist())
                client_id = int(clients.loc[clients["name"] == client_label, "id"].iloc[0])

                code = st.text_input("Code mission (unique)", placeholder="ex: M-2026-003")
                name = st.text_input("Nom mission", placeholder="ex: Mission DataOps")
                status = st.selectbox("Statut", ["pipeline", "ongoing", "paused", "done", "cancelled"], index=1)
                start_date = st.date_input("Date de début")
                end_date = st.date_input("Date de fin (optionnel)", value=None)

                sold_days = st.number_input("Jours vendus", min_value=0.0, value=0.0, step=1.0)

                # Champs finance (admin OK). Si tu veux les masquer à l’admin aussi, on peut.
                sold_amount_eur = st.number_input("CA vendu (€)", min_value=0.0, value=0.0, step=1000.0)
                daily_cost_eur = st.number_input("Coût/jour (€)", min_value=0.0, value=0.0, step=50.0)

                notes = st.text_area("Notes (optionnel)")
                submitted = st.form_submit_button("Ajouter mission", use_container_width=True)

                if submitted:
                    if not code.strip() or not name.strip():
                        st.error("Code + nom mission obligatoires.")
                    else:
                        conn.execute(
                            """
                            INSERT INTO missions(
                                client_id, code, name, status, start_date, end_date,
                                sold_days, sold_amount_eur, daily_cost_eur, is_active, notes
                            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 1, ?)
                            """,
                            (
                                client_id,
                                code.strip(),
                                name.strip(),
                                status,
                                start_date.isoformat(),
                                (end_date.isoformat() if end_date else None),
                                float(sold_days),
                                float(sold_amount_eur),
                                float(daily_cost_eur),
                                (notes.strip() if notes and notes.strip() else None),
                            ),
                        )
                        st.success("Mission ajoutée.")
                        st.rerun()

        missions_df = df_query(
            conn,
            """
            SELECT m.id, c.name AS client, m.code, m.name, m.status, m.start_date, m.end_date, m.sold_days, m.is_active
            FROM missions m
            JOIN clients c ON c.id=m.client_id
            ORDER BY c.name, m.code
            """,
        )
        st.dataframe(missions_df, use_container_width=True, hide_index=True)

    # ======================
    # Données: Reset demo + Import ZIP (existant)
    # ======================
    with tab_data:
        st.subheader("Réinitialiser données démo")
        if st.button("Reset Démo (efface et recharge data/sample)", type="primary", key="btn_reset_demo"):
            reset_demo(conn, settings=settings)
            st.success("Données démo réinitialisées.")
            st.rerun()

        st.subheader("Import CSV (ZIP)")
        st.caption(
            "Uploadez un ZIP contenant : users.csv, clients.csv, missions.csv, mission_leads.csv, "
            "mission_assignments.csv, time_entries.csv, capacity_overrides.csv (optionnel)."
        )
        up = st.file_uploader("ZIP", type=["zip"], key="uploader_zip_import_1")

        if up is not None:
            if st.button("Importer (remplace les données)", use_container_width=True, key="btn_import_zip_1"):
                import tempfile
                with tempfile.TemporaryDirectory() as tmp:
                    zbytes = io.BytesIO(up.read())
                    with zipfile.ZipFile(zbytes) as zf:
                        zf.extractall(tmp)

                    conn.execute("DELETE FROM time_entries;")
                    conn.execute("DELETE FROM mission_assignments;")
                    conn.execute("DELETE FROM mission_leads;")
                    conn.execute("DELETE FROM missions;")
                    conn.execute("DELETE FROM clients;")
                    conn.execute("DELETE FROM capacity_overrides;")
                    conn.execute("DELETE FROM users;")

                    seed_from_csv(conn, settings=settings, sample_dir=Path(tmp))

                st.success("Import terminé.")
                st.rerun()



# =========================
# Main
# =========================

def main():
    st.set_page_config(page_title="Pilotage Cabinet - POC", layout="wide")
    settings = load_settings()

    # Pré-auth (utile si déployé sur internet)
    if settings.env != "local":
        access_code = ""
        try:
            access_code = st.secrets.get("ACCESS_CODE", "")
        except Exception:
            access_code = ""

        if access_code:
            if st.session_state.get("access_ok") is not True:
                st.sidebar.subheader("Accès")
                entered = st.sidebar.text_input("Code d'accès", type="password")
                if st.sidebar.button("Valider", use_container_width=True, key="btn_access_code"):
                    st.session_state["access_ok"] = (entered == access_code)
                    st.rerun()
                if st.session_state.get("access_ok") is not True:
                    st.stop()

    with get_conn() as conn:
        ensure_schema(conn, settings=settings)
        seed_demo_if_empty(conn, settings=settings)

        # Sidebar auth
        if "user" not in st.session_state:
            login_ui(conn)
        else:
            logout_ui()

        st.sidebar.divider()

        # Navigation (RBAC)
        nav_all = ["Missions", "Temps / CRA", "Capacités", "Alertes", "Synthèse"]

        # Simulation Board/Admin uniquement
        if "user" in st.session_state and role() in ("BOARD", "ADMIN"):
            nav_all.append("Simulation (Board)")


        if "user" in st.session_state and is_admin():
            nav_all.append("Admin")


        choice = st.sidebar.radio("Navigation", nav_all)

        require_login()

        if choice == "Missions":
            section_missions(conn)
        elif choice == "Temps / CRA":
            section_cra(conn)
        elif choice == "Capacités":
            section_capacites(conn)
        elif choice == "Alertes":
            section_alertes(conn)
        elif choice == "Synthèse":
            section_synthese(conn)
        elif choice == "Simulation (Board)":
            section_simulation_board(conn)
        elif choice == "Admin":
            section_admin(conn, settings)
        else:
            st.info("Choisissez une section.")


if __name__ == "__main__":
    main()
