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
    tab_movimiento, tab_inventario, tab_ajuste = st.tabs([
        "Registrar movimiento", "Inventario actual", "✏️ Ajuste de inventario"
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
                cantidad = st.number_input("Cantidad", min_value=0.0, step=1.0)
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
            negativos = df_inv[df_inv["saldo"] < 0]
            if not negativos.empty:
                st.warning(
                    f"⚠️ {len(negativos)} ítem(s) con saldo negativo — significa que se registraron "
                    f"más salidas/mermas que entradas (probablemente falta registrar una compra)."
                )
            st.dataframe(df_inv, use_container_width=True)

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

        if df_cat.empty:
            st.info(f"No hay {tipo_ajuste.lower()}s configurados en Catálogos.")
        else:
            c1, c2, c3, c4 = st.columns([2, 1, 1, 2])
            item_sel = c1.selectbox(
                "Ítem", df_cat[id_col],
                format_func=lambda x: df_cat.set_index(id_col).loc[x, "nombre"],
                key="ajuste_item",
            )
            tipo_mov = c2.selectbox("Movimiento", ["entrada", "salida", "merma"], key="ajuste_mov")
            cantidad = c3.number_input("Cantidad", min_value=1, step=1, key="ajuste_cant")
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
                        "item_tipo": tipo_ajuste.split()[0].lower(),
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
                    st.success(f"✅ {cantidad} unidades {accion} de {item_sel} — motivo: {motivo}")
                    st.rerun()
