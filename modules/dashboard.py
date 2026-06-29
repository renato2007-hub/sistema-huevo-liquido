import datetime
import pandas as pd
import plotly.graph_objects as go
import streamlit as st

from utils.horas_trabajo import (
    calcular_horas_sesion,
    clasificar_horas_por_dia,
    feriados_como_set,
    compensaciones_como_set,
)
from utils.pdf_horas_personal import generar_pdf_horas_personal
from utils.permisos import ve_costos

VERDE  = "#2e7d32"
NARANJA = "#D9740C"
DORADO = "#f9a825"
TEAL   = "#00695c"
AZUL   = "#1565c0"
MORADO = "#6a1b9a"


# ── helpers ───────────────────────────────────────────────────────────────────
def _num(df, col):
    if df.empty or col not in df.columns:
        return pd.Series(dtype=float)
    return pd.to_numeric(df[col], errors="coerce").fillna(0)


def _filtrar_por_fecha(df, desde, hasta):
    if df.empty or "fecha" not in df.columns:
        return df
    fechas = pd.to_datetime(df["fecha"], errors="coerce").dt.date
    return df[(fechas >= desde) & (fechas <= hasta)].copy()


def _periodo_anterior(desde, hasta):
    delta = (hasta - desde).days + 1
    return desde - datetime.timedelta(days=delta), hasta - datetime.timedelta(days=delta)


def _delta_pct(actual, anterior):
    if anterior and anterior != 0:
        return f"{((actual - anterior) / abs(anterior)) * 100:+.1f}%"
    return None


def _calcular_kpis_resumen(produccion, limpieza, desde, hasta):
    prod = _filtrar_por_fecha(produccion, desde, hasta)
    limp = _filtrar_por_fecha(limpieza, desde, hasta)
    kg   = _num(prod, "kg_real").sum()
    ct   = _num(prod, "costo_total").sum()
    agua = _num(prod, "agua_litros").sum() + _num(limp, "agua_litros").sum()
    return {"kg_total": kg, "costo_total": ct, "costo_por_kg": ct/kg if kg else 0, "agua_total": agua}


def _saldo_actual(movimientos, tipo, item_id):
    if movimientos.empty:
        return 0
    m = movimientos[movimientos.get("item_tipo", pd.Series(dtype=str)) == tipo] if "item_tipo" in movimientos else movimientos
    m = m[m.get("item_id", pd.Series(dtype=str)) == item_id] if "item_id" in m else m
    if m.empty:
        return 0
    entradas = _num(m[m.get("tipo_movimiento","") == "entrada"], "cantidad").sum()
    salidas  = _num(m[m.get("tipo_movimiento","") == "salida"],  "cantidad").sum()
    mermas   = _num(m[m.get("tipo_movimiento","") == "merma"],   "cantidad").sum()
    return entradas - salidas - mermas


def _kpi_card(container, icono, etiqueta, valor, delta=None, ayuda=None, sufijo=None):
    with container:
        st.metric(etiqueta, f"{icono} {valor}", delta=delta, help=ayuda)
        if sufijo:
            st.caption(sufijo)


def _grafico_dona(labels, values, titulo):
    labels = list(labels); values = list(values)
    if not values or sum(values) == 0:
        st.info("Sin datos para el gráfico.")
        return
    colores = [VERDE, NARANJA, DORADO, TEAL, AZUL, MORADO, "#c0392b", "#7f8c8d"]
    fig = go.Figure(go.Pie(
        labels=labels, values=values, hole=0.5,
        marker_colors=colores[:len(labels)],
        textinfo="label+percent", hovertemplate="%{label}: %{value:,.1f}<extra></extra>",
    ))
    fig.update_layout(
        title_text=titulo, showlegend=False,
        margin=dict(t=40, b=10, l=10, r=10), height=260,
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
    )
    st.plotly_chart(fig, use_container_width=True)


def _grafico_barras(serie, color=VERDE, horizontal=False):
    if serie.empty or serie.sum() == 0:
        st.info("Sin datos para el gráfico.")
        return
    if horizontal:
        fig = go.Figure(go.Bar(y=serie.index.tolist(), x=serie.values.tolist(),
                               orientation="h", marker_color=color))
    else:
        fig = go.Figure(go.Bar(x=serie.index.tolist(), y=serie.values.tolist(),
                               marker_color=color))
    fig.update_layout(margin=dict(t=20, b=20, l=10, r=10), height=220,
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
    st.plotly_chart(fig, use_container_width=True)


def _grafico_barras_apiladas(df, columnas):
    colores = [VERDE, NARANJA, DORADO, TEAL]
    fig = go.Figure()
    for col, color in zip(columnas, colores):
        if col in df.columns:
            fig.add_trace(go.Bar(name=col.replace("horas_","").title(),
                                 x=df.index.tolist(), y=df[col].tolist(),
                                 marker_color=color))
    fig.update_layout(barmode="stack", margin=dict(t=20, b=40, l=10, r=10), height=280,
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
    st.plotly_chart(fig, use_container_width=True)


def _grafico_linea_tiempo(df, col_fecha, col_valor, color=VERDE, titulo=""):
    if df.empty:
        st.info("Sin datos para el gráfico.")
        return
    df = df.copy()
    df[col_fecha] = pd.to_datetime(df[col_fecha], errors="coerce")
    df[col_valor] = pd.to_numeric(df[col_valor], errors="coerce").fillna(0)
    df = df.dropna(subset=[col_fecha]).sort_values(col_fecha)
    consumo_dia = df.groupby(col_fecha)[col_valor].sum().reset_index()
    fig = go.Figure(go.Scatter(x=consumo_dia[col_fecha], y=consumo_dia[col_valor],
                               mode="lines+markers", line_color=color, fill="tozeroy",
                               fillcolor="rgba(0,0,0,0.05)"))
    fig.update_layout(title_text=titulo, margin=dict(t=30, b=20, l=10, r=10), height=220,
                      paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
    st.plotly_chart(fig, use_container_width=True)


# ── render ─────────────────────────────────────────────────────────────────────
def render(db, username, rol):
    st.title("📊 Dashboard")

    with st.container(border=True):
        col_periodo, col_fechas = st.columns([1, 2])
        periodo = col_periodo.selectbox("Período", ["Hoy", "Últimos 7 días", "Este mes", "Personalizado"])
        hoy = datetime.date.today()
        if periodo == "Hoy":
            desde, hasta = hoy, hoy
        elif periodo == "Últimos 7 días":
            desde, hasta = hoy - datetime.timedelta(days=6), hoy
        elif periodo == "Este mes":
            desde, hasta = hoy.replace(day=1), hoy
        else:
            c1, c2 = col_fechas.columns(2)
            desde = c1.date_input("Desde", value=hoy - datetime.timedelta(days=6), key="dash_desde")
            hasta = c2.date_input("Hasta", value=hoy, key="dash_hasta")
        if periodo != "Personalizado":
            col_fechas.markdown(f"#### 📅 {desde.strftime('%d/%m/%Y')} → {hasta.strftime('%d/%m/%Y')}")

    if desde > hasta:
        st.error("La fecha 'Desde' no puede ser posterior a 'Hasta'.")
        return

    # ── cargar tablas ──────────────────────────────────────────────────────────
    produccion      = db.get_df("produccion_semielaborados")
    consumo_mp      = db.get_df("consumo_mp_produccion")
    personal_detalle= db.get_df("produccion_personal")  # tabla legado
    jornadas_personal = db.get_df("jornadas_personal")   # nueva tabla centralizada
    pasteurizacion  = db.get_df("pasteurizacion_envasado")
    movimientos     = db.get_df("movimientos_envases_insumos")
    limpieza        = db.get_df("limpieza_desinfeccion")
    mermas_mp       = db.get_df("mermas_mp")
    presentaciones  = db.get_df("presentaciones")
    insumos         = db.get_df("insumos")
    personal_cat    = db.get_df("personal")
    recepciones_mp  = db.get_df("recepciones_mp")
    categorias      = db.get_df("categorias_huevo")

    prod_f       = _filtrar_por_fecha(produccion, desde, hasta)
    consumo_f    = _filtrar_por_fecha(consumo_mp, desde, hasta)
    past_f       = _filtrar_por_fecha(pasteurizacion, desde, hasta)
    mov_f        = _filtrar_por_fecha(movimientos, desde, hasta)
    limpieza_f   = _filtrar_por_fecha(limpieza, desde, hasta)
    mermas_mp_f  = _filtrar_por_fecha(mermas_mp, desde, hasta)

    cubetas_total      = _num(consumo_f, "cubetas_usadas").sum()
    costo_huevo_total  = _num(consumo_f, "costo_total_aplicado").sum()

    prod_costos = prod_f.copy()
    if not prod_costos.empty:
        for col in ["costo_total","kg_real","costo_huevo","costo_insumos","costo_mano_obra","agua_litros","cascara_real_kg"]:
            prod_costos[col] = pd.to_numeric(prod_costos[col], errors="coerce").fillna(0)
    costo_huevo_periodo = prod_costos["costo_huevo"].sum() if not prod_costos.empty else 0.0
    costo_insumos_periodo = prod_costos["costo_insumos"].sum() if not prod_costos.empty else 0.0
    kg_total_periodo    = prod_costos["kg_real"].sum() if not prod_costos.empty else 0.0
    agua_produccion     = prod_costos["agua_litros"].sum() if not prod_costos.empty else 0.0
    agua_limpieza       = _num(limpieza_f, "agua_litros").sum()

    # horas hombre del período — prioridad: jornadas_personal (nuevo módulo)
    # con fallback a produccion_personal + supervision_diaria (tablas legado)
    hh_df = pd.DataFrame()
    if not jornadas_personal.empty:
        # nueva fuente centralizada
        hh_df = jornadas_personal[["personal_id","fecha","horas","horas_nocturnas","costo_calculado"]].copy()
    else:
        # fallback a fuente legado
        if not personal_detalle.empty and not produccion.empty:
            hh_df = personal_detalle.merge(
                produccion[["lote_semielaborado_id","fecha"]], on="lote_semielaborado_id", how="left"
            )
        superv = db.get_df("supervision_diaria")
        if not superv.empty:
            hh_df = pd.concat(
                [hh_df, superv[["personal_id","fecha","horas","horas_nocturnas","costo_calculado"]]],
                ignore_index=True,
            )
    hh_f = _filtrar_por_fecha(hh_df, desde, hasta)
    horas_totales_periodo = _num(hh_f, "horas").sum()

    # Costo MO real desde jornadas del período
    costo_mo_periodo = _num(hh_f, "costo_calculado").sum()

    # Costo de energía del período
    diesel_periodo = db.get_df("registro_diesel")
    elec_periodo   = db.get_df("registro_electricidad")
    diesel_f = _filtrar_por_fecha(diesel_periodo, desde, hasta)
    costo_diesel_f = _num(diesel_f, "costo_total").sum()
    costo_elec_f   = 0.0
    if not elec_periodo.empty:
        for c in ["anio","mes","costo_total","mj_total","kwh"]:
            elec_periodo[c] = pd.to_numeric(elec_periodo[c], errors="coerce").fillna(0)
        elec_periodo["fecha_mes"] = pd.to_datetime(
            elec_periodo.apply(lambda r: f"{int(r['anio'])}-{int(r['mes']):02d}-01", axis=1)
        ).dt.date
        elec_f_mask = (
            (elec_periodo["fecha_mes"] >= desde.replace(day=1)) &
            (elec_periodo["fecha_mes"] <= hasta.replace(day=1))
        )
        costo_elec_f = elec_periodo[elec_f_mask]["costo_total"].sum()
    costo_energia_periodo = costo_diesel_f + costo_elec_f

    # Costo envases desde pasteurizacion
    costo_env_periodo = sum(
        _num(past_f, c).sum() for c in
        ["costo_envases","costo_tapas","costo_etiquetas","costo_cartones","costo_liners"]
        if not past_f.empty and c in past_f.columns
    )

    costo_total_periodo = costo_huevo_periodo + costo_insumos_periodo + costo_mo_periodo + costo_env_periodo + costo_energia_periodo
    costo_por_kg_general = costo_total_periodo / kg_total_periodo if kg_total_periodo > 0 else 0
    hh_por_kg = horas_totales_periodo / kg_total_periodo if kg_total_periodo > 0 else 0

    # ======================== RESUMEN EJECUTIVO ========================
    desde_ant, hasta_ant = _periodo_anterior(desde, hasta)
    kpis_ant = _calcular_kpis_resumen(produccion, limpieza, desde_ant, hasta_ant)

    st.markdown("### 🎯 Resumen del período")
    st.caption(f"Comparado contra el período anterior equivalente: {desde_ant.strftime('%d/%m')} → {hasta_ant.strftime('%d/%m')}")

    # KPIs del resumen: solo productos terminados (pasteurizacion_envasado)
    # El costo incluye envases, etiquetas, tapas, etc. — no semielaborados en el tanque
    if not past_f.empty and not produccion.empty:
        past_resumen = past_f.merge(
            produccion[["lote_semielaborado_id", "tipo_producto"]],
            on="lote_semielaborado_id", how="left",
        )
        past_resumen["kg_usado"]        = pd.to_numeric(past_resumen["kg_usado"], errors="coerce").fillna(0)
        past_resumen["costo_total_lote"] = pd.to_numeric(past_resumen.get("costo_total", 0), errors="coerce").fillna(0)
        past_resumen["pasteurizado_bool"] = past_resumen["pasteurizado"].astype(str).str.upper().isin(["TRUE","1","SI","SÍ"])

        def _etiq_producto(tipo, past_bool):
            if past_bool:
                sufijo = "a" if tipo in ("Clara","Yema") else "o"
                return f"{tipo} pasteurizad{sufijo}"
            return f"{tipo} sin pasteurizar"

        past_resumen["etiqueta"] = past_resumen.apply(
            lambda r: _etiq_producto(r.get("tipo_producto",""), r["pasteurizado_bool"]), axis=1
        )
        resumen_prod = past_resumen.groupby("etiqueta").agg(
            kg=("kg_usado","sum"), costo_total=("costo_total_lote","sum")
        ).reset_index()
        resumen_prod = resumen_prod[resumen_prod["kg"] > 0]

        if not resumen_prod.empty:
            cols_prod = st.columns(min(len(resumen_prod), 4))
            for col, (_, row) in zip(cols_prod, resumen_prod.iterrows()):
                icono = "✅" if "sin" not in row["etiqueta"] else "🔴"
                costo_kg = row["costo_total"] / row["kg"] if row["kg"] > 0 else 0
                _kpi_card(col, icono, row["etiqueta"],
                          f"{row['kg']:,.1f} kg",
                          sufijo=f"Costo prom. ${costo_kg:,.3f}/kg" if ve_costos(rol) else None)

    st.write("")
    r1, r2, r3, r4 = st.columns(4)
    _kpi_card(r1, "⏱️", "Horas-hombre / kg", f"{hh_por_kg:,.3f}", ayuda="Horas totales trabajadas ÷ kg producidos")
    _kpi_card(r2, "💧", "Agua total (L)", f"{agua_produccion + agua_limpieza:,.0f}", _delta_pct(agua_produccion+agua_limpieza, kpis_ant["agua_total"]))
    if ve_costos(rol):
        _kpi_card(r3, "💰", "Costo total producción", f"${costo_total_periodo:,.2f}", _delta_pct(costo_total_periodo, kpis_ant["costo_total"]))
        _kpi_card(r4, "💲", "Costo promedio /kg", f"${costo_por_kg_general:,.3f}", _delta_pct(costo_por_kg_general, kpis_ant["costo_por_kg"]))

    st.write("")

    tabs = st.tabs([
        "🥚 Materia prima", "🏭 Producción y costos", "👷 Personal",
        "📦 Insumos y envases", "♻️ Residuos y mermas", "💧 Agua", "💲 Costos por turno",
    ])

    # ======================== TAB: MATERIA PRIMA ========================
    with tabs[0]:
        st.caption("⚠️ Esta pestaña muestra el inventario disponible ahora mismo, no cambia según el período seleccionado.")

        with st.container(border=True):
            st.markdown("##### 🥚 Inventario actual de huevo en bodega MP")
            if recepciones_mp.empty:
                st.info("No hay recepciones registradas.")
            else:
                inv_huevo = recepciones_mp.copy()
                inv_huevo["cubetas_saldo"] = pd.to_numeric(inv_huevo["cubetas_saldo"], errors="coerce").fillna(0)
                inv_huevo["costo_cubeta"]  = pd.to_numeric(inv_huevo["costo_cubeta"], errors="coerce").fillna(0)
                inv_huevo = inv_huevo[inv_huevo["cubetas_saldo"] > 0].copy()
                if inv_huevo.empty:
                    st.info("No hay saldo disponible en bodega MP.")
                else:
                    if not categorias.empty:
                        inv_huevo = inv_huevo.merge(
                            categorias[["categoria_id","nombre"]].rename(columns={"nombre":"categoria_nombre"}),
                            on="categoria_id", how="left",
                        )
                        inv_huevo["categoria_nombre"] = inv_huevo["categoria_nombre"].fillna(inv_huevo["categoria_id"])
                    else:
                        inv_huevo["categoria_nombre"] = inv_huevo["categoria_id"]

                    c1, c2 = st.columns(2)
                    _kpi_card(c1, "📦", "Cubetas disponibles", f"{inv_huevo['cubetas_saldo'].sum():,.0f}")
                    _kpi_card(c2, "🥚", "Huevos disponibles", f"{inv_huevo['cubetas_saldo'].sum()*30:,.0f}")
                    st.write("")

                    resumen_cat = inv_huevo.groupby("categoria_nombre").agg(cubetas=("cubetas_saldo","sum")).reset_index()
                    col_tabla, col_graf = st.columns([3, 2])
                    col_tabla.dataframe(resumen_cat, use_container_width=True, hide_index=True)
                    with col_graf:
                        _grafico_dona(resumen_cat["categoria_nombre"], resumen_cat["cubetas"], "Cubetas por categoría")
                    with st.expander("Ver detalle por lote"):
                        st.dataframe(
                            inv_huevo[["recepcion_id","categoria_nombre","cubetas_saldo","costo_cubeta","fecha_vencimiento"]],
                            use_container_width=True, hide_index=True,
                        )

        st.write("")

        with st.container(border=True):
            st.markdown("##### 📈 Consumo de cubetas en el período seleccionado")
            if consumo_f.empty:
                st.info("Sin consumo de MP en el período.")
            else:
                consumo_f2 = consumo_f.copy()
                consumo_f2["cubetas_usadas"] = pd.to_numeric(consumo_f2["cubetas_usadas"], errors="coerce").fillna(0)
                if "fecha" not in consumo_f2.columns and not produccion.empty:
                    consumo_f2 = consumo_f2.merge(
                        produccion[["lote_semielaborado_id","fecha"]], on="lote_semielaborado_id", how="left"
                    )
                elif "fecha" not in consumo_f2.columns:
                    consumo_f2["fecha"] = pd.NaT
                # si el merge creó fecha_x / fecha_y, usar la de produccion
                if "fecha_y" in consumo_f2.columns:
                    consumo_f2["fecha"] = consumo_f2["fecha_y"]
                elif "fecha_x" in consumo_f2.columns:
                    consumo_f2["fecha"] = consumo_f2["fecha_x"]
                _grafico_linea_tiempo(consumo_f2, "fecha", "cubetas_usadas", VERDE, "Cubetas consumidas por día")
                total_cubetas_f = consumo_f2["cubetas_usadas"].sum()
                st.caption(f"Total período: **{total_cubetas_f:,.0f} cubetas** — {total_cubetas_f*30:,.0f} huevos")

    # ======================== TAB: PRODUCCION Y COSTOS ========================
    with tabs[1]:
        with st.container(border=True):
            st.markdown("##### 🥚 Huevo procesado en el período")
            c1, c2, c3 = st.columns(3)
            _kpi_card(c1, "📦", "Cubetas usadas", f"{cubetas_total:,.0f}")
            _kpi_card(c2, "🥚", "Huevos procesados", f"{cubetas_total*30:,.0f}")
            if ve_costos(rol):
                _kpi_card(c3, "💲", "Costo de huevo consumido", f"${costo_huevo_total:,.2f}")

        st.write("")

        with st.container(border=True):
            st.markdown("##### 💰 Producción por tipo de producto")
            if prod_costos.empty:
                st.info("No hay producción de semielaborados en este período.")
            else:
                por_tipo = prod_costos.groupby("tipo_producto").agg(
                    kg_real=("kg_real","sum"), costo_total=("costo_total","sum"),
                ).reset_index()
                por_tipo["costo_por_kg"] = por_tipo.apply(
                    lambda r: r["costo_total"]/r["kg_real"] if r["kg_real"] > 0 else 0, axis=1
                )
                col_tabla, col_graf = st.columns([3, 2])
                col_tabla.dataframe(
                    por_tipo.rename(columns={"tipo_producto":"Producto","kg_real":"Kg producidos",
                                             "costo_total":"Costo total","costo_por_kg":"Costo/kg"}),
                    use_container_width=True, hide_index=True,
                )
                with col_graf:
                    _grafico_dona(por_tipo["tipo_producto"], por_tipo["kg_real"], "Kg por tipo")

        st.write("")

        if ve_costos(rol):
            with st.container(border=True):
                st.markdown("##### 🔢 Composición del costo de producción")
                if prod_costos.empty:
                    st.info("Sin datos de costos en este período.")
                else:
                    costo_huevo_sum   = costo_huevo_periodo
                    costo_insumos_sum = costo_insumos_periodo
                    costo_mo_sum      = costo_mo_periodo
                    costo_env_sum     = costo_env_periodo
                    pct = lambda v: (v/costo_total_periodo*100) if costo_total_periodo > 0 else 0
                    c1, c2, c3, c4 = st.columns(4)
                    _kpi_card(c1, "🥚", "Huevo", f"${costo_huevo_sum:,.2f}", ayuda=f"{pct(costo_huevo_sum):.0f}% del costo total")
                    _kpi_card(c2, "📦", "Envases/etiquetas", f"${costo_env_sum:,.2f}", ayuda=f"{pct(costo_env_sum):.0f}% del costo total")
                    _kpi_card(c3, "🧴", "Insumos", f"${costo_insumos_sum:,.2f}", ayuda=f"{pct(costo_insumos_sum):.0f}% del costo total")
                    _kpi_card(c4, "👷", "Mano de obra", f"${costo_mo_sum:,.2f}", ayuda=f"{pct(costo_mo_sum):.0f}% del costo total")
                    _grafico_dona(["Huevo","Envases","Insumos","Mano de obra"],
                                  [costo_huevo_sum, costo_env_sum, costo_insumos_sum, costo_mo_sum],
                                  "Composición del costo")

                    superv_periodo = _filtrar_por_fecha(db.get_df("supervision_diaria"), desde, hasta)
                    costo_superv = _num(superv_periodo, "costo_calculado").sum()
                    st.info(f"Costo de supervisión/calidad (overhead, no incluido en costo/kg de lotes): **${costo_superv:,.2f}**")

        st.write("")
        with st.container(border=True):
            st.markdown("##### ⚖️ Balance de masa promedio")
            st.caption("(líquido + cáscara real) ÷ peso bruto teórico. 100% = sin pérdidas sin explicar.")
            if not prod_costos.empty and "balance_masa_pct" in prod_costos.columns:
                balance_vals = pd.to_numeric(prod_costos["balance_masa_pct"], errors="coerce").dropna()
                if not balance_vals.empty:
                    fuera = balance_vals[(balance_vals > 100.5) | (balance_vals < 85)]
                    c1, c2 = st.columns(2)
                    _kpi_card(c1, "⚖️", "Balance promedio", f"{balance_vals.mean():.1f}%")
                    _kpi_card(c2, "🔍", "Lotes fuera de rango (85-100%)", f"{len(fuera)} de {len(balance_vals)}")
                    if not fuera.empty:
                        st.warning("Hay lotes con balance fuera de rango — revísalos en Producción → 'Teórico vs. real'.")

        st.write("")
        col_past, col_pend = st.columns(2)
        with col_past:
            with st.container(border=True):
                st.markdown("##### 🧪 Kg pasteurizados por tipo")
                if past_f.empty or produccion.empty:
                    st.info("Sin pasteurizaciones en el período.")
                else:
                    past_tipo = past_f.merge(produccion[["lote_semielaborado_id","tipo_producto"]], on="lote_semielaborado_id", how="left")
                    past_tipo["kg_usado"] = pd.to_numeric(past_tipo["kg_usado"], errors="coerce").fillna(0)
                    _grafico_barras(past_tipo.groupby("tipo_producto")["kg_usado"].sum(), color=VERDE)

        with col_pend:
            with st.container(border=True):
                st.markdown("##### 🛢️ Pendiente de pasteurizar (kg en tanque)")
                if prod_costos.empty:
                    st.info("Sin producción en el período.")
                else:
                    prod_saldo = prod_f.copy()
                    prod_saldo["kg_saldo"] = pd.to_numeric(prod_saldo["kg_saldo"], errors="coerce").fillna(0)
                    _grafico_barras(prod_saldo.groupby("tipo_producto")["kg_saldo"].sum(), color=DORADO)

    # ======================== TAB: PERSONAL ========================
    with tabs[2]:
        for _col in ["horas", "horas_nocturnas", "costo_calculado"]:
            if _col in hh_f.columns:
                hh_f[_col] = pd.to_numeric(hh_f[_col], errors="coerce").fillna(0)
            else:
                hh_f[_col] = 0.0

        costo_por_persona     = hh_f.groupby("personal_id")["costo_calculado"].sum() if not hh_f.empty else pd.Series(dtype=float)
        nocturnas_por_persona = hh_f.groupby("personal_id")["horas_nocturnas"].sum() if not hh_f.empty else pd.Series(dtype=float)

        if not hh_f.empty:
            por_persona_dia = hh_f.groupby(["personal_id","fecha"])["horas"].sum().reset_index()
            feriados_set    = feriados_como_set(db.get_df("feriados"))
            compensados_set = compensaciones_como_set(db.get_df("compensaciones_feriado"))
            por_persona_dia = clasificar_horas_por_dia(por_persona_dia, feriados_set, compensados_set)
            resumen_por_id  = por_persona_dia.groupby("personal_id").agg(
                horas_normales=("horas_normales","sum"), horas_extras=("horas_extras","sum"),
                horas_dobles=("horas_dobles","sum"), horas_compensadas=("horas_compensadas","sum"),
                horas_totales=("horas","sum"),
            )
        else:
            resumen_por_id = pd.DataFrame(columns=["horas_normales","horas_extras","horas_dobles","horas_compensadas","horas_totales"])

        if personal_cat.empty:
            st.info("Configura personal en Catálogos → Personal.")
        else:
            with st.container(border=True):
                st.markdown("##### 👷 Horas de trabajo por persona")
                st.caption("Normales ≤8h día normal · Extras >8h · Dobles = feriado sin compensar · Compensadas = feriado con descanso · Nocturnas = 19:00-05:00")

                activos = personal_cat[personal_cat.get("activo","TRUE").astype(str).str.upper() != "FALSE"].copy()
                reporte = activos.set_index("personal_id").join(resumen_por_id, how="left")
                for col in ["horas_normales","horas_extras","horas_dobles","horas_compensadas","horas_totales"]:
                    reporte[col] = reporte[col].fillna(0)
                reporte["costo"] = reporte.index.map(costo_por_persona).fillna(0)
                reporte["horas_nocturnas"] = reporte.index.map(nocturnas_por_persona).fillna(0)
                reporte["trabajo"] = reporte["horas_totales"] > 0
                reporte = reporte.reset_index().sort_values("horas_totales", ascending=False)

                c1,c2,c3,c4,c5,c6 = st.columns(6)
                _kpi_card(c1,"🕐","H. normales",f"{reporte['horas_normales'].sum():,.1f}")
                _kpi_card(c2,"⏱️","H. extras",f"{reporte['horas_extras'].sum():,.1f}")
                _kpi_card(c3,"✖️2","H. dobles",f"{reporte['horas_dobles'].sum():,.1f}")
                _kpi_card(c4,"🔁","H. compensadas",f"{reporte['horas_compensadas'].sum():,.1f}")
                _kpi_card(c5,"🌙","H. nocturnas",f"{reporte['horas_nocturnas'].sum():,.1f}")
                if ve_costos(rol):
                    _kpi_card(c6,"💲","Costo M.O.",f"${reporte['costo'].sum():,.2f}")

                r1b, r2b = st.columns(2)
                _kpi_card(r1b,"⏱️","Horas-hombre / kg",f"{hh_por_kg:,.3f}", ayuda="Horas totales ÷ kg producidos en el período")
                _kpi_card(r2b,"👷","Total horas trabajadas",f"{horas_totales_periodo:,.1f} h")

                sin_trabajar = reporte[~reporte["trabajo"]]
                if not sin_trabajar.empty:
                    st.warning(f"⚠️ {len(sin_trabajar)} persona(s) sin registros: {', '.join(sin_trabajar['nombre'])}")

                st.write("")
                columnas_mostrar = [c for c in ["nombre","cargo","horas_normales","horas_extras",
                                                 "horas_dobles","horas_compensadas","horas_nocturnas","horas_totales","costo"]
                                    if c in reporte.columns]
                st.dataframe(reporte[columnas_mostrar], use_container_width=True, hide_index=True)

                st.write("")
                st.markdown("**Desglose de horas por persona**")
                _grafico_barras_apiladas(reporte.set_index("nombre"),
                                         ["horas_normales","horas_extras","horas_dobles","horas_compensadas"])
                st.write("")

                # ── KPI de género (indicador GRI 405-1) ──────────────────
                with st.container(border=True):
                    st.markdown("##### 🚺🚹 Indicador de género (GRI 405-1)")
                    if "genero" not in personal_cat.columns or personal_cat["genero"].isna().all():
                        st.info("Agrega la columna **genero** (F/M) en el catálogo de personal del Sheet para ver este indicador.")
                    else:
                        if not hh_f.empty:
                            gen_df = hh_f.merge(
                                personal_cat[["personal_id","genero"]], on="personal_id", how="left"
                            )
                            gen_df["genero"] = gen_df["genero"].fillna("No especificado").str.upper()
                            horas_por_genero = gen_df.groupby("genero")["horas"].apply(
                                lambda s: pd.to_numeric(s, errors="coerce").fillna(0).sum()
                            )
                            horas_f = horas_por_genero.get("F", 0)
                            horas_m = horas_por_genero.get("M", 0)
                            horas_total_gen = horas_f + horas_m
                            pct_f = horas_f / horas_total_gen * 100 if horas_total_gen > 0 else 0
                            pct_m = horas_m / horas_total_gen * 100 if horas_total_gen > 0 else 0

                            personas_f = gen_df[gen_df["genero"]=="F"]["personal_id"].nunique()
                            personas_m = gen_df[gen_df["genero"]=="M"]["personal_id"].nunique()

                            gc1, gc2, gc3, gc4 = st.columns(4)
                            gc1.metric("🚺 Horas mujeres", f"{horas_f:,.1f} h", f"{pct_f:.1f}%")
                            gc2.metric("🚹 Horas hombres", f"{horas_m:,.1f} h", f"{pct_m:.1f}%")
                            gc3.metric("🚺 Mujeres activas", f"{personas_f}")
                            gc4.metric("🚹 Hombres activos", f"{personas_m}")

                            if horas_total_gen > 0:
                                import plotly.graph_objects as go
                                fig_g = go.Figure(go.Pie(
                                    labels=["Mujeres (F)","Hombres (M)"],
                                    values=[horas_f, horas_m],
                                    hole=0.55,
                                    marker_colors=["#e91e63","#1565c0"],
                                    textinfo="label+percent",
                                ))
                                fig_g.update_layout(
                                    showlegend=False, height=200,
                                    margin=dict(t=10,b=10,l=10,r=10),
                                    paper_bgcolor="rgba(0,0,0,0)",
                                )
                                st.plotly_chart(fig_g, use_container_width=True)
                        else:
                            st.info("Sin registros de jornadas en el período seleccionado.")

                st.write("")
                pdf_bytes = generar_pdf_horas_personal(reporte.to_dict("records"), desde, hasta)
                st.download_button("📄 Descargar reporte PDF de horas",
                                   data=pdf_bytes,
                                   file_name=f"horas_personal_{desde.isoformat()}_a_{hasta.isoformat()}.pdf",
                                   mime="application/pdf", use_container_width=True)

    # ======================== TAB: INSUMOS Y ENVASES ========================
    with tabs[3]:
        # ── Inventario actual ──
        st.markdown("##### 📊 Inventario actual (no cambia con el período)")
        col_env_inv, col_ins_inv = st.columns(2)
        with col_env_inv:
            with st.container(border=True):
                st.markdown("**📦 Envases disponibles**")
                if presentaciones.empty:
                    st.info("Sin presentaciones configuradas.")
                else:
                    filas_env = [{"Presentación": r["nombre"], "Saldo": _saldo_actual(movimientos,"envase",r["presentacion_id"])}
                                 for _, r in presentaciones.iterrows()]
                    df_env = pd.DataFrame(filas_env)
                    st.dataframe(df_env, use_container_width=True, hide_index=True)
                    _grafico_barras(df_env.set_index("Presentación")["Saldo"], color=NARANJA)

        with col_ins_inv:
            with st.container(border=True):
                st.markdown("**🧴 Insumos disponibles**")
                if insumos.empty:
                    st.info("Sin insumos configurados.")
                else:
                    filas_ins = [{"Insumo": r["nombre"], "Unidad": r.get("unidad",""),
                                  "Saldo": _saldo_actual(movimientos,"insumo",r["insumo_id"])}
                                 for _, r in insumos.iterrows()]
                    df_ins = pd.DataFrame(filas_ins)
                    st.dataframe(df_ins, use_container_width=True, hide_index=True)
                    _grafico_barras(df_ins.set_index("Insumo")["Saldo"], color=TEAL)

        st.divider()

        # ── Consumo en el período ──
        st.markdown("##### 📈 Consumo en el período seleccionado")
        col_ins_c, col_env_c = st.columns(2)

        with col_ins_c:
            with st.container(border=True):
                st.markdown("**🧴 Insumos consumidos**")
                salidas_insumos = pd.DataFrame()
                if not mov_f.empty and "item_tipo" in mov_f.columns:
                    salidas_insumos = mov_f[(mov_f["item_tipo"]=="insumo") & (mov_f["tipo_movimiento"]=="salida")].copy()
                if salidas_insumos.empty:
                    st.info("Sin consumo de insumos en el período.")
                else:
                    salidas_insumos["cantidad"]   = pd.to_numeric(salidas_insumos["cantidad"], errors="coerce").fillna(0)
                    salidas_insumos["costo_total"]= pd.to_numeric(salidas_insumos.get("costo_total",0), errors="coerce").fillna(0)
                    if not insumos.empty:
                        salidas_insumos = salidas_insumos.merge(
                            insumos[["insumo_id","nombre","unidad"]].rename(columns={"insumo_id":"item_id"}),
                            on="item_id", how="left",
                        )
                        salidas_insumos["nombre"] = salidas_insumos["nombre"].fillna(salidas_insumos["item_id"])
                    else:
                        salidas_insumos["nombre"] = salidas_insumos.get("item_id","")
                    resumen_ins = salidas_insumos.groupby("nombre").agg(
                        cantidad=("cantidad","sum"), costo=("costo_total","sum")
                    ).reset_index().sort_values("cantidad", ascending=False)
                    if ve_costos(rol):
                        st.caption(f"Costo total: **${resumen_ins['costo'].sum():,.2f}**")
                    st.dataframe(resumen_ins, use_container_width=True, hide_index=True)
                    _grafico_barras(resumen_ins.set_index("nombre")["cantidad"], color=TEAL)

        with col_env_c:
            with st.container(border=True):
                st.markdown("**📦 Envases consumidos**")
                salidas_env = pd.DataFrame()
                if not mov_f.empty and "item_tipo" in mov_f.columns:
                    salidas_env = mov_f[(mov_f["item_tipo"]=="envase") & (mov_f["tipo_movimiento"]=="salida")].copy()
                if salidas_env.empty:
                    st.info("Sin consumo de envases en el período.")
                else:
                    salidas_env["cantidad"] = pd.to_numeric(salidas_env["cantidad"], errors="coerce").fillna(0)
                    if not presentaciones.empty:
                        salidas_env = salidas_env.merge(
                            presentaciones[["presentacion_id","nombre"]].rename(columns={"presentacion_id":"item_id"}),
                            on="item_id", how="left",
                        )
                        salidas_env["nombre"] = salidas_env["nombre"].fillna(salidas_env.get("item_id",""))
                    else:
                        salidas_env["nombre"] = salidas_env.get("item_id","")
                    resumen_env = salidas_env.groupby("nombre")["cantidad"].sum().reset_index()
                    resumen_env.columns = ["Presentación","Unidades"]
                    st.caption(f"Total: **{int(resumen_env['Unidades'].sum()):,} unidades**")
                    st.dataframe(resumen_env, use_container_width=True, hide_index=True)
                    _grafico_barras(resumen_env.set_index("Presentación")["Unidades"], color=NARANJA)
                    _grafico_linea_tiempo(
                        salidas_env.rename(columns={"nombre":"item"}), "fecha", "cantidad",
                        color=NARANJA, titulo="Consumo de envases por día"
                    )

    # ======================== TAB: RESIDUOS Y MERMAS ========================
    with tabs[4]:
        cascara_total        = prod_costos["cascara_real_kg"].sum() if not prod_costos.empty else 0.0
        huevos_danados_total = _num(mermas_mp_f, "huevos_danados").sum()
        costo_mermas_mp      = _num(mermas_mp_f, "costo_estimado").sum()
        mermas_semi          = db.get_df("mermas_semielaborado")
        mermas_semi_f        = _filtrar_por_fecha(mermas_semi, desde, hasta)
        kg_semi_desechado    = _num(mermas_semi_f, "kg_desechado").sum()
        costo_semi_desechado = _num(mermas_semi_f, "costo_estimado").sum()

        m1,m2,m3,m4 = st.columns(4)
        _kpi_card(m1,"🥚","Cáscara generada",f"{cascara_total:,.1f} kg")
        _kpi_card(m2,"💔","Huevos dañados en bodega",f"{huevos_danados_total:,.0f}")
        _kpi_card(m3,"🧪","Clara/yema desechada",f"{kg_semi_desechado:,.1f} kg")
        if ve_costos(rol):
            _kpi_card(m4,"💲","Costo perdido (huevo + semi)",f"${(costo_mermas_mp+costo_semi_desechado):,.2f}")

        if kg_total_periodo > 0:
            pct_cascara = cascara_total / kg_total_periodo * 100
            st.caption(f"La cáscara representa el **{pct_cascara:.1f}%** del peso total producido.")

        st.write("")
        col_r1, col_r2 = st.columns(2)

        with col_r1:
            with st.container(border=True):
                st.markdown("##### 🧪 Clara/yema desechada por tipo")
                if mermas_semi_f.empty:
                    st.info("Sin desechos de semielaborados en el período.")
                else:
                    mermas_semi_v = mermas_semi_f.copy()
                    if not produccion.empty:
                        mermas_semi_v = mermas_semi_v.merge(produccion[["lote_semielaborado_id","tipo_producto"]], on="lote_semielaborado_id", how="left")
                    st.dataframe(mermas_semi_v[[c for c in ["fecha","lote_semielaborado_id","tipo_producto","kg_desechado","causa"] if c in mermas_semi_v.columns]],
                                 use_container_width=True, hide_index=True)
                    if "tipo_producto" in mermas_semi_v.columns:
                        _grafico_dona(mermas_semi_v.groupby("tipo_producto")["kg_desechado"].sum().index,
                                      mermas_semi_v.groupby("tipo_producto")["kg_desechado"].sum().values,
                                      "Kg desechados por tipo")

        with col_r2:
            with st.container(border=True):
                st.markdown("##### 📦 Envases dañados")
                mermas_env = pd.DataFrame()
                if not mov_f.empty and "item_tipo" in mov_f.columns:
                    mermas_env = mov_f[(mov_f["item_tipo"]=="envase") & (mov_f["tipo_movimiento"]=="merma")].copy()
                if mermas_env.empty:
                    st.info("Sin envases dañados en el período.")
                else:
                    mermas_env["cantidad"] = pd.to_numeric(mermas_env["cantidad"], errors="coerce").fillna(0)
                    if not presentaciones.empty:
                        mermas_env = mermas_env.merge(presentaciones[["presentacion_id","nombre"]].rename(columns={"presentacion_id":"item_id"}), on="item_id", how="left")
                        mermas_env["nombre"] = mermas_env["nombre"].fillna(mermas_env.get("item_id",""))
                    resumen_me = mermas_env.groupby("nombre")["cantidad"].sum().reset_index()
                    resumen_me.columns = ["Presentación","Unidades dañadas"]
                    st.dataframe(resumen_me, use_container_width=True, hide_index=True)
                    _grafico_barras(resumen_me.set_index("Presentación")["Unidades dañadas"], color="#c0392b", horizontal=True)

    # ======================== TAB: AGUA ========================
    with tabs[5]:
        with st.container(border=True):
            st.markdown("##### 💧 Agua usada en el período")
            c1,c2,c3 = st.columns(3)
            _kpi_card(c1,"🏭","En producción",f"{agua_produccion:,.0f} L")
            _kpi_card(c2,"🧽","En limpieza/desinfección",f"{agua_limpieza:,.0f} L")
            _kpi_card(c3,"💧","Total",f"{agua_produccion+agua_limpieza:,.0f} L",
                      _delta_pct(agua_produccion+agua_limpieza, kpis_ant["agua_total"]))

            if kg_total_periodo > 0 and (agua_produccion+agua_limpieza) > 0:
                litros_por_kg = (agua_produccion+agua_limpieza) / kg_total_periodo
                st.caption(f"Eficiencia hídrica: **{litros_por_kg:,.2f} L/kg** producido")

            if agua_produccion + agua_limpieza > 0:
                _grafico_dona(["Producción","Limpieza/desinfección"],
                              [agua_produccion, agua_limpieza], "Distribución del agua")

        st.write("")
        with st.container(border=True):
            st.markdown("##### 📈 Consumo de agua por día")
            col_a1, col_a2 = st.columns(2)
            with col_a1:
                agua_prod_diaria = prod_f.copy() if not prod_f.empty else pd.DataFrame()
                if not agua_prod_diaria.empty and "agua_litros" in agua_prod_diaria.columns:
                    agua_prod_diaria["agua_litros"] = pd.to_numeric(agua_prod_diaria["agua_litros"], errors="coerce").fillna(0)
                    _grafico_linea_tiempo(agua_prod_diaria, "fecha", "agua_litros", AZUL, "Agua en producción (L/día)")
                else:
                    st.info("Sin datos de agua en producción.")
            with col_a2:
                if not limpieza_f.empty and "agua_litros" in limpieza_f.columns:
                    limp_agua = limpieza_f.copy()
                    limp_agua["agua_litros"] = pd.to_numeric(limp_agua["agua_litros"], errors="coerce").fillna(0)
                    _grafico_linea_tiempo(limp_agua, "fecha", "agua_litros", TEAL, "Agua en limpieza (L/día)")
                else:
                    st.info("Sin datos de agua en limpieza.")

    # ======================== TAB: COSTOS POR TURNO ========================
    with tabs[6]:
        import plotly.graph_objects as go

        jornadas     = db.get_df("jornadas_personal")
        turno_cat    = db.get_df("turnos")
        past_full    = db.get_df("pasteurizacion_envasado")
        limp_full    = db.get_df("limpieza_desinfeccion")
        limp_det     = db.get_df("produccion_insumos")
        consumo_full = db.get_df("consumo_mp_produccion")

        def _num_col(df, col):
            if df.empty or col not in df.columns:
                return pd.Series(dtype=float)
            return pd.to_numeric(df[col], errors="coerce").fillna(0)

        def _filtrar(df, desde, hasta):
            if df.empty or "fecha" not in df.columns:
                return df
            fechas = pd.to_datetime(df["fecha"], errors="coerce").dt.date
            return df[(fechas >= desde) & (fechas <= hasta)].copy()

        sub_turno, sub_dia, sub_tendencia = st.tabs([
            "🔄 Por turno", "📅 Por día", "📈 Tendencia del período"
        ])

        # ── helpers comunes ──────────────────────────────────────────────
        def _costo_mo_turno(jornadas_df, personal_cat_df, fecha, turno_id):
            """Costo de operarios (no JEF_) del turno específico."""
            if jornadas_df.empty:
                return 0.0
            f = jornadas_df[
                (jornadas_df["fecha"].astype(str) == str(fecha)) &
                (jornadas_df["turno_id"].astype(str) == str(turno_id))
            ]
            if f.empty:
                return 0.0
            # excluir JEF_* (overhead diario)
            if not personal_cat_df.empty and "personal_id" in personal_cat_df.columns:
                ids_jefes = set(personal_cat_df[personal_cat_df["personal_id"].str.startswith("JEF_")]["personal_id"])
                f = f[~f["personal_id"].isin(ids_jefes)]
            return _num_col(f, "costo_calculado").sum()

        def _costo_mo_overhead_dia(jornadas_df, personal_cat_df, fecha):
            """Costo de JEF_* para ese día (overhead)."""
            if jornadas_df.empty:
                return 0.0
            f = jornadas_df[jornadas_df["fecha"].astype(str) == str(fecha)]
            if f.empty or personal_cat_df.empty:
                return 0.0
            ids_jefes = set(personal_cat_df[personal_cat_df["personal_id"].str.startswith("JEF_")]["personal_id"])
            f = f[f["personal_id"].isin(ids_jefes)]
            return _num_col(f, "costo_calculado").sum()

        def _personas_turno(jornadas_df, personal_cat_df, fecha, turno_id):
            if jornadas_df.empty:
                return []
            f = jornadas_df[
                (jornadas_df["fecha"].astype(str) == str(fecha)) &
                (jornadas_df["turno_id"].astype(str) == str(turno_id))
            ]
            if f.empty:
                return []
            if not personal_cat_df.empty:
                f = f.merge(personal_cat_df[["personal_id","nombre"]], on="personal_id", how="left")
                f["nombre"] = f["nombre"].fillna(f["personal_id"])
                return list(f["nombre"])
            return list(f["personal_id"])

        def _kg_turno(prod_df, fecha, turno_id):
            if prod_df.empty:
                return 0.0, {}
            f = prod_df[
                (prod_df["fecha"].astype(str) == str(fecha)) &
                (prod_df["turno"].astype(str) == str(turno_id))
            ]
            if f.empty:
                return 0.0, {}
            f["kg_real"] = pd.to_numeric(f["kg_real"], errors="coerce").fillna(0)
            por_tipo = f.groupby("tipo_producto")["kg_real"].sum().to_dict()
            return f["kg_real"].sum(), por_tipo

        def _costo_mp_turno(consumo_df, prod_df, fecha, turno_id):
            if consumo_df.empty or prod_df.empty:
                return 0.0
            lotes_turno = prod_df[
                (prod_df["fecha"].astype(str) == str(fecha)) &
                (prod_df["turno"].astype(str) == str(turno_id))
            ]["lote_semielaborado_id"].tolist()
            if not lotes_turno:
                return 0.0
            c = consumo_df[consumo_df["lote_semielaborado_id"].isin(lotes_turno)]
            return _num_col(c, "costo_total_aplicado").sum()

        def _costo_envases_turno(past_df, prod_df, fecha, turno_id):
            if past_df.empty or prod_df.empty:
                return 0.0
            lotes_turno = prod_df[
                (prod_df["fecha"].astype(str) == str(fecha)) &
                (prod_df["turno"].astype(str) == str(turno_id))
            ]["lote_semielaborado_id"].tolist()
            if not lotes_turno:
                return 0.0
            p = past_df[past_df["lote_semielaborado_id"].isin(lotes_turno)]
            for col in ["costo_envases","costo_tapas","costo_etiquetas","costo_cartones","costo_liners"]:
                if col not in p.columns:
                    p[col] = 0
            return sum(_num_col(p, col).sum() for col in ["costo_envases","costo_tapas","costo_etiquetas","costo_cartones","costo_liners"])

        def _costo_insumos_dia(limp_df, fecha):
            if limp_df.empty:
                return 0.0
            f = limp_df[limp_df["fecha"].astype(str) == str(fecha)]
            return _num_col(f, "costo_insumos").sum()

        def _agua_turno(prod_df, limp_df, fecha, turno_id):
            agua_prod = 0.0
            if not prod_df.empty:
                f = prod_df[
                    (prod_df["fecha"].astype(str) == str(fecha)) &
                    (prod_df["turno"].astype(str) == str(turno_id))
                ]
                agua_prod = _num_col(f, "agua_litros").sum()
            agua_limp = 0.0
            if not limp_df.empty:
                f2 = limp_df[limp_df["fecha"].astype(str) == str(fecha)]
                if "turno" in limp_df.columns:
                    f2 = f2[f2["turno"].astype(str) == str(turno_id)]
                agua_limp = _num_col(f2, "agua_litros").sum()
            return agua_prod + agua_limp

        # ── TAB: POR TURNO ───────────────────────────────────────────────
        with sub_turno:
            col_ft1, col_ft2 = st.columns(2)
            fecha_t = col_ft1.date_input("Fecha", value=hoy, key="ct_fecha")
            opciones_turnos = ["Todos"] + (list(turno_cat["turno_id"]) if not turno_cat.empty else [])
            turno_sel = col_ft2.selectbox(
                "Turno", opciones_turnos,
                format_func=lambda x: x if x == "Todos" else (
                    turno_cat.set_index("turno_id").loc[x, "nombre"]
                    if not turno_cat.empty and x in list(turno_cat["turno_id"]) else x
                ),
                key="ct_turno",
            )

            turnos_a_mostrar = list(turno_cat["turno_id"]) if (turno_sel == "Todos" and not turno_cat.empty) else [turno_sel]
            costo_overhead = _costo_mo_overhead_dia(jornadas, personal_cat, fecha_t)
            n_turnos = len(turnos_a_mostrar) if turnos_a_mostrar else 1

            for tid in turnos_a_mostrar:
                tnombre = tid
                if not turno_cat.empty and tid in list(turno_cat["turno_id"]):
                    tnombre = turno_cat.set_index("turno_id").loc[tid, "nombre"]

                kg_total, kg_por_tipo = _kg_turno(produccion, fecha_t, tid)
                costo_mp  = _costo_mp_turno(consumo_full, produccion, fecha_t, tid)
                costo_env = _costo_envases_turno(past_full, produccion, fecha_t, tid)
                costo_mo  = _costo_mo_turno(jornadas, personal_cat, fecha_t, tid)
                costo_ins = _costo_insumos_dia(limpieza_f, fecha_t) / n_turnos
                overhead_turno = costo_overhead / n_turnos
                agua      = _agua_turno(prod_f, limpieza_f, fecha_t, tid)
                personas  = _personas_turno(jornadas, personal_cat, fecha_t, tid)

                costo_total_turno = costo_mp + costo_env + costo_mo + costo_ins + overhead_turno
                costo_kg_turno    = costo_total_turno / kg_total if kg_total > 0 else 0

                with st.container(border=True):
                    st.markdown(f"#### 🔄 {tnombre}")
                    c1,c2,c3,c4 = st.columns(4)
                    c1.metric("Kg producidos", f"{kg_total:,.1f} kg")
                    c2.metric("Costo total", f"${costo_total_turno:,.2f}")
                    c3.metric("Costo/kg", f"${costo_kg_turno:,.3f}")
                    c4.metric("Agua usada", f"{agua:,.0f} L")

                    if kg_por_tipo:
                        st.caption("Kg por producto: " + " · ".join(f"**{t}**: {v:.1f} kg" for t,v in kg_por_tipo.items()))

                    col_d, col_g = st.columns([2,1])
                    with col_d:
                        st.markdown("**Desglose de costo**")
                        items_costo = [
                            ("🥚 Materia prima (huevo)", costo_mp),
                            ("📦 Envases + etiquetas", costo_env),
                            ("👷 Mano de obra (operarios)", costo_mo),
                            ("🧪 Insumos/limpieza (prorrat.)", costo_ins),
                            ("👔 Jefes overhead (prorrat.)", overhead_turno),
                        ]
                        for label, val in items_costo:
                            pct = val/costo_total_turno*100 if costo_total_turno > 0 else 0
                            st.caption(f"{label}: **${val:,.2f}** ({pct:.1f}%)")

                    with col_g:
                        labels = [i[0].split("(")[0].strip() for i in items_costo if i[1] > 0]
                        vals   = [i[1] for i in items_costo if i[1] > 0]
                        if vals:
                            fig = go.Figure(go.Pie(labels=labels, values=vals, hole=0.5,
                                                   marker_colors=[VERDE,NARANJA,DORADO,TEAL,MORADO],
                                                   textinfo="percent"))
                            fig.update_layout(showlegend=False, height=200,
                                              margin=dict(t=10,b=10,l=10,r=10),
                                              paper_bgcolor="rgba(0,0,0,0)")
                            st.plotly_chart(fig, use_container_width=True)

                    if personas:
                        st.caption("👷 Personal: " + ", ".join(personas))
                    if costo_overhead > 0:
                        st.caption(f"👔 Jefes de planta/calidad (costo diario ${ costo_overhead:,.2f} dividido entre {n_turnos} turno(s))")

        # ── TAB: POR DÍA ────────────────────────────────────────────────
        with sub_dia:
            fecha_d = st.date_input("Fecha", value=hoy, key="cd_fecha")
            prod_dia   = produccion[produccion["fecha"].astype(str) == str(fecha_d)].copy() if not produccion.empty else pd.DataFrame()
            past_dia   = past_full[past_full["fecha"].astype(str) == str(fecha_d)].copy() if not past_full.empty else pd.DataFrame()
            jorn_dia   = jornadas[jornadas["fecha"].astype(str) == str(fecha_d)].copy() if not jornadas.empty else pd.DataFrame()
            limp_dia   = limp_full[limp_full["fecha"].astype(str) == str(fecha_d)].copy() if not limp_full.empty else pd.DataFrame()
            cons_dia   = consumo_full.copy()
            if not cons_dia.empty and not prod_dia.empty:
                lotes_dia = prod_dia["lote_semielaborado_id"].tolist()
                cons_dia  = cons_dia[cons_dia["lote_semielaborado_id"].isin(lotes_dia)]
            kg_dia     = _num_col(prod_dia, "kg_real").sum()
            costo_mp_d = _num_col(cons_dia, "costo_total_aplicado").sum()
            costo_env_d= sum(_num_col(past_dia, c).sum() for c in ["costo_envases","costo_tapas","costo_etiquetas","costo_cartones","costo_liners"] if c in past_dia.columns)
            ids_jefes  = set(personal_cat[personal_cat["personal_id"].str.startswith("JEF_")]["personal_id"]) if not personal_cat.empty else set()
            costo_mo_op= _num_col(jorn_dia[~jorn_dia["personal_id"].isin(ids_jefes)] if not jorn_dia.empty else pd.DataFrame(), "costo_calculado").sum()
            costo_jef  = _num_col(jorn_dia[jorn_dia["personal_id"].isin(ids_jefes)] if not jorn_dia.empty else pd.DataFrame(), "costo_calculado").sum()
            costo_ins_d= _num_col(limp_dia, "costo_insumos").sum()
            agua_dia   = _num_col(prod_dia, "agua_litros").sum() + _num_col(limp_dia, "agua_litros").sum()
            personas_dia= len(jorn_dia["personal_id"].unique()) if not jorn_dia.empty else 0
            horas_dia  = _num_col(jorn_dia, "horas").sum()

            costo_total_dia = costo_mp_d + costo_env_d + costo_mo_op + costo_jef + costo_ins_d
            costo_kg_dia    = costo_total_dia / kg_dia if kg_dia > 0 else 0
            hh_por_kg_dia   = horas_dia / kg_dia if kg_dia > 0 else 0

            st.markdown(f"### 📅 Resumen del {fecha_d.strftime('%d/%m/%Y')}")
            r1,r2,r3,r4,r5 = st.columns(5)
            r1.metric("Kg producidos", f"{kg_dia:,.1f} kg")
            r2.metric("Costo total día", f"${costo_total_dia:,.2f}")
            r3.metric("Costo/kg", f"${costo_kg_dia:,.3f}")
            r4.metric("HH/kg", f"{hh_por_kg_dia:,.3f}")
            r5.metric("Agua total", f"{agua_dia:,.0f} L")

            st.write("")
            col_tab, col_pie = st.columns([2,1])
            with col_tab:
                desglose = pd.DataFrame([
                    {"Componente": "🥚 Materia prima", "Costo": costo_mp_d},
                    {"Componente": "📦 Envases/etiquetas", "Costo": costo_env_d},
                    {"Componente": "👷 MO operarios", "Costo": costo_mo_op},
                    {"Componente": "👔 Jefes planta/calidad", "Costo": costo_jef},
                    {"Componente": "🧪 Insumos limpieza", "Costo": costo_ins_d},
                ])
                desglose["% del total"] = desglose["Costo"].apply(
                    lambda v: f"{v/costo_total_dia*100:.1f}%" if costo_total_dia > 0 else "—"
                )
                desglose["Costo"] = desglose["Costo"].apply(lambda v: f"${v:,.2f}")
                st.dataframe(desglose, use_container_width=True, hide_index=True)
                st.caption(f"Personal: {personas_dia} personas · {horas_dia:,.1f} horas totales · {agua_dia:,.0f} L agua")

            with col_pie:
                vals_pie = [costo_mp_d, costo_env_d, costo_mo_op, costo_jef, costo_ins_d]
                labs_pie = ["MP","Envases","MO Op.","Jefes","Insumos"]
                if sum(vals_pie) > 0:
                    fig2 = go.Figure(go.Pie(
                        labels=labs_pie, values=vals_pie, hole=0.5,
                        marker_colors=[VERDE,NARANJA,DORADO,TEAL,MORADO],
                        textinfo="percent+label",
                    ))
                    fig2.update_layout(showlegend=False, height=260,
                                       margin=dict(t=10,b=10,l=10,r=10),
                                       paper_bgcolor="rgba(0,0,0,0)")
                    st.plotly_chart(fig2, use_container_width=True)

            # Comparación entre turnos del día
            if not turno_cat.empty and not prod_dia.empty:
                st.write("")
                st.markdown("##### 🔄 Comparación entre turnos del día")
                filas_comp = []
                for tid in turno_cat["turno_id"]:
                    tnombre = turno_cat.set_index("turno_id").loc[tid, "nombre"]
                    kg_t, _ = _kg_turno(prod_dia, fecha_d, tid)
                    if kg_t == 0:
                        continue
                    cmo_t = _costo_mo_turno(jorn_dia, personal_cat, fecha_d, tid)
                    cmp_t = _costo_mp_turno(cons_dia, prod_dia, fecha_d, tid)
                    cenv_t= _costo_envases_turno(past_dia, prod_dia, fecha_d, tid)
                    pers_t= len([p for p in _personas_turno(jorn_dia, personal_cat, fecha_d, tid)])
                    horas_t = _num_col(jorn_dia[(jorn_dia["turno_id"].astype(str)==str(tid)) if "turno_id" in jorn_dia.columns else jorn_dia], "horas").sum()
                    ctotal_t= cmo_t + cmp_t + cenv_t
                    filas_comp.append({
                        "Turno": tnombre, "Kg": round(kg_t,1),
                        "Personas": pers_t, "Horas": round(horas_t,1),
                        "HH/kg": round(horas_t/kg_t,3) if kg_t > 0 else 0,
                        "Costo MO": round(cmo_t,2), "Costo MP": round(cmp_t,2),
                        "Costo total": round(ctotal_t,2),
                        "Costo/kg": round(ctotal_t/kg_t,3) if kg_t > 0 else 0,
                    })
                if filas_comp:
                    st.dataframe(pd.DataFrame(filas_comp), use_container_width=True, hide_index=True)

        # ── TAB: TENDENCIA DEL PERÍODO ───────────────────────────────────
        with sub_tendencia:
            st.caption("Evolución diaria del costo/kg y eficiencia para detectar días anómalos.")

            # Construir serie diaria para el período seleccionado
            if produccion.empty:
                st.info("Sin datos de producción en el período.")
            else:
                dias_unicos = sorted(
                    pd.to_datetime(prod_f["fecha"], errors="coerce").dt.date.dropna().unique()
                )
                if not dias_unicos:
                    st.info("Sin datos en el período seleccionado.")
                else:
                    filas_tend = []
                    for dia in dias_unicos:
                        prod_d   = produccion[produccion["fecha"].astype(str) == str(dia)]
                        jorn_d   = jornadas[jornadas["fecha"].astype(str) == str(dia)] if not jornadas.empty else pd.DataFrame()
                        limp_d   = limp_full[limp_full["fecha"].astype(str) == str(dia)] if not limp_full.empty else pd.DataFrame()
                        cons_d   = consumo_full[consumo_full["lote_semielaborado_id"].isin(prod_d["lote_semielaborado_id"])] if not consumo_full.empty and not prod_d.empty else pd.DataFrame()
                        past_d   = past_full[past_full["fecha"].astype(str) == str(dia)] if not past_full.empty else pd.DataFrame()

                        kg_d     = _num_col(prod_d, "kg_real").sum()
                        cmp_d    = _num_col(cons_d, "costo_total_aplicado").sum()
                        cenv_d   = sum(_num_col(past_d, c).sum() for c in ["costo_envases","costo_tapas","costo_etiquetas","costo_cartones","costo_liners"] if c in past_d.columns)
                        cmo_d    = _num_col(jorn_d, "costo_calculado").sum()
                        cins_d   = _num_col(limp_d, "costo_insumos").sum()
                        agua_d   = _num_col(prod_d, "agua_litros").sum() + _num_col(limp_d, "agua_litros").sum()
                        horas_d  = _num_col(jorn_d, "horas").sum()
                        pers_d   = len(jorn_d["personal_id"].unique()) if not jorn_d.empty else 0
                        ctot_d   = cmp_d + cenv_d + cmo_d + cins_d
                        filas_tend.append({
                            "dia": dia,
                            "kg": kg_d,
                            "costo_kg": ctot_d/kg_d if kg_d > 0 else 0,
                            "hh_kg": horas_d/kg_d if kg_d > 0 else 0,
                            "agua_kg": agua_d/kg_d if kg_d > 0 else 0,
                            "personas": pers_d,
                            "costo_mp": cmp_d,
                            "costo_env": cenv_d,
                            "costo_mo": cmo_d,
                            "costo_ins": cins_d,
                        })

                    tend_df = pd.DataFrame(filas_tend)

                    # Gráficos de tendencia
                    col_t1, col_t2 = st.columns(2)
                    with col_t1:
                        with st.container(border=True):
                            st.markdown("**💲 Costo/kg por día**")
                            fig_ck = go.Figure(go.Scatter(
                                x=tend_df["dia"], y=tend_df["costo_kg"],
                                mode="lines+markers", line_color=VERDE,
                                fill="tozeroy", fillcolor="rgba(0,0,0,0.05)",
                                hovertemplate="Día %{x}: $%{y:.3f}/kg<extra></extra>",
                            ))
                            # línea promedio
                            avg_ckg = tend_df["costo_kg"].mean()
                            fig_ck.add_hline(y=avg_ckg, line_dash="dash",
                                             line_color=NARANJA, annotation_text=f"Prom ${avg_ckg:.3f}")
                            fig_ck.update_layout(height=220, margin=dict(t=10,b=20,l=10,r=10),
                                                 paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
                            st.plotly_chart(fig_ck, use_container_width=True)

                    with col_t2:
                        with st.container(border=True):
                            st.markdown("**⏱️ Horas-hombre / kg por día**")
                            fig_hh = go.Figure(go.Scatter(
                                x=tend_df["dia"], y=tend_df["hh_kg"],
                                mode="lines+markers", line_color=DORADO,
                                fill="tozeroy", fillcolor="rgba(0,0,0,0.05)",
                                hovertemplate="Día %{x}: %{y:.3f} HH/kg<extra></extra>",
                            ))
                            avg_hh = tend_df["hh_kg"].mean()
                            fig_hh.add_hline(y=avg_hh, line_dash="dash",
                                             line_color=NARANJA, annotation_text=f"Prom {avg_hh:.3f}")
                            fig_hh.update_layout(height=220, margin=dict(t=10,b=20,l=10,r=10),
                                                 paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
                            st.plotly_chart(fig_hh, use_container_width=True)

                    col_t3, col_t4 = st.columns(2)
                    with col_t3:
                        with st.container(border=True):
                            st.markdown("**💧 Agua / kg por día**")
                            fig_ag = go.Figure(go.Scatter(
                                x=tend_df["dia"], y=tend_df["agua_kg"],
                                mode="lines+markers", line_color=AZUL,
                                fill="tozeroy", fillcolor="rgba(0,0,0,0.05)",
                                hovertemplate="Día %{x}: %{y:.2f} L/kg<extra></extra>",
                            ))
                            avg_ag = tend_df["agua_kg"].mean()
                            fig_ag.add_hline(y=avg_ag, line_dash="dash",
                                             line_color=NARANJA, annotation_text=f"Prom {avg_ag:.2f} L")
                            fig_ag.update_layout(height=220, margin=dict(t=10,b=20,l=10,r=10),
                                                 paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
                            st.plotly_chart(fig_ag, use_container_width=True)

                    with col_t4:
                        with st.container(border=True):
                            st.markdown("**👷 Personas por día**")
                            fig_pe = go.Figure(go.Bar(
                                x=tend_df["dia"], y=tend_df["personas"],
                                marker_color=MORADO,
                                hovertemplate="Día %{x}: %{y} personas<extra></extra>",
                            ))
                            avg_pe = tend_df["personas"].mean()
                            fig_pe.add_hline(y=avg_pe, line_dash="dash",
                                             line_color=NARANJA, annotation_text=f"Prom {avg_pe:.1f}")
                            fig_pe.update_layout(height=220, margin=dict(t=10,b=20,l=10,r=10),
                                                 paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)")
                            st.plotly_chart(fig_pe, use_container_width=True)

                    # Composición del costo apilada por día
                    st.write("")
                    with st.container(border=True):
                        st.markdown("**📊 Composición del costo por día (barras apiladas)**")
                        fig_ap = go.Figure()
                        fig_ap.add_trace(go.Bar(name="MP", x=tend_df["dia"], y=tend_df["costo_mp"], marker_color=VERDE))
                        fig_ap.add_trace(go.Bar(name="Envases", x=tend_df["dia"], y=tend_df["costo_env"], marker_color=NARANJA))
                        fig_ap.add_trace(go.Bar(name="MO", x=tend_df["dia"], y=tend_df["costo_mo"], marker_color=DORADO))
                        fig_ap.add_trace(go.Bar(name="Insumos", x=tend_df["dia"], y=tend_df["costo_ins"], marker_color=TEAL))
                        fig_ap.update_layout(
                            barmode="stack", height=280,
                            margin=dict(t=10,b=40,l=10,r=10),
                            paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
                            legend=dict(orientation="h", yanchor="bottom", y=1.02),
                        )
                        st.plotly_chart(fig_ap, use_container_width=True)

                    # Tabla resumen del período
                    st.write("")
                    with st.expander("📋 Ver tabla resumen del período"):
                        tend_mostrar = tend_df.copy()
                        tend_mostrar["dia"] = tend_mostrar["dia"].astype(str)
                        tend_mostrar = tend_mostrar.rename(columns={
                            "dia":"Fecha","kg":"Kg","costo_kg":"$/kg",
                            "hh_kg":"HH/kg","agua_kg":"L/kg","personas":"Personas",
                        })
                        st.dataframe(tend_mostrar[["Fecha","Kg","$/kg","HH/kg","L/kg","Personas"]],
                                     use_container_width=True, hide_index=True)
                        # Fila de promedios
                        st.caption(
                            f"**Promedios del período** — "
                            f"Kg/día: {tend_df['kg'].mean():,.1f} · "
                            f"Costo/kg: ${tend_df['costo_kg'].mean():,.3f} · "
                            f"HH/kg: {tend_df['hh_kg'].mean():,.3f} · "
                            f"L/kg: {tend_df['agua_kg'].mean():,.2f} · "
                            f"Personas/día: {tend_df['personas'].mean():,.1f}"
                        )
