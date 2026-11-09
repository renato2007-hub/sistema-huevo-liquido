"""
Bodega de envases e insumos: compras (entradas), consumo (salidas) e inventario
por item. Las salidas hacia produccion, pasteurizacion y limpieza se registran
automaticamente desde esos modulos; aqui tambien se puede hacer un movimiento
manual (compra) cuando haga falta, y registrar mermas: envases que resultan
danados/defectuosos al momento de usarlos (no por culpa del proceso, sino
porque ya venian mal o se danaron en bodega).
"""
import datetime
import streamlit as st
import pandas as pd
from utils.permisos import ve_costos
import utils.graficas as graf
from html import escape

CAUSAS_MERMA = ["Llegó dañado del proveedor", "Se dañó en bodega", "Se dañó al manipular/usar", "Otro"]


def _saldo_actual(df_movimientos, item_tipo, item_id):
    if df_movimientos.empty:
        return 0.0
    sub = df_movimientos[
        (df_movimientos["item_tipo"] == item_tipo) & (df_movimientos["item_id"] == item_id)
    ]
    if sub.empty:
        return 0.0
    cantidades = pd.to_numeric(sub["cantidad"], errors="coerce").fillna(0)
    signo = sub["tipo_movimiento"].map({"entrada": 1, "salida": -1, "merma": -1}).fillna(0)
    return float((cantidades * signo).sum())


def render(db, username, rol):
    st.title("Bodega de envases e insumos")
    tab_movimiento, tab_inventario, tab_historial, tab_ajuste = st.tabs([
        "Registrar movimiento", "Inventario actual", "📋 Historial", "✏️ Ajuste de inventario"
    ])

    insumos = db.get_df("insumos")
    presentaciones = db.get_df("presentaciones")
    tapas = db.get_df("tapas")
    etiquetas = db.get_df("etiquetas")
    cartones = db.get_df("cartones")
    liners = db.get_df("liners")

    with tab_movimiento:
        tipo_item = st.radio(
            "Tipo de ítem",
            ["Insumo de limpieza", "Envase", "Tapa (PET)", "Etiqueta", "Cartón", "Liner de aluminio"],
            horizontal=True,
        )
        catalogo = {
            "Insumo de limpieza": insumos, "Envase": presentaciones,
            "Tapa (PET)": tapas, "Etiqueta": etiquetas, "Cartón": cartones, "Liner de aluminio": liners,
        }[tipo_item]
        col_id = {
            "Insumo de limpieza": "insumo_id", "Envase": "presentacion_id",
            "Tapa (PET)": "tapa_id", "Etiqueta": "etiqueta_id", "Cartón": "carton_id",
            "Liner de aluminio": "liner_id",
        }[tipo_item]
        col_nombre = "color" if tipo_item == "Tapa (PET)" else "nombre"

        if catalogo.empty:
            st.warning("No hay ítems configurados todavía en Catálogos.")
        else:
            tipo_movimiento = st.selectbox(
                "Movimiento", ["entrada", "salida", "merma"],
                format_func=lambda x: {
                    "entrada": "Entrada (compra)", "salida": "Salida (uso/consumo manual)",
                    "merma": "Merma (dañado/defectuoso)",
                }[x],
            )
            causa = ""
            if tipo_movimiento == "merma":
                causa = st.selectbox("Causa de la merma", CAUSAS_MERMA)

            with st.form("form_movimiento_envases"):
                fecha = st.date_input("Fecha", value=datetime.date.today())
                item_id = st.selectbox(
                    "Ítem", catalogo[col_id],
                    format_func=lambda x: catalogo.set_index(col_id).loc[x, col_nombre],
                )
                cantidad = round(st.number_input("Cantidad", min_value=0.0, step=1.0, format="%.2f"), 2)
                costo_unitario = st.number_input("Costo unitario", min_value=0.0, step=0.01, format="%.2f")
                proveedor = st.text_input(
                    "Proveedor (opcional, solo informativo — no es un catálogo, escribe el nombre libremente)", "",
                )
                modulo_destino = st.text_input(
                    "Módulo destino / motivo",
                    "Compra directa" if tipo_movimiento == "entrada" else "",
                )
                observaciones = st.text_area("Observaciones", "")
                guardar = st.form_submit_button("Registrar movimiento")

            if guardar:
                item_tipo_map = {
                    "Insumo de limpieza": "insumo", "Envase": "envase",
                    "Tapa (PET)": "tapa", "Etiqueta": "etiqueta", "Cartón": "carton",
                    "Liner de aluminio": "liner",
                }
                saldo_previo = _saldo_actual(
                    db.get_df("movimientos_envases_insumos"), item_tipo_map[tipo_item], item_id,
                )
                if tipo_movimiento in ("salida", "merma") and cantidad > saldo_previo:
                    st.warning(
                        f"⚠️ Vas a dejar el saldo en negativo: hay {saldo_previo:.0f} disponibles "
                        f"y estás sacando {cantidad:.0f}. Se va a guardar igual, pero revisa si "
                        f"falta registrar una compra anterior."
                    )
                movimiento_id = db.siguiente_id("movimientos_envases_insumos", "ENV", fecha)
                item_tipo = item_tipo_map[tipo_item]
                db.append_row("movimientos_envases_insumos", {
                    "movimiento_id": movimiento_id,
                    "fecha": fecha.isoformat(),
                    "item_tipo": item_tipo,
                    "item_id": item_id,
                    "tipo_movimiento": tipo_movimiento,
                    "causa": causa,
                    "cantidad": cantidad,
                    "costo_unitario": costo_unitario,
                    "costo_total": cantidad * costo_unitario,
                    "proveedor": proveedor,
                    "modulo_destino": modulo_destino,
                    "usuario": username,
                    "observaciones": observaciones,
                })
                st.success(f"Movimiento {movimiento_id} registrado.")

    with tab_inventario:
        df_movimientos = db.get_df("movimientos_envases_insumos")
        filas = []
        for _, row in insumos.iterrows():
            filas.append({
                "tipo": "Insumo", "item_id": row["insumo_id"], "nombre": row["nombre"],
                "saldo": _saldo_actual(df_movimientos, "insumo", row["insumo_id"]),
            })
        for _, row in presentaciones.iterrows():
            filas.append({
                "tipo": "Envase", "item_id": row["presentacion_id"], "nombre": row["nombre"],
                "saldo": _saldo_actual(df_movimientos, "envase", row["presentacion_id"]),
            })
        for _, row in tapas.iterrows():
            filas.append({
                "tipo": "Tapa", "item_id": row["tapa_id"], "nombre": row["color"],
                "saldo": _saldo_actual(df_movimientos, "tapa", row["tapa_id"]),
            })
        for _, row in etiquetas.iterrows():
            filas.append({
                "tipo": "Etiqueta", "item_id": row["etiqueta_id"], "nombre": row["nombre"],
                "saldo": _saldo_actual(df_movimientos, "etiqueta", row["etiqueta_id"]),
            })
        for _, row in cartones.iterrows():
            filas.append({
                "tipo": "Cartón", "item_id": row["carton_id"], "nombre": row["nombre"],
                "saldo": _saldo_actual(df_movimientos, "carton", row["carton_id"]),
            })
        for _, row in liners.iterrows():
            filas.append({
                "tipo": "Liner", "item_id": row["liner_id"], "nombre": row["nombre"],
                "saldo": _saldo_actual(df_movimientos, "liner", row["liner_id"]),
            })
        if filas:
            df_inv = pd.DataFrame(filas)
            df_inv["saldo"] = df_inv["saldo"].round(2)
            negativos = df_inv[df_inv["saldo"] < 0]
            if not negativos.empty:
                st.warning(
                    f"⚠️ {len(negativos)} ítem(s) con saldo negativo — significa que se registraron "
                    f"más salidas/mermas que entradas (probablemente falta registrar una compra)."
                )
            st.dataframe(
                df_inv, use_container_width=True,
                column_config={"saldo": st.column_config.NumberColumn(format="%.2f")},
            )

            # Gráficas de barras por tipo de ítem
            import plotly.graph_objects as go
            st.write("")
            COLORES = {
                "Insumo": "#00695c", "Envase": "#1565c0",
                "Tapa": "#f9a825", "Etiqueta": "#6a1b9a",
                "Cartón": "#D9740C", "Liner": "#2e7d32",
            }
            tipos = df_inv["tipo"].unique().tolist()
            cols_graf = st.columns(min(len(tipos), 3))
            for col_g, tipo in zip(cols_graf * (len(tipos)//max(len(cols_graf),1)+1), tipos):
                df_tipo = df_inv[df_inv["tipo"] == tipo].copy()
                df_tipo = df_tipo[df_tipo["saldo"] != 0].sort_values("saldo", ascending=True)
                if df_tipo.empty:
                    continue
                fig = go.Figure(go.Bar(
                    x=df_tipo["saldo"].tolist(),
                    y=df_tipo["nombre"].tolist(),
                    orientation="h",
                    marker_color=COLORES.get(tipo, "#2e7d32"),
                    text=df_tipo["saldo"].apply(lambda v: f"{v:g}" if tipo != "Insumo" else f"{v:.2f}").tolist(),
                    textposition="auto",
                    textangle=0,
                    textfont=dict(size=11, family="Arial Black"),
                    hovertemplate="%{y}: %{x} unidades<extra></extra>",
                ))
                fig.update_layout(
                    title=f"{tipo}s",
                    height=max(180, len(df_tipo) * 45),
                    margin=dict(l=10, r=50, t=40, b=20),
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                )
                col_g.plotly_chart(fig, use_container_width=True)

            if not df_movimientos.empty and "tipo_movimiento" in df_movimientos.columns:
                mermas = df_movimientos[df_movimientos["tipo_movimiento"] == "merma"]
                if not mermas.empty:
                    st.markdown("**Mermas registradas (histórico completo):**")
                    catalogo_todo = pd.concat([
                        insumos[["insumo_id", "nombre"]].rename(columns={"insumo_id": "item_id"}) if not insumos.empty else pd.DataFrame(columns=["item_id", "nombre"]),
                        presentaciones[["presentacion_id", "nombre"]].rename(columns={"presentacion_id": "item_id"}) if not presentaciones.empty else pd.DataFrame(columns=["item_id", "nombre"]),
                        tapas[["tapa_id", "color"]].rename(columns={"tapa_id": "item_id", "color": "nombre"}) if not tapas.empty else pd.DataFrame(columns=["item_id", "nombre"]),
                        etiquetas[["etiqueta_id", "nombre"]].rename(columns={"etiqueta_id": "item_id"}) if not etiquetas.empty else pd.DataFrame(columns=["item_id", "nombre"]),
                        cartones[["carton_id", "nombre"]].rename(columns={"carton_id": "item_id"}) if not cartones.empty else pd.DataFrame(columns=["item_id", "nombre"]),
                        liners[["liner_id", "nombre"]].rename(columns={"liner_id": "item_id"}) if not liners.empty else pd.DataFrame(columns=["item_id", "nombre"]),
                    ])
                    mermas_mostrar = mermas.merge(catalogo_todo, on="item_id", how="left")
                    columnas_merma = ["fecha", "nombre", "cantidad", "causa", "observaciones"]
                    if ve_costos(rol):
                        columnas_merma.insert(4, "costo_total")
                    st.dataframe(mermas_mostrar[columnas_merma], use_container_width=True)
        else:
            st.info("Configura insumos y presentaciones en Catálogos para ver el inventario.")

    # ======================== HISTORIAL ========================
    with tab_historial:
        _render_historial(db, rol, {
            "insumo": (insumos, "insumo_id", "nombre"),
            "envase": (presentaciones, "presentacion_id", "nombre"),
            "tapa": (tapas, "tapa_id", "color"),
            "etiqueta": (etiquetas, "etiqueta_id", "nombre"),
            "carton": (cartones, "carton_id", "nombre"),
            "liner": (liners, "liner_id", "nombre"),
        })

    # ======================== AJUSTE DE INVENTARIO ========================
    with tab_ajuste:
        st.caption("Registra entradas o salidas manuales para corregir el inventario — por errores de conteo, devoluciones, etc.")

        tipo_ajuste = st.radio("Tipo de ítem a ajustar",
            ["Insumo de limpieza", "Envase", "Tapa (PET)", "Etiqueta", "Cartón", "Liner de aluminio"],
            horizontal=True, key="ajuste_tipo")

        cat_ajuste = {
            "Insumo de limpieza": (insumos, "insumo_id"),
            "Envase": (presentaciones, "presentacion_id"),
            "Tapa (PET)": (tapas, "tapa_id"),
            "Etiqueta": (etiquetas, "etiqueta_id"),
            "Cartón": (cartones, "carton_id"),
            "Liner de aluminio": (liners, "liner_id"),
        }[tipo_ajuste]
        df_cat, id_col = cat_ajuste
        # Las tapas no tienen columna "nombre" -- se identifican por "color".
        col_nombre_ajuste = "color" if tipo_ajuste == "Tapa (PET)" else "nombre"

        if df_cat.empty:
            st.info(f"No hay {tipo_ajuste.lower()}s configurados en Catálogos.")
        else:
            c1, c2, c3, c4 = st.columns([2, 1, 1, 2])
            item_sel = c1.selectbox(
                "Ítem", df_cat[id_col],
                format_func=lambda x: df_cat.set_index(id_col).loc[x, col_nombre_ajuste],
                key="ajuste_item",
            )
            tipo_mov = c2.selectbox("Movimiento", ["entrada", "salida", "merma"], key="ajuste_mov")
            cantidad = c3.number_input(
                "Cantidad", min_value=0.01, step=0.01, format="%.2f", key="ajuste_cant",
            )
            motivo   = c4.text_input("Motivo del ajuste", "", key="ajuste_motivo")

            fecha_aj = st.date_input("Fecha del ajuste", value=datetime.date.today(), key="ajuste_fecha")

            if st.button("💾 Registrar ajuste", type="primary", use_container_width=True):
                if not motivo.strip():
                    st.error("Escribe el motivo del ajuste.")
                else:
                    import datetime as _dt
                    mov_id = db.siguiente_id("movimientos_envases_insumos", "AJ", fecha_aj)
                    costo_unit = 0.0
                    if "costo_unitario" in df_cat.columns:
                        costo_unit = float(pd.to_numeric(
                            df_cat.set_index(id_col).loc[item_sel, "costo_unitario"],
                            errors="coerce") or 0)
                    db.append_row("movimientos_envases_insumos", {
                        "movimiento_id": mov_id,
                        "fecha": fecha_aj.isoformat(),
                        # Mapa explicito: derivarlo del texto ("Cartón" -> "cartón")
                        # dejaba la tilde y el inventario, que busca "carton", no
                        # veia el ajuste.
                        "item_tipo": {
                            "Insumo de limpieza": "insumo", "Envase": "envase",
                            "Tapa (PET)": "tapa", "Etiqueta": "etiqueta",
                            "Cartón": "carton", "Liner de aluminio": "liner",
                        }[tipo_ajuste],
                        "item_id": item_sel,
                        "tipo_movimiento": tipo_mov,
                        "cantidad": cantidad,
                        "costo_unitario": costo_unit,
                        "costo_total": costo_unit * cantidad,
                        "referencia": "ajuste_manual",
                        "usuario": username,
                        "observaciones": f"Ajuste manual: {motivo}",
                    })
                    accion = "ingresadas" if tipo_mov == "entrada" else "descontadas"
                    st.success(f"✅ {cantidad:.2f} unidades {accion} de {item_sel} — motivo: {motivo}")
                    st.rerun()


NOMBRES_TIPO_ITEM = {
    "insumo": "Insumo", "envase": "Envase", "tapa": "Tapa",
    "etiqueta": "Etiqueta", "carton": "Cartón", "liner": "Liner",
}
NOMBRES_MOVIMIENTO = {"entrada": "Entrada", "salida": "Salida", "merma": "Merma"}


def _render_historial(db, rol, catalogos):
    """Kardex de la bodega: cada movimiento con su ingreso, salida, el saldo
    del item despues de ese movimiento y el costo total, filtrado por periodo.
    El saldo se calcula con TODO el historico (no solo con el periodo
    filtrado) para que sea el saldo real que tenia el item en esa fecha."""
    movimientos = db.get_df("movimientos_envases_insumos")
    if movimientos.empty:
        st.info("Todavía no hay movimientos registrados en la bodega.")
        return

    # Nombre (y unidad, para insumos) de cada item segun su catalogo.
    nombres, unidades = {}, {}
    for item_tipo, (df_cat, col_id, col_nombre) in catalogos.items():
        if df_cat.empty or col_id not in df_cat.columns:
            continue
        for _, fila in df_cat.iterrows():
            clave = (item_tipo, str(fila[col_id]))
            nombres[clave] = str(fila.get(col_nombre, "") or fila[col_id])
            unidades[clave] = str(fila.get("unidad", "") or "unid.") if item_tipo == "insumo" else "unid."

    mov = movimientos.copy()
    mov["orden"] = range(len(mov))  # orden de registro, para desempatar el mismo dia
    mov["item_tipo"] = mov["item_tipo"].astype(str).str.strip().str.lower()
    mov["item_id"] = mov["item_id"].astype(str)
    mov["tipo_movimiento"] = mov["tipo_movimiento"].astype(str).str.strip().str.lower()
    mov["fecha"] = pd.to_datetime(mov["fecha"], errors="coerce")
    cantidad = pd.to_numeric(mov["cantidad"], errors="coerce").fillna(0)
    es_entrada = mov["tipo_movimiento"] == "entrada"
    es_salida = mov["tipo_movimiento"].isin(["salida", "merma"])
    mov["ingreso"] = cantidad.where(es_entrada, 0.0)
    mov["salida"] = cantidad.where(es_salida, 0.0)
    mov["costo_total"] = pd.to_numeric(mov["costo_total"], errors="coerce").fillna(0)
    claves = list(zip(mov["item_tipo"], mov["item_id"]))
    mov["item"] = [nombres.get(k, k[1]) for k in claves]
    mov["unidad"] = [unidades.get(k, "unid.") for k in claves]
    mov["tipo"] = mov["item_tipo"].map(NOMBRES_TIPO_ITEM).fillna(mov["item_tipo"])

    mov = mov.sort_values(["fecha", "orden"], na_position="first")
    mov["saldo"] = (mov["ingreso"] - mov["salida"]).groupby([mov["item_tipo"], mov["item_id"]]).cumsum()

    # ---- filtros ----
    c1, c2, c3, c4 = st.columns(4)
    desde = c1.date_input(
        "Desde", value=datetime.date.today() - datetime.timedelta(days=30), key="env_hist_desde",
    )
    hasta = c2.date_input("Hasta", value=datetime.date.today(), key="env_hist_hasta")
    tipos_presentes = [t for t in NOMBRES_TIPO_ITEM.values() if t in set(mov["tipo"])]
    tipo_sel = c3.selectbox("Tipo de ítem", ["Todos"] + tipos_presentes, key="env_hist_tipo")
    items_disponibles = sorted(mov.loc[mov["tipo"] == tipo_sel, "item"].unique()) if tipo_sel != "Todos" else []
    item_sel = c4.selectbox(
        "Ítem", ["Todos"] + items_disponibles, key="env_hist_item",
        disabled=tipo_sel == "Todos",
    )
    if desde > hasta:
        st.error("La fecha 'Desde' no puede ser posterior a 'Hasta'.")
        return

    periodo = mov[
        (mov["fecha"].dt.date >= desde) & (mov["fecha"].dt.date <= hasta)
    ]
    if tipo_sel != "Todos":
        periodo = periodo[periodo["tipo"] == tipo_sel]
        if item_sel != "Todos":
            periodo = periodo[periodo["item"] == item_sel]
    sub_graficas, sub_movimientos = st.tabs(["📊 Gráficas", "📋 Movimientos"])
    with sub_graficas:
        _render_graficas_env(db, periodo, tipo_sel, item_sel, desde, hasta, rol)
    with sub_movimientos:
        _render_movimientos_env(periodo, rol, item_sel, hasta)


def _render_movimientos_env(periodo, rol, item_sel, hasta):
    """Tabla de movimientos (kardex) del periodo filtrado."""
    if periodo.empty:
        st.info("No hay movimientos en ese período.")
        return

    # ---- resumen ----
    m = st.columns(4 if ve_costos(rol) else 2)
    m[0].metric("Movimientos", f"{len(periodo):,}")
    m[1].metric("Ítems con movimiento", f"{periodo.groupby(['item_tipo', 'item_id']).ngroups:,}")
    if ve_costos(rol):
        m[2].metric("Costo de ingresos ($)", f"{periodo.loc[periodo['ingreso'] > 0, 'costo_total'].sum():,.2f}")
        m[3].metric("Costo de salidas ($)", f"{periodo.loc[periodo['salida'] > 0, 'costo_total'].sum():,.2f}")
    if item_sel != "Todos":
        st.caption(
            f"**{item_sel}** — ingresó {periodo['ingreso'].sum():,.2f}, "
            f"salió {periodo['salida'].sum():,.2f}, saldo al {hasta.isoformat()}: "
            f"{periodo['saldo'].iloc[-1]:,.2f} {periodo['unidad'].iloc[0]}"
        )

    # ---- tabla ----
    tabla = periodo.copy()
    tabla["fecha"] = tabla["fecha"].dt.date
    tabla["movimiento"] = tabla["tipo_movimiento"].map(NOMBRES_MOVIMIENTO).fillna(tabla["tipo_movimiento"])
    columnas = ["fecha", "tipo", "item", "unidad", "movimiento", "ingreso", "salida", "saldo"]
    if ve_costos(rol):
        columnas.append("costo_total")
    columnas += [c for c in ["modulo_destino", "usuario", "observaciones"] if c in tabla.columns]
    st.dataframe(
        tabla[columnas].rename(columns={
            "fecha": "Fecha", "tipo": "Tipo", "item": "Ítem", "unidad": "Unidad",
            "movimiento": "Movimiento", "ingreso": "Ingreso", "salida": "Salida",
            "saldo": "Saldo", "costo_total": "Costo total ($)",
            "modulo_destino": "Destino / motivo", "usuario": "Usuario",
            "observaciones": "Observaciones",
        }),
        use_container_width=True, hide_index=True,
        column_config={
            "Fecha": st.column_config.DateColumn(format="YYYY-MM-DD"),
            "Ingreso": st.column_config.NumberColumn(format="%.2f"),
            "Salida": st.column_config.NumberColumn(format="%.2f"),
            "Saldo": st.column_config.NumberColumn(format="%.2f"),
            "Costo total ($)": st.column_config.NumberColumn(format="%.2f"),
        },
    )
    st.caption(
        "**Saldo** = existencias del ítem después de cada movimiento (calculado con todo "
        "el histórico). Las mermas cuentan como salida."
    )


# ============================ GRÁFICAS DEL HISTORIAL ============================
# Un color fijo por cada cosa que se grafica (paleta categórica validada).
COLOR_CONSUMO = graf.VIOLETA
COLOR_COMPRAS = graf.AQUA
COLOR_AREA = graf.AZUL
COLOR_MERMA = graf.NARANJA
COLOR_TIPO = {
    "Insumo": graf.AZUL, "Envase": graf.NARANJA, "Tapa": graf.AQUA,
    "Etiqueta": graf.AMARILLO, "Cartón": graf.MAGENTA, "Liner": graf.VERDE,
}


def _clasificar_movimientos(periodo):
    """Separa los movimientos del periodo en consumo, compras, mermas y
    ajustes. Los ajustes son los registrados en '✏️ Ajuste de inventario'
    (codigo AJ-...); el resto de entradas son compras, el resto de salidas
    son consumo (produccion, pasteurizacion, limpieza o uso manual)."""
    p = periodo.copy()
    es_ajuste = p["movimiento_id"].astype(str).str.upper().str.startswith("AJ")
    p["area"] = p["modulo_destino"].astype(str).str.strip().replace({"": "Salida manual", "nan": "Salida manual"})
    p["proveedor_txt"] = p["proveedor"].astype(str).str.strip().replace({"": "Sin proveedor", "nan": "Sin proveedor"})
    p["causa_txt"] = p["causa"].astype(str).str.strip().replace({"": "Sin causa", "nan": "Sin causa"})
    consumo = p[~es_ajuste & (p["tipo_movimiento"] == "salida")]
    compras = p[~es_ajuste & (p["tipo_movimiento"] == "entrada")]
    mermas = p[~es_ajuste & (p["tipo_movimiento"] == "merma")]
    ajustes = p[es_ajuste]
    return consumo, compras, mermas, ajustes


def _ranking(titulo, df, grupo, columna, color, unidad, rol, vacio, etiqueta_grupo):
    """Barras horizontales del total por grupo + cuadro con su analisis."""
    st.markdown(f"##### {titulo}")
    if df.empty or df[columna].sum() == 0:
        st.info(vacio)
        return
    tabla = df.groupby(grupo).agg(
        total=(columna, "sum"), costo=("costo_total", "sum"), registros=(columna, "count"),
    ).sort_values("total")
    hover = f"%{{y}}<br><b>%{{x:,.2f}}</b> {unidad} en %{{customdata[0]}} registros"
    if ve_costos(rol):
        hover += "<br>Costo: $%{customdata[1]:,.2f}"
    graf.barras_horizontales(
        tabla.index.astype(str).tolist(), tabla["total"].tolist(), color,
        eje=unidad, hover=hover, customdata=tabla[["registros", "costo"]].values,
    )
    total = float(tabla["total"].sum())
    principal = tabla["total"].idxmax()
    celdas = [
        ("Total", f"<b>{graf.fmt(total)}</b> {unidad}"),
        (etiqueta_grupo, f"{len(tabla)}"),
        ("Principal", f"{escape(str(principal))} ({tabla.loc[principal, 'total'] / total * 100:,.0f}%)"),
        ("Registros", f"{int(tabla['registros'].sum())}"),
    ]
    if ve_costos(rol):
        costo = float(tabla["costo"].sum())
        celdas += [
            ("Costo total", f"${costo:,.2f}"),
            (f"Costo prom./{unidad}", f"${costo / total:,.2f}" if total else "—"),
        ]
    graf.cuadro_html(celdas)


def _render_graficas_env(db, periodo, tipo_sel, item_sel, desde, hasta, rol):
    """Panel de gráficas del Historial de envases e insumos."""
    import plotly.graph_objects as go

    if tipo_sel == "Todos":
        st.info(
            "Elige un **Tipo de ítem** arriba (y si quieres un **Ítem**) para ver las gráficas "
            "de consumo, compras, mermas y ajustes en sus cantidades. Cada tipo se mide en su "
            "propia unidad, por eso no se suman entre sí."
        )
        if ve_costos(rol) and not periodo.empty:
            consumo, compras, _, _ = _clasificar_movimientos(periodo)
            g1, g2 = st.columns(2)
            for col, titulo, df in [(g1, "💸 Costo del consumo por tipo de ítem", consumo),
                                    (g2, "🛒 Costo de las compras por tipo de ítem", compras)]:
                with col:
                    st.markdown(f"##### {titulo}")
                    if df.empty or df["costo_total"].sum() == 0:
                        st.info("Sin movimientos con costo en este período.")
                        continue
                    por_tipo = df.groupby("tipo")["costo_total"].sum().sort_values()
                    fig = go.Figure(go.Bar(
                        x=por_tipo.values, y=por_tipo.index, orientation="h",
                        marker_color=[COLOR_TIPO.get(t, graf.AZUL) for t in por_tipo.index],
                        text=[f"${v:,.2f}" for v in por_tipo.values], textposition="outside",
                        textfont=dict(color="#52514e"), cliponaxis=False,
                        hovertemplate="%{y}<br><b>$%{x:,.2f}</b><extra></extra>",
                    ))
                    fig = graf.layout_base(fig, alto=max(220, 40 * len(por_tipo) + 60))
                    fig.update_layout(xaxis=dict(title="Costo ($)", showgrid=True,
                                                 gridcolor="rgba(128,128,128,0.15)"),
                                      yaxis=dict(title=None), margin=dict(l=10, r=70, t=10, b=10))
                    st.plotly_chart(fig, use_container_width=True)
                    total = float(por_tipo.sum())
                    graf.cuadro_html([
                        ("Total", f"<b>${total:,.2f}</b>"),
                        ("Mayor", f"{escape(str(por_tipo.idxmax()))} ({por_tipo.max() / total * 100:,.0f}%)"),
                    ])
        return

    freq_nombre, freq = graf.selector_granularidad(desde, hasta, key="env_graf_freq")
    consumo, compras, mermas, ajustes = _clasificar_movimientos(periodo)
    unidades = periodo["unidad"].dropna().unique().tolist() if not periodo.empty else []
    unidad = unidades[0] if len(unidades) == 1 else "unid."
    if len(unidades) > 1:
        st.warning(
            f"Los ítems de este tipo usan unidades distintas ({', '.join(unidades)}). Las gráficas "
            f"por período suman todo junto; elige un **Ítem** para verlo en su propia unidad."
        )
    dias_prod = graf.dias_produccion(db, desde, hasta)
    que = item_sel if item_sel != "Todos" else f"{tipo_sel.lower()}s"

    # ---- resumen rápido ----
    r = st.columns(4)
    r[0].metric("Consumo", f"{graf.fmt(consumo['salida'].sum())} {unidad}")
    r[1].metric("Compras", f"{graf.fmt(compras['ingreso'].sum())} {unidad}")
    r[2].metric("Mermas", f"{graf.fmt(mermas['salida'].sum())} {unidad}")
    r[3].metric(
        "Días de producción", f"{len(dias_prod)} de {(hasta - desde).days + 1}",
        help="Días del período en que se produjo semielaborado. Los promedios diarios se calculan sobre estos días.",
    )

    g1, g2 = st.columns(2)
    with g1:
        graf.grafica_serie(
            f"📦 Consumo de {que}", consumo, "salida", COLOR_CONSUMO, desde, hasta, freq, freq_nombre,
            "No hubo consumo en este período.", dias_prod, unidad=unidad, eje=unidad,
        )
    with g2:
        graf.grafica_serie(
            f"🛒 Compras de {que}", compras, "ingreso", COLOR_COMPRAS, desde, hasta, freq, freq_nombre,
            "No hubo compras en este período.", dias_prod, unidad=unidad, eje=unidad,
        )

    g3, g4 = st.columns(2)
    with g3:
        if item_sel == "Todos":
            _ranking(
                f"📦 Consumo por ítem ({tipo_sel.lower()}s)", consumo, "item", "salida", COLOR_CONSUMO,
                unidad, rol, "No hubo consumo en este período.", "Ítems",
            )
        else:
            _ranking(
                "🛒 Compras por proveedor", compras, "proveedor_txt", "ingreso", COLOR_COMPRAS,
                unidad, rol, "No hubo compras en este período.", "Proveedores",
            )
    with g4:
        _ranking(
            "🏭 Consumo por área", consumo, "area", "salida", COLOR_AREA,
            unidad, rol, "No hubo consumo en este período.", "Áreas",
        )

    g5, g6 = st.columns(2)
    with g5:
        if item_sel == "Todos":
            _ranking(
                "🛒 Compras por proveedor", compras, "proveedor_txt", "ingreso", COLOR_COMPRAS,
                unidad, rol, "No hubo compras en este período.", "Proveedores",
            )
        else:
            _ranking(
                "⚠️ Mermas por causa", mermas, "causa_txt", "salida", COLOR_MERMA,
                unidad, rol, "No hubo mermas en este período.", "Causas",
            )
    with g6:
        st.markdown("##### ⚖️ Ajustes de inventario")
        if ajustes.empty:
            st.info("No hubo ajustes en este período.")
        else:
            mas = graf.serie_por_periodo(ajustes, "ingreso", desde, hasta, freq)
            menos = graf.serie_por_periodo(ajustes, "salida", desde, hasta, freq)
            if freq == "D":
                visibles = [(p.start_time.date() in dias_prod) or mas[p] > 0 or menos[p] > 0 for p in mas.index]
                mas, menos = mas[visibles], menos[visibles]
            etiquetas = [graf.etiqueta_periodo(p, freq) for p in mas.index]
            fig = go.Figure([
                go.Bar(x=etiquetas, y=mas.values, name="Agregadas", marker_color=graf.COLOR_POSITIVO,
                       hovertemplate=f"%{{x}}<br>Agregadas: <b>%{{y:,.2f}}</b> {unidad}<extra></extra>"),
                go.Bar(x=etiquetas, y=-menos.values, name="Quitadas", marker_color=graf.COLOR_NEGATIVO,
                       customdata=menos.values,
                       hovertemplate=f"%{{x}}<br>Quitadas: <b>%{{customdata:,.2f}}</b> {unidad}<extra></extra>"),
            ])
            fig = graf.layout_base(fig, leyenda=True, eje=unidad)
            fig.update_layout(barmode="relative")
            fig.update_xaxes(type="category")
            st.plotly_chart(fig, use_container_width=True)
            agregadas, quitadas = float(mas.sum()), float(menos.sum())
            neto = agregadas - quitadas
            movido = float(consumo["salida"].sum() + compras["ingreso"].sum())
            graf.cuadro_html([
                ("Registros", f"{len(ajustes)}"),
                ("Agregadas", f"+{graf.fmt(agregadas)} {unidad}"),
                ("Quitadas", f"−{graf.fmt(quitadas)} {unidad}"),
                ("Neto", f"<b>{'+' if neto >= 0 else '−'}{graf.fmt(abs(neto))}</b> {unidad}"),
                ("Neto vs. movido", f"{abs(neto) / movido * 100:,.1f}%" if movido else "—"),
            ])

    if item_sel == "Todos":
        g7, _ = st.columns(2)
        with g7:
            _ranking(
                "⚠️ Mermas por causa", mermas, "causa_txt", "salida", COLOR_MERMA,
                unidad, rol, "No hubo mermas en este período.", "Causas",
            )

    st.caption(
        "Las gráficas usan el período Desde/Hasta y el Tipo/Ítem elegidos arriba. **Consumo** = "
        "salidas hacia producción, pasteurización, limpieza o uso manual; **compras** = entradas; "
        "las **mermas** y los **ajustes** (registrados en ✏️ Ajuste de inventario) van aparte. "
        "Los promedios diarios usan solo los días en que se produjo semielaborado. "
        "**Variabilidad** = desviación estándar por período; **Tendencia** = pendiente de la recta de mejor ajuste."
    )
