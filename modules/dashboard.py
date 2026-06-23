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
    personal_detalle= db.get_df("produccion_personal")
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
    costo_total_periodo = prod_costos["costo_total"].sum() if not prod_costos.empty else 0.0
    kg_total_periodo    = prod_costos["kg_real"].sum() if not prod_costos.empty else 0.0
    costo_por_kg_general= costo_total_periodo / kg_total_periodo if kg_total_periodo > 0 else 0
    agua_produccion     = prod_costos["agua_litros"].sum() if not prod_costos.empty else 0.0
    agua_limpieza       = _num(limpieza_f, "agua_litros").sum()

    # horas hombre del período
    hh_df = pd.DataFrame()
    if not personal_detalle.empty and not produccion.empty:
        hh_df = personal_detalle.merge(produccion[["lote_semielaborado_id","fecha"]], on="lote_semielaborado_id", how="left")
    superv = db.get_df("supervision_diaria")
    if not superv.empty:
        hh_df = pd.concat([hh_df, superv[["personal_id","fecha","horas","horas_nocturnas","costo_calculado"]]], ignore_index=True)
    hh_f = _filtrar_por_fecha(hh_df, desde, hasta)
    horas_totales_periodo = _num(hh_f, "horas").sum()
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
        "📦 Insumos y envases", "♻️ Residuos y mermas", "💧 Agua",
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
                    costo_huevo_sum   = prod_costos["costo_huevo"].sum()
                    costo_insumos_sum = prod_costos["costo_insumos"].sum()
                    costo_mo_sum      = prod_costos["costo_mano_obra"].sum()
                    pct = lambda v: (v/costo_total_periodo*100) if costo_total_periodo > 0 else 0
                    c1, c2, c3 = st.columns(3)
                    _kpi_card(c1, "🥚", "Huevo", f"${costo_huevo_sum:,.2f}", ayuda=f"{pct(costo_huevo_sum):.0f}% del costo total")
                    _kpi_card(c2, "🧴", "Insumos", f"${costo_insumos_sum:,.2f}", ayuda=f"{pct(costo_insumos_sum):.0f}% del costo total")
                    _kpi_card(c3, "👷", "Mano de obra", f"${costo_mo_sum:,.2f}", ayuda=f"{pct(costo_mo_sum):.0f}% del costo total")
                    _grafico_dona(["Huevo","Insumos","Mano de obra"],
                                  [costo_huevo_sum, costo_insumos_sum, costo_mo_sum], "Composición del costo")

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
        with st.container(border=True):
            st.markdown("##### 👷 Horas de trabajo por persona")
            st.caption("Normales ≤8h día normal · Extras >8h · Dobles = feriado sin compensar · Compensadas = feriado con descanso · Nocturnas = 19:00-05:00")

            hh_f["horas"] = pd.to_numeric(hh_f.get("horas"), errors="coerce").fillna(0)
            hh_f["horas_nocturnas"] = pd.to_numeric(hh_f.get("horas_nocturnas"), errors="coerce").fillna(0)
            hh_f["costo_calculado"] = pd.to_numeric(hh_f.get("costo_calculado"), errors="coerce").fillna(0)

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
                activos = personal_cat[personal_cat.get("activo","TRUE").astype(str).str.upper() != "FALSE"].copy()
                reporte = activos.set_index("personal_id").join(resumen_por_id, how="left")
                for col in ["horas_normales","horas_extras","horas_dobles","horas_compensadas","horas_totales"]:
                    reporte[col] = reporte[col].fillna(0)
                reporte["costo"] = reporte.index.map(costo_por_persona).fillna(0)
                reporte["horas_nocturnas"] = reporte.index.map(nocturnas_por_persona).fillna(0)
                reporte["hh_por_kg"] = reporte["horas_totales"].apply(lambda h: h/kg_total_periodo if kg_total_periodo > 0 else 0)
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

                st.markdown("**Desglose de horas por persona**")
                _grafico_barras_apiladas(reporte.set_index("nombre"),
                                         ["horas_normales","horas_extras","horas_dobles","horas_compensadas"])
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
