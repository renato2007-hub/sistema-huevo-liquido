"""
Módulo de Energía — registro de consumo de diésel (caldero y transporte MP)
y electricidad mensual, con conversión a Joules e indicadores de intensidad
energética por kg de producto.

Factores de conversión:
  1 galón diésel  = 138,874,000 J  = 138.874 MJ
  1 kWh           =   3,600,000 J  =   3.6   MJ
"""
import datetime
import streamlit as st
import pandas as pd

MJ_POR_GALON_DIESEL = 138.874   # MJ
MJ_POR_KWH          = 3.6       # MJ
USOS_DIESEL         = ["Caldero", "Transporte MP"]
MESES = ["Enero","Febrero","Marzo","Abril","Mayo","Junio",
          "Julio","Agosto","Septiembre","Octubre","Noviembre","Diciembre"]

CAPACIDAD_TANQUE_DIESEL = 16.0  # galones -- tanque (medidor visual) del caldero


def _num(df, col):
    if df.empty or col not in df.columns:
        return pd.Series(dtype=float)
    return pd.to_numeric(df[col], errors="coerce").fillna(0)


def _gauge_diesel(titulo, nivel, capacidad):
    """Barra vertical tipo medidor de tanque (mismo estilo que los tanques de
    semielaborado), para ver de un vistazo qué tan lleno está el tanque de
    diésel del caldero al momento de la lectura."""
    import plotly.graph_objects as go
    nivel = max(0.0, min(float(nivel), capacidad))
    libre = capacidad - nivel
    pct = (nivel / capacidad * 100) if capacidad else 0
    fig = go.Figure()
    fig.add_trace(go.Bar(
        x=[titulo], y=[nivel], marker_color="#f9a825",
        text=[f"{nivel:.1f} gal"], textposition="inside", hoverinfo="skip",
    ))
    if libre > 0:
        fig.add_trace(go.Bar(
            x=[titulo], y=[libre], marker_color="#f0f0f0",
            text=[f"{libre:.1f} gal libre"], textposition="inside",
            textfont={"color": "#aaa"}, hoverinfo="skip",
        ))
    fig.update_layout(
        barmode="stack", height=160,
        title={"text": f"{titulo}: {nivel:.1f}/{capacidad:.0f} gal ({pct:.0f}%)", "x": 0.5, "font": {"size": 12}},
        showlegend=False,
        margin={"l": 5, "r": 5, "t": 35, "b": 5},
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        yaxis={"range": [0, capacidad], "showgrid": False, "showticklabels": False},
        xaxis={"showticklabels": False},
    )
    st.plotly_chart(fig, use_container_width=True, config={"displayModeBar": False})


def _filtrar(df, desde, hasta):
    if df.empty or "fecha" not in df.columns:
        return df
    fechas = pd.to_datetime(df["fecha"], errors="coerce").dt.date
    return df[(fechas >= desde) & (fechas <= hasta)].copy()


def render(db, username, rol):
    st.title("⚡ Energía")

    tab_diesel, tab_elec, tab_resumen = st.tabs([
        "⛽ Diésel", "💡 Electricidad", "📊 Resumen energético"
    ])

    # ======================== DIESEL ========================
    with tab_diesel:
        col_form, col_hist = st.columns([1, 2])

        with col_form:
            with st.container(border=True):
                st.markdown("##### ⛽ Registrar consumo de diésel")
                fecha_d  = st.date_input("Fecha", value=datetime.date.today(), key="d_fecha")
                uso_d    = st.selectbox("Uso", USOS_DIESEL, key="d_uso")
                costo_gl = st.number_input("Costo por galón ($)", min_value=0.0, step=0.01, key="d_costo_galon")

                nivel_inicio = 0.0
                nivel_fin = 0.0
                galones_agregados = 0.0
                niveles_invalidos = False

                if uso_d == "Caldero":
                    st.caption(
                        f"Tanque del caldero (medidor visual, ~{CAPACIDAD_TANQUE_DIESEL:.0f} gal). "
                        "Anota el nivel al arrancar el caldero y de nuevo al terminar el turno — "
                        "el sistema calcula solo cuánto se consumió."
                    )
                    nivel_inicio = st.number_input(
                        "Nivel al inicio del turno (gal)", min_value=0.0,
                        max_value=CAPACIDAD_TANQUE_DIESEL, step=0.1, key="d_nivel_inicio",
                    )
                    _gauge_diesel("Inicio de turno", nivel_inicio, CAPACIDAD_TANQUE_DIESEL)

                    if nivel_inicio <= CAPACIDAD_TANQUE_DIESEL / 3:
                        st.warning(
                            f"🟡 El tanque está en el tercio inferior (≤ {CAPACIDAD_TANQUE_DIESEL / 3:.1f} gal) "
                            "— hay que agregarle diésel."
                        )

                    se_agrego = st.checkbox(
                        "¿Se agregó diésel al tanque durante el turno?", key="d_se_agrego",
                    )
                    if se_agrego:
                        galones_agregados = st.number_input(
                            "Galones agregados al tanque", min_value=0.0, step=0.1, key="d_gal_agregados",
                        )

                    nivel_fin = st.number_input(
                        "Nivel al final del turno (gal)", min_value=0.0,
                        max_value=CAPACIDAD_TANQUE_DIESEL, step=0.1, key="d_nivel_fin",
                    )
                    _gauge_diesel("Final de turno", nivel_fin, CAPACIDAD_TANQUE_DIESEL)

                    galones = round(nivel_inicio + galones_agregados - nivel_fin, 2)
                    if galones < 0:
                        niveles_invalidos = True
                        st.error(
                            f"Los niveles no cuadran: con {nivel_inicio:.1f} gal al inicio + "
                            f"{galones_agregados:.1f} agregados no se puede terminar en "
                            f"{nivel_fin:.1f} gal (es más de lo que había disponible). Revisa las lecturas."
                        )
                else:
                    galones = st.number_input("Galones usados", min_value=0.0, step=0.1, key="d_galones")

                obs_d = st.text_input("Observaciones (opcional)", "", key="d_obs")

                costo_total_d = galones * costo_gl
                mj_total_d    = galones * MJ_POR_GALON_DIESEL

                if galones > 0:
                    st.info(
                        f"**{galones:.2f} gal** = **{mj_total_d:,.1f} MJ** · "
                        f"Costo: **${costo_total_d:,.2f}**"
                    )

                if st.button("💾 Registrar", type="primary", use_container_width=True, key="d_guardar"):
                    if niveles_invalidos:
                        st.error("Corrige los niveles del tanque antes de guardar.")
                    elif galones <= 0:
                        st.error("Ingresa una cantidad de galones mayor a cero (o revisa los niveles del tanque).")
                    elif costo_gl <= 0:
                        st.error("Ingresa el costo por galón.")
                    else:
                        did = db.siguiente_id("registro_diesel", "DS", fecha_d)
                        db.append_row("registro_diesel", {
                            "diesel_id":    did,
                            "fecha":        fecha_d.isoformat(),
                            "uso":          uso_d,
                            "galones":      galones,
                            "costo_galon":  costo_gl,
                            "costo_total":  costo_total_d,
                            "mj_total":     mj_total_d,
                            "usuario":      username,
                            "observaciones":obs_d,
                            "nivel_inicio": nivel_inicio,
                            "nivel_fin": nivel_fin,
                            "galones_agregados": galones_agregados,
                        })
                        st.success(f"✅ {did}: {galones:.2f} gal ({uso_d}) registrados.")
                        st.rerun()

        with col_hist:
            st.markdown("##### 📋 Historial de diésel")
            diesel_df = db.get_df("registro_diesel")
            if diesel_df.empty:
                st.info("No hay registros de diésel todavía.")
            else:
                hoy = datetime.date.today()
                c1, c2 = st.columns(2)
                desde_d = c1.date_input("Desde", value=hoy.replace(day=1), key="dh_desde")
                hasta_d = c2.date_input("Hasta", value=hoy, key="dh_hasta")
                uso_f   = st.selectbox("Filtrar uso", ["Todos"] + USOS_DIESEL, key="dh_uso")

                df_dh = _filtrar(diesel_df, desde_d, hasta_d)
                if uso_f != "Todos":
                    df_dh = df_dh[df_dh["uso"] == uso_f]

                if df_dh.empty:
                    st.info("Sin registros en ese período.")
                else:
                    for col in ["galones","costo_total","mj_total","nivel_inicio","nivel_fin","galones_agregados"]:
                        if col in df_dh.columns:
                            df_dh[col] = pd.to_numeric(df_dh[col], errors="coerce").fillna(0)

                    m1,m2,m3 = st.columns(3)
                    m1.metric("Total galones", f"{df_dh['galones'].sum():,.2f} gal")
                    m2.metric("Total MJ", f"{df_dh['mj_total'].sum():,.1f} MJ")
                    m3.metric("Costo total", f"${df_dh['costo_total'].sum():,.2f}")

                    cols_hist_d = [c for c in [
                        "fecha", "uso", "nivel_inicio", "galones_agregados", "nivel_fin",
                        "galones", "costo_galon", "costo_total", "mj_total", "observaciones",
                    ] if c in df_dh.columns]
                    st.dataframe(
                        df_dh[cols_hist_d].rename(columns={
                            "nivel_inicio": "Nivel inicio (gal)",
                            "galones_agregados": "Gal. agregados",
                            "nivel_fin": "Nivel fin (gal)",
                        }).sort_values("fecha", ascending=False),
                        use_container_width=True, hide_index=True,
                    )

    # ======================== ELECTRICIDAD ========================
    with tab_elec:
        col_fe, col_he = st.columns([1, 2])

        with col_fe:
            with st.container(border=True):
                st.markdown("##### 💡 Registrar consumo eléctrico mensual")
                st.caption("Copia el valor de tu planilla eléctrica una vez al mes.")
                hoy = datetime.date.today()
                c1e, c2e = st.columns(2)
                anio_e = c1e.number_input("Año", min_value=2020, max_value=2050,
                                           value=hoy.year, step=1, key="e_anio")
                mes_e  = c2e.selectbox("Mes", range(1,13), key="e_mes",
                                        format_func=lambda x: MESES[x-1])
                kwh_e       = st.number_input("kWh consumidos", min_value=0.0, step=1.0, key="e_kwh")
                costo_tot_e = st.number_input("Costo total del mes ($)", min_value=0.0, step=0.01, key="e_costo")
                obs_e       = st.text_input("Observaciones (opcional)", "", key="e_obs")

                mj_elec = kwh_e * MJ_POR_KWH
                if kwh_e > 0:
                    costo_kwh = costo_tot_e / kwh_e if kwh_e > 0 else 0
                    st.info(
                        f"**{kwh_e:,.1f} kWh** = **{mj_elec:,.1f} MJ** · "
                        f"Costo/kWh: **${costo_kwh:,.4f}**"
                    )

                if st.button("💾 Registrar", type="primary", use_container_width=True, key="e_guardar"):
                    if kwh_e <= 0:
                        st.error("Ingresa el consumo en kWh.")
                    else:
                        # Verificar si ya existe registro para ese mes/año
                        elec_exist = db.get_df("registro_electricidad")
                        if not elec_exist.empty:
                            dup = elec_exist[
                                (elec_exist["anio"].astype(str) == str(int(anio_e))) &
                                (elec_exist["mes"].astype(str) == str(int(mes_e)))
                            ]
                            if not dup.empty:
                                st.error(f"⚠️ Ya existe un registro para {MESES[int(mes_e)-1]} {int(anio_e)}. Usa Corregir/eliminar si necesitas actualizarlo.")
                                st.stop()
                        eid = db.siguiente_id("registro_electricidad", "EL",
                                              datetime.date(int(anio_e), int(mes_e), 1))
                        db.append_row("registro_electricidad", {
                            "elec_id":      eid,
                            "anio":         int(anio_e),
                            "mes":          int(mes_e),
                            "kwh":          kwh_e,
                            "costo_total":  costo_tot_e,
                            "mj_total":     mj_elec,
                            "usuario":      username,
                            "observaciones":obs_e,
                        })
                        st.success(f"✅ {eid}: {kwh_e:,.1f} kWh de {MESES[int(mes_e)-1]} {int(anio_e)} registrados.")
                        st.rerun()

        with col_he:
            st.markdown("##### 📋 Historial de electricidad")
            elec_df = db.get_df("registro_electricidad")
            if elec_df.empty:
                st.info("No hay registros de electricidad todavía.")
            else:
                for col in ["kwh","costo_total","mj_total","anio","mes"]:
                    elec_df[col] = pd.to_numeric(elec_df[col], errors="coerce").fillna(0)
                elec_df["periodo"] = elec_df.apply(
                    lambda r: f"{MESES[int(r['mes'])-1]} {int(r['anio'])}", axis=1
                )
                elec_df["orden"] = elec_df["anio"] * 100 + elec_df["mes"]
                m1,m2,m3 = st.columns(3)
                m1.metric("Total kWh", f"{elec_df['kwh'].sum():,.1f}")
                m2.metric("Total MJ", f"{elec_df['mj_total'].sum():,.1f}")
                m3.metric("Costo total", f"${elec_df['costo_total'].sum():,.2f}")
                elec_sorted = elec_df.sort_values("orden", ascending=False)
                st.dataframe(
                    elec_sorted[["periodo","kwh","costo_total","mj_total","observaciones"]],
                    use_container_width=True, hide_index=True,
                )

    # ======================== RESUMEN ENERGÉTICO ========================
    with tab_resumen:
        hoy = datetime.date.today()
        c1r, c2r = st.columns(2)
        desde_r = c1r.date_input("Desde", value=hoy.replace(day=1), key="r_desde")
        hasta_r = c2r.date_input("Hasta", value=hoy, key="r_hasta")

        diesel_r  = _filtrar(db.get_df("registro_diesel"), desde_r, hasta_r)
        # Para electricidad filtrar por meses que caen en el período
        elec_all  = db.get_df("registro_electricidad")
        if not elec_all.empty:
            for col in ["anio","mes","kwh","costo_total","mj_total"]:
                elec_all[col] = pd.to_numeric(elec_all[col], errors="coerce").fillna(0)
            elec_all["fecha_mes"] = pd.to_datetime(
                elec_all.apply(lambda r: f"{int(r['anio'])}-{int(r['mes']):02d}-01", axis=1)
            ).dt.date
            elec_r = elec_all[
                (elec_all["fecha_mes"] >= desde_r.replace(day=1)) &
                (elec_all["fecha_mes"] <= hasta_r.replace(day=1))
            ]
        else:
            elec_r = pd.DataFrame()

        mj_diesel  = _num(diesel_r, "mj_total").sum()
        mj_elec    = _num(elec_r, "mj_total").sum()
        mj_total   = mj_diesel + mj_elec
        costo_d    = _num(diesel_r, "costo_total").sum()
        costo_e    = _num(elec_r, "costo_total").sum()
        costo_eng  = costo_d + costo_e
        gal_total  = _num(diesel_r, "galones").sum()
        kwh_total  = _num(elec_r, "kwh").sum()

        # kg producidos en el período (para intensidad energética)
        prod_df = db.get_df("produccion_semielaborados")
        if not prod_df.empty:
            fechas_prod = pd.to_datetime(prod_df["fecha"], errors="coerce").dt.date
            prod_r = prod_df[(fechas_prod >= desde_r) & (fechas_prod <= hasta_r)]
            kg_periodo = pd.to_numeric(prod_r["kg_real"], errors="coerce").fillna(0).sum()
        else:
            kg_periodo = 0

        mj_por_kg    = mj_total / kg_periodo if kg_periodo > 0 else 0
        costo_eng_kg = costo_eng / kg_periodo if kg_periodo > 0 else 0

        with st.container(border=True):
            st.markdown("##### ⚡ Indicadores energéticos del período")
            r1,r2,r3,r4 = st.columns(4)
            r1.metric("Energía total", f"{mj_total:,.1f} MJ")
            r2.metric("MJ / kg producido", f"{mj_por_kg:,.2f}")
            r3.metric("Costo energía total", f"${costo_eng:,.2f}")
            r4.metric("Costo energía / kg", f"${costo_eng_kg:,.4f}")

        st.write("")
        col_d1, col_d2 = st.columns(2)

        with col_d1:
            with st.container(border=True):
                st.markdown("**⛽ Diésel**")
                st.metric("Galones", f"{gal_total:,.2f}")
                st.metric("MJ", f"{mj_diesel:,.1f}")
                st.metric("Costo", f"${costo_d:,.2f}")
                if not diesel_r.empty:
                    # desglose por uso
                    diesel_r["galones"] = _num(diesel_r, "galones")
                    diesel_r["mj_total"] = _num(diesel_r, "mj_total")
                    por_uso = diesel_r.groupby("uso").agg(
                        galones=("galones","sum"), mj=("mj_total","sum")
                    ).reset_index()
                    st.dataframe(por_uso, use_container_width=True, hide_index=True)

        with col_d2:
            with st.container(border=True):
                st.markdown("**💡 Electricidad**")
                st.metric("kWh", f"{kwh_total:,.1f}")
                st.metric("MJ", f"{mj_elec:,.1f}")
                st.metric("Costo", f"${costo_e:,.2f}")

        if mj_total > 0:
            st.write("")
            with st.container(border=True):
                st.markdown("**Composición energética (MJ)**")
                import plotly.graph_objects as go
                fig = go.Figure(go.Pie(
                    labels=["Diésel","Electricidad"],
                    values=[mj_diesel, mj_elec],
                    hole=0.5,
                    marker_colors=["#f9a825","#1565c0"],
                    textinfo="label+percent+value",
                    hovertemplate="%{label}: %{value:,.1f} MJ (%{percent})<extra></extra>",
                ))
                fig.update_layout(
                    showlegend=False, height=280,
                    margin=dict(t=10,b=10,l=10,r=10),
                    paper_bgcolor="rgba(0,0,0,0)",
                )
                st.plotly_chart(fig, use_container_width=True)
                st.caption(
                    f"Equivalencias: {gal_total:,.2f} gal diésel + {kwh_total:,.1f} kWh eléctricos "
                    f"= {mj_total:,.1f} MJ totales"
                )
