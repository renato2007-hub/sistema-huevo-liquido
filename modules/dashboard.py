"""
Dashboard: panel de control con seleccion de periodo (fecha o rango) que
resume la operacion completa. Estructura: un resumen ejecutivo siempre
visible arriba (lo mas importante de un vistazo), y el detalle agrupado en
pestanas por tema para no amontonar todo en una sola pantalla.

No guarda nada -- solo lee y resume las mismas tablas que alimentan los
demas modulos.
"""
import datetime
import streamlit as st
import pandas as pd
from utils.horas_trabajo import clasificar_horas_por_dia, feriados_como_set, compensaciones_como_set
from utils.pdf_horas_personal import generar_pdf_horas_personal
from modules.bodega_envases_insumos import _saldo_actual


def _filtrar_por_fecha(df, desde, hasta, columna="fecha"):
    if df.empty or columna not in df.columns:
        return df
    fechas = pd.to_datetime(df[columna], errors="coerce")
    return df[(fechas.dt.date >= desde) & (fechas.dt.date <= hasta)]


def _num(df, col):
    if df.empty or col not in df.columns:
        return pd.Series(dtype=float)
    return pd.to_numeric(df[col], errors="coerce").fillna(0)


def _tarjeta_metrica(col, etiqueta, valor, ayuda=None, delta=None):
    with col.container(border=True):
        st.metric(etiqueta, valor, delta=delta, help=ayuda)


def render(db, username, rol):
    st.title("📊 Dashboard")

    with st.container(border=True):
        col_periodo, col_fechas = st.columns([1, 2])
        periodo = col_periodo.selectbox(
            "Período", ["Hoy", "Últimos 7 días", "Este mes", "Personalizado"],
        )
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

    # ---- cargar todas las tablas necesarias una sola vez ----
    produccion = db.get_df("produccion_semielaborados")
    consumo_mp = db.get_df("consumo_mp_produccion")
    personal_detalle = db.get_df("produccion_personal")
    pasteurizacion = db.get_df("pasteurizacion_envasado")
    movimientos = db.get_df("movimientos_envases_insumos")
    limpieza = db.get_df("limpieza_desinfeccion")
    mermas_mp = db.get_df("mermas_mp")
    presentaciones = db.get_df("presentaciones")
    insumos = db.get_df("insumos")
    personal_cat = db.get_df("personal")
    recepciones_mp = db.get_df("recepciones_mp")
    categorias = db.get_df("categorias_huevo")

    prod_f = _filtrar_por_fecha(produccion, desde, hasta)
    consumo_f = _filtrar_por_fecha(consumo_mp, desde, hasta)
    past_f = _filtrar_por_fecha(pasteurizacion, desde, hasta)
    mov_f = _filtrar_por_fecha(movimientos, desde, hasta)
    limpieza_f = _filtrar_por_fecha(limpieza, desde, hasta)
    mermas_mp_f = _filtrar_por_fecha(mermas_mp, desde, hasta)

    # calculos base reutilizados en varias tarjetas
    cubetas_total = _num(consumo_f, "cubetas_usadas").sum()
    costo_huevo_total = _num(consumo_f, "costo_total_aplicado").sum()

    prod_costos = prod_f.copy()
    if not prod_costos.empty:
        for col in ["costo_total", "kg_real", "costo_huevo", "costo_insumos", "costo_mano_obra", "agua_litros", "cascara_real_kg"]:
            prod_costos[col] = pd.to_numeric(prod_costos[col], errors="coerce").fillna(0)
    costo_total_periodo = prod_costos["costo_total"].sum() if not prod_costos.empty else 0.0
    kg_total_periodo = prod_costos["kg_real"].sum() if not prod_costos.empty else 0.0
    costo_por_kg_general = costo_total_periodo / kg_total_periodo if kg_total_periodo > 0 else 0
    agua_produccion = prod_costos["agua_litros"].sum() if not prod_costos.empty else 0.0
    agua_limpieza = _num(limpieza_f, "agua_litros").sum()

    # ======================== RESUMEN EJECUTIVO ========================
    st.markdown("### 🎯 Resumen del período")
    r1, r2, r3, r4 = st.columns(4)
    _tarjeta_metrica(r1, "Kg producidos", f"{kg_total_periodo:,.1f}", "Suma de kg reales de todos los lotes (huevo entero + clara + yema)")
    _tarjeta_metrica(r2, "Costo promedio /kg", f"{costo_por_kg_general:,.3f}", "Costo total de producción ÷ kg reales producidos")
    _tarjeta_metrica(r3, "Costo total producción", f"{costo_total_periodo:,.2f}")
    _tarjeta_metrica(r4, "Agua total (L)", f"{agua_produccion + agua_limpieza:,.0f}", "Producción + limpieza y desinfección")

    st.write("")

    tabs = st.tabs([
        "📦 Inventarios disponibles", "🏭 Producción y costos", "👷 Personal",
        "🧴 Insumos y envases", "♻️ Residuos y mermas", "💧 Agua",
    ])

    # ======================== TAB: INVENTARIOS DISPONIBLES (estado actual) ========================
    with tabs[0]:
        st.caption(
            "⚠️ Esta pestaña muestra el inventario **disponible ahora mismo** — "
            "no cambia según el período seleccionado arriba, porque es una foto "
            "del estado actual, no un acumulado del rango de fechas."
        )

        with st.container(border=True):
            st.markdown("##### 🥚 Huevo en bodega de materia prima")
            if recepciones_mp.empty:
                st.info("No hay recepciones registradas.")
            else:
                inv_huevo = recepciones_mp.copy()
                inv_huevo["cubetas_saldo"] = pd.to_numeric(inv_huevo["cubetas_saldo"], errors="coerce").fillna(0)
                inv_huevo["costo_cubeta"] = pd.to_numeric(inv_huevo["costo_cubeta"], errors="coerce").fillna(0)
                inv_huevo = inv_huevo[inv_huevo["cubetas_saldo"] > 0].copy()
                if inv_huevo.empty:
                    st.info("No hay saldo disponible en bodega de materia prima.")
                else:
                    inv_huevo["valor"] = inv_huevo["cubetas_saldo"] * inv_huevo["costo_cubeta"]
                    if not categorias.empty:
                        inv_huevo = inv_huevo.merge(
                            categorias[["categoria_id", "nombre"]].rename(columns={"nombre": "categoria_nombre"}),
                            on="categoria_id", how="left",
                        )
                        inv_huevo["categoria_nombre"] = inv_huevo["categoria_nombre"].fillna(inv_huevo["categoria_id"])
                    else:
                        inv_huevo["categoria_nombre"] = inv_huevo["categoria_id"]

                    c1, c2, c3 = st.columns(3)
                    c1.metric("Cubetas disponibles", f"{inv_huevo['cubetas_saldo'].sum():,.0f}")
                    c2.metric("Huevos disponibles", f"{inv_huevo['cubetas_saldo'].sum() * 30:,.0f}")
                    c3.metric("Valor en bodega", f"{inv_huevo['valor'].sum():,.2f}")

                    resumen_categoria = inv_huevo.groupby("categoria_nombre").agg(
                        cubetas=("cubetas_saldo", "sum"), valor=("valor", "sum"),
                    ).reset_index()
                    st.dataframe(resumen_categoria, use_container_width=True, hide_index=True)
                    with st.expander("Ver detalle por lote"):
                        st.dataframe(
                            inv_huevo[["recepcion_id", "categoria_nombre", "cubetas_saldo", "costo_cubeta", "valor", "fecha_vencimiento"]],
                            use_container_width=True, hide_index=True,
                        )

        st.write("")

        col_env, col_quim = st.columns(2)
        with col_env:
            with st.container(border=True):
                st.markdown("##### 📦 Envases disponibles")
                if presentaciones.empty:
                    st.info("No hay presentaciones configuradas.")
                else:
                    filas_env = []
                    for _, row in presentaciones.iterrows():
                        filas_env.append({
                            "Presentación": row["nombre"],
                            "Saldo": _saldo_actual(movimientos, "envase", row["presentacion_id"]),
                        })
                    df_env = pd.DataFrame(filas_env)
                    negativos_env = df_env[df_env["Saldo"] < 0]
                    if not negativos_env.empty:
                        st.warning(f"⚠️ {len(negativos_env)} presentación(es) con saldo negativo.")
                    st.dataframe(df_env, use_container_width=True, hide_index=True)

        with col_quim:
            with st.container(border=True):
                st.markdown("##### 🧴 Insumos químicos disponibles")
                if insumos.empty:
                    st.info("No hay insumos configurados.")
                else:
                    filas_quim = []
                    for _, row in insumos.iterrows():
                        filas_quim.append({
                            "Insumo": row["nombre"],
                            "Unidad": row.get("unidad", ""),
                            "Saldo": _saldo_actual(movimientos, "insumo", row["insumo_id"]),
                        })
                    df_quim = pd.DataFrame(filas_quim)
                    negativos_quim = df_quim[df_quim["Saldo"] < 0]
                    if not negativos_quim.empty:
                        st.warning(f"⚠️ {len(negativos_quim)} insumo(s) con saldo negativo.")
                    st.dataframe(df_quim, use_container_width=True, hide_index=True)

    # ======================== TAB: PRODUCCION Y COSTOS ========================
    with tabs[1]:
        with st.container(border=True):
            st.markdown("##### 🥚 Huevo procesado (Bodega MP → Producción)")
            c1, c2, c3 = st.columns(3)
            c1.metric("Cubetas usadas", f"{cubetas_total:,.0f}")
            c2.metric("Huevos procesados", f"{cubetas_total * 30:,.0f}")
            c3.metric("Costo de huevo consumido", f"{costo_huevo_total:,.2f}")

        st.write("")

        with st.container(border=True):
            st.markdown("##### ⚖️ Balance de masa promedio del período")
            st.caption("(líquido + cáscara real) ÷ peso bruto teórico del huevo procesado. 100% = sin pérdidas sin explicar; nunca debería superar 100%.")
            if prod_costos.empty or "balance_masa_pct" not in prod_costos.columns:
                st.info("No hay datos de balance de masa en este período.")
            else:
                balance_vals = pd.to_numeric(prod_costos["balance_masa_pct"], errors="coerce").dropna()
                if balance_vals.empty:
                    st.info("No hay datos de balance de masa en este período.")
                else:
                    fuera_de_rango = balance_vals[(balance_vals > 100.5) | (balance_vals < 85)]
                    c1, c2 = st.columns(2)
                    c1.metric("Balance promedio", f"{balance_vals.mean():.1f}%")
                    c2.metric("Lotes fuera de rango (85-100%)", f"{len(fuera_de_rango)} de {len(balance_vals)}")
                    if not fuera_de_rango.empty:
                        st.warning("Hay lotes con balance de masa fuera de rango — revísalos en Producción → 'Teórico vs. real'.")

        st.write("")

        with st.container(border=True):
            st.markdown("##### 💰 Costo por kg, según tipo de producto")
            if prod_costos.empty:
                st.info("No hay producción de semielaborados en este período.")
            else:
                por_tipo = prod_costos.groupby("tipo_producto").agg(
                    costo_total=("costo_total", "sum"), kg_real=("kg_real", "sum"),
                ).reset_index()
                por_tipo["costo_por_kg"] = por_tipo.apply(
                    lambda r: r["costo_total"] / r["kg_real"] if r["kg_real"] > 0 else 0, axis=1,
                )
                st.dataframe(
                    por_tipo.rename(columns={
                        "tipo_producto": "Producto", "costo_total": "Costo total",
                        "kg_real": "Kg producidos", "costo_por_kg": "Costo/kg",
                    }),
                    use_container_width=True, hide_index=True,
                )

                st.caption("De qué se compone el costo total del período:")
                costo_huevo_sum = prod_costos["costo_huevo"].sum()
                costo_insumos_sum = prod_costos["costo_insumos"].sum()
                costo_mo_sum = prod_costos["costo_mano_obra"].sum()
                pct = lambda v: (v / costo_total_periodo * 100) if costo_total_periodo > 0 else 0
                c1, c2, c3 = st.columns(3)
                c1.metric("🥚 Huevo", f"{costo_huevo_sum:,.2f}", f"{pct(costo_huevo_sum):.0f}%")
                c2.metric("🧴 Insumos", f"{costo_insumos_sum:,.2f}", f"{pct(costo_insumos_sum):.0f}%")
                c3.metric("👷 Mano de obra directa", f"{costo_mo_sum:,.2f}", f"{pct(costo_mo_sum):.0f}%")

                superv_periodo = _filtrar_por_fecha(db.get_df("supervision_diaria"), desde, hasta)
                costo_superv = _num(superv_periodo, "costo_calculado").sum()
                st.metric(
                    "👔 Costo de supervisión/calidad (overhead, NO incluido arriba)",
                    f"{costo_superv:,.2f}",
                    help="Costo del Jefe de planta / Jefe de calidad del período — se muestra aparte a propósito, no se reparte en el costo/kg de los lotes.",
                )

        st.write("")

        col_past, col_pend = st.columns(2)
        with col_past:
            with st.container(border=True):
                st.markdown("##### 🧪 Pasteurizado por tipo (kg)")
                if past_f.empty or produccion.empty:
                    st.info("No hay pasteurizaciones en este período.")
                else:
                    past_tipo = past_f.merge(
                        produccion[["lote_semielaborado_id", "tipo_producto"]],
                        on="lote_semielaborado_id", how="left",
                    )
                    past_tipo["kg_usado"] = pd.to_numeric(past_tipo["kg_usado"], errors="coerce").fillna(0)
                    kg_pasteurizado_tipo = past_tipo.groupby("tipo_producto")["kg_usado"].sum()
                    st.metric("Huevo entero", f"{kg_pasteurizado_tipo.get('Huevo entero', 0):,.1f} kg")
                    st.metric("Clara", f"{kg_pasteurizado_tipo.get('Clara', 0):,.1f} kg")
                    st.metric("Yema", f"{kg_pasteurizado_tipo.get('Yema', 0):,.1f} kg")

        with col_pend:
            with st.container(border=True):
                st.markdown("##### 🛢️ Pendiente de pasteurizar (kg)")
                if prod_costos.empty:
                    st.info("No hay producción en este período.")
                else:
                    prod_saldo = prod_f.copy()
                    prod_saldo["kg_saldo"] = pd.to_numeric(prod_saldo["kg_saldo"], errors="coerce").fillna(0)
                    kg_saldo_tipo = prod_saldo.groupby("tipo_producto")["kg_saldo"].sum()
                    st.metric("Huevo entero", f"{kg_saldo_tipo.get('Huevo entero', 0):,.1f} kg")
                    st.metric("Clara", f"{kg_saldo_tipo.get('Clara', 0):,.1f} kg")
                    st.metric("Yema", f"{kg_saldo_tipo.get('Yema', 0):,.1f} kg")

    # ======================== TAB: PERSONAL ========================
    with tabs[2]:
        with st.container(border=True):
            st.markdown("##### 👷 Horas de trabajo por persona")
            st.caption(
                "Normales = hasta 8h en día normal · Extras = lo que excede 8h en día "
                "normal · Dobles = horas en feriado sin compensación · Compensadas = "
                "horas en feriado con descanso acordado en su lugar · Nocturnas = "
                "horas entre 19:00 y 05:00 (eje aparte, se cruza con las demás)."
            )

            if not personal_detalle.empty and not produccion.empty:
                ph = personal_detalle.merge(
                    produccion[["lote_semielaborado_id", "fecha"]], on="lote_semielaborado_id", how="left",
                )
            else:
                ph = pd.DataFrame(columns=["personal_id", "fecha", "horas", "horas_nocturnas", "costo_calculado"])

            superv = db.get_df("supervision_diaria")
            if not superv.empty:
                superv_cols = superv[["personal_id", "fecha", "horas", "horas_nocturnas", "costo_calculado"]].copy()
                ph = pd.concat([ph, superv_cols], ignore_index=True)

            ph = _filtrar_por_fecha(ph, desde, hasta)
            ph["horas"] = pd.to_numeric(ph.get("horas"), errors="coerce").fillna(0)
            ph["horas_nocturnas"] = pd.to_numeric(ph.get("horas_nocturnas"), errors="coerce").fillna(0)
            ph["costo_calculado"] = pd.to_numeric(ph.get("costo_calculado"), errors="coerce").fillna(0)

            # costo y horas nocturnas por persona (ejes aparte de la clasificacion)
            costo_por_persona = ph.groupby("personal_id")["costo_calculado"].sum() if not ph.empty else pd.Series(dtype=float)
            nocturnas_por_persona = ph.groupby("personal_id")["horas_nocturnas"].sum() if not ph.empty else pd.Series(dtype=float)

            # sumar horas del MISMO dia antes de clasificar (si una persona trabajo en
            # varios lotes el mismo dia, hay que sumarlas antes del limite de 8 horas)
            if not ph.empty:
                por_persona_dia = ph.groupby(["personal_id", "fecha"])["horas"].sum().reset_index()
                feriados_set = feriados_como_set(db.get_df("feriados"))
                compensados_set = compensaciones_como_set(db.get_df("compensaciones_feriado"))
                por_persona_dia = clasificar_horas_por_dia(por_persona_dia, feriados_set, compensados_set)
                resumen_por_id = por_persona_dia.groupby("personal_id").agg(
                    horas_normales=("horas_normales", "sum"),
                    horas_extras=("horas_extras", "sum"),
                    horas_dobles=("horas_dobles", "sum"),
                    horas_compensadas=("horas_compensadas", "sum"),
                    horas_totales=("horas", "sum"),
                )
            else:
                resumen_por_id = pd.DataFrame(columns=[
                    "horas_normales", "horas_extras", "horas_dobles", "horas_compensadas", "horas_totales",
                ])

            # reporte COMPLETO: parte de TODO el personal activo del catalogo, no
            # solo quienes tienen registros -- asi se ve quien NO trabajo el periodo
            if personal_cat.empty:
                st.info("Configura personal en Catálogos → Personal para ver este reporte.")
            else:
                activos = personal_cat[personal_cat.get("activo", "TRUE").astype(str).str.upper() != "FALSE"].copy()
                reporte = activos.set_index("personal_id").join(resumen_por_id, how="left")
                for col in ["horas_normales", "horas_extras", "horas_dobles", "horas_compensadas", "horas_totales"]:
                    reporte[col] = reporte[col].fillna(0)
                reporte["costo"] = reporte.index.map(costo_por_persona).fillna(0)
                reporte["horas_nocturnas"] = reporte.index.map(nocturnas_por_persona).fillna(0)
                reporte["trabajo"] = reporte["horas_totales"] > 0
                reporte = reporte.reset_index().sort_values("horas_totales", ascending=False)

                c1, c2, c3, c4, c5, c6 = st.columns(6)
                c1.metric("Horas normales", f"{reporte['horas_normales'].sum():,.1f}")
                c2.metric("Horas extras", f"{reporte['horas_extras'].sum():,.1f}")
                c3.metric("Horas dobles", f"{reporte['horas_dobles'].sum():,.1f}")
                c4.metric("Horas compensadas", f"{reporte['horas_compensadas'].sum():,.1f}")
                c5.metric("Horas nocturnas", f"{reporte['horas_nocturnas'].sum():,.1f}")
                c6.metric("Costo mano de obra", f"{reporte['costo'].sum():,.2f}")

                sin_trabajar = reporte[~reporte["trabajo"]]
                if not sin_trabajar.empty:
                    st.warning(
                        f"⚠️ {len(sin_trabajar)} persona(s) sin registros en este período: "
                        f"{', '.join(sin_trabajar['nombre'])}"
                    )

                st.caption("'Nocturnas' es un eje aparte (cuándo se trabajó) — esas horas también están incluidas en normales/extras/dobles según corresponda.")
                st.caption("El costo de esta tabla incluye mano de obra directa de producción + supervisión/calidad combinados — para el costo por kg de cada lote (que NO incluye supervisión), ve a la pestaña 'Producción y costos'.")
                columnas_mostrar = [c for c in [
                    "nombre", "cargo", "tipo_personal", "trabajo", "horas_normales", "horas_extras",
                    "horas_dobles", "horas_compensadas", "horas_nocturnas", "horas_totales", "costo",
                ] if c in reporte.columns]
                st.dataframe(reporte[columnas_mostrar], use_container_width=True, hide_index=True)

                st.markdown("**Desglose de horas por persona**")
                st.bar_chart(
                    reporte.set_index("nombre")[
                        ["horas_normales", "horas_extras", "horas_dobles", "horas_compensadas"]
                    ]
                )

                st.write("")
                pdf_bytes = generar_pdf_horas_personal(reporte.to_dict("records"), desde, hasta)
                st.download_button(
                    "📄 Descargar reporte PDF de horas de personal",
                    data=pdf_bytes,
                    file_name=f"horas_personal_{desde.isoformat()}_a_{hasta.isoformat()}.pdf",
                    mime="application/pdf",
                    use_container_width=True,
                )

    # ======================== TAB: INSUMOS Y ENVASES ========================
    with tabs[3]:
        col_ins, col_env = st.columns(2)
        with col_ins:
            with st.container(border=True):
                st.markdown("##### 🧴 Insumos químicos consumidos")
                st.caption("Producción + limpieza y desinfección")
                salidas_insumos = pd.DataFrame()
                if not mov_f.empty:
                    salidas_insumos = mov_f[
                        (mov_f["item_tipo"] == "insumo") & (mov_f["tipo_movimiento"] == "salida")
                    ].copy()
                if salidas_insumos.empty:
                    st.info("No hay consumo de insumos en este período.")
                else:
                    salidas_insumos["cantidad"] = pd.to_numeric(salidas_insumos["cantidad"], errors="coerce").fillna(0)
                    salidas_insumos["costo_total"] = pd.to_numeric(salidas_insumos["costo_total"], errors="coerce").fillna(0)
                    if not insumos.empty:
                        salidas_insumos = salidas_insumos.merge(
                            insumos[["insumo_id", "nombre", "unidad"]].rename(columns={"insumo_id": "item_id"}),
                            on="item_id", how="left",
                        )
                        salidas_insumos["nombre"] = salidas_insumos["nombre"].fillna(salidas_insumos["item_id"])
                        salidas_insumos["unidad"] = salidas_insumos["unidad"].fillna("")
                    else:
                        salidas_insumos["nombre"] = salidas_insumos["item_id"]
                        salidas_insumos["unidad"] = ""
                    resumen_insumos = salidas_insumos.groupby(["nombre", "unidad"]).agg(
                        cantidad=("cantidad", "sum"), costo=("costo_total", "sum"),
                    ).reset_index().sort_values("cantidad", ascending=False)
                    st.metric("Costo total insumos", f"{resumen_insumos['costo'].sum():,.2f}")
                    st.dataframe(resumen_insumos, use_container_width=True, hide_index=True)

        with col_env:
            with st.container(border=True):
                st.markdown("##### 📦 Envases consumidos")
                salidas_envases = pd.DataFrame()
                if not mov_f.empty:
                    salidas_envases = mov_f[
                        (mov_f["item_tipo"] == "envase") & (mov_f["tipo_movimiento"] == "salida")
                    ].copy()
                if salidas_envases.empty:
                    st.info("No hay consumo de envases en este período.")
                else:
                    salidas_envases["cantidad"] = pd.to_numeric(salidas_envases["cantidad"], errors="coerce").fillna(0)
                    if not presentaciones.empty:
                        salidas_envases = salidas_envases.merge(
                            presentaciones[["presentacion_id", "nombre"]].rename(columns={"presentacion_id": "item_id"}),
                            on="item_id", how="left",
                        )
                        salidas_envases["nombre"] = salidas_envases["nombre"].fillna(salidas_envases["item_id"])
                    else:
                        salidas_envases["nombre"] = salidas_envases["item_id"]
                    resumen_envases = salidas_envases.groupby("nombre")["cantidad"].sum().reset_index()
                    resumen_envases.columns = ["Presentación", "Unidades"]
                    st.metric("Total unidades", f"{int(resumen_envases['Unidades'].sum()):,}")
                    st.dataframe(resumen_envases, use_container_width=True, hide_index=True)
                    st.bar_chart(resumen_envases.set_index("Presentación")["Unidades"])

    # ======================== TAB: RESIDUOS Y MERMAS ========================
    with tabs[4]:
        cascara_total = prod_costos["cascara_real_kg"].sum() if not prod_costos.empty else 0.0
        huevos_danados_total = _num(mermas_mp_f, "huevos_danados").sum()
        costo_mermas_mp = _num(mermas_mp_f, "costo_estimado").sum()

        m1, m2, m3 = st.columns(3)
        _tarjeta_metrica(m1, "Cáscara generada", f"{cascara_total:,.1f} kg")
        _tarjeta_metrica(m2, "Huevos dañados en bodega", f"{huevos_danados_total:,.0f}")
        _tarjeta_metrica(m3, "Costo huevo perdido", f"{costo_mermas_mp:,.2f}")

        st.write("")
        with st.container(border=True):
            st.markdown("##### 📦 Envases dañados por presentación")
            mermas_envases = pd.DataFrame()
            if not mov_f.empty:
                mermas_envases = mov_f[
                    (mov_f["item_tipo"] == "envase") & (mov_f["tipo_movimiento"] == "merma")
                ].copy()
            if mermas_envases.empty:
                st.info("No hay envases registrados como dañados en este período.")
            else:
                mermas_envases["cantidad"] = pd.to_numeric(mermas_envases["cantidad"], errors="coerce").fillna(0)
                if not presentaciones.empty:
                    mermas_envases = mermas_envases.merge(
                        presentaciones[["presentacion_id", "nombre"]].rename(columns={"presentacion_id": "item_id"}),
                        on="item_id", how="left",
                    )
                    mermas_envases["nombre"] = mermas_envases["nombre"].fillna(mermas_envases["item_id"])
                else:
                    mermas_envases["nombre"] = mermas_envases["item_id"]
                resumen_mermas_env = mermas_envases.groupby("nombre")["cantidad"].sum().reset_index()
                resumen_mermas_env.columns = ["Presentación", "Unidades dañadas"]
                st.dataframe(resumen_mermas_env, use_container_width=True, hide_index=True)

    # ======================== TAB: AGUA ========================
    with tabs[5]:
        with st.container(border=True):
            st.markdown("##### 💧 Agua usada")
            c1, c2, c3 = st.columns(3)
            c1.metric("En producción", f"{agua_produccion:,.0f} L")
            c2.metric("En limpieza/desinfección", f"{agua_limpieza:,.0f} L")
            c3.metric("Total", f"{agua_produccion + agua_limpieza:,.0f} L")
            if agua_produccion + agua_limpieza > 0:
                st.bar_chart(pd.Series({
                    "Producción": agua_produccion, "Limpieza/desinfección": agua_limpieza,
                }))
