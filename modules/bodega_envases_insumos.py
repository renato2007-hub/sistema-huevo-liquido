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


def render(db, username):
    st.title("Bodega de envases e insumos")
    tab_movimiento, tab_inventario = st.tabs(["Registrar movimiento", "Inventario actual"])

    insumos = db.get_df("insumos")
    presentaciones = db.get_df("presentaciones")

    with tab_movimiento:
        tipo_item = st.radio("Tipo de ítem", ["Insumo de limpieza", "Envase"], horizontal=True)
        catalogo = insumos if tipo_item == "Insumo de limpieza" else presentaciones
        col_id = "insumo_id" if tipo_item == "Insumo de limpieza" else "presentacion_id"

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
                    format_func=lambda x: catalogo.set_index(col_id).loc[x, "nombre"],
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
                saldo_previo = _saldo_actual(
                    db.get_df("movimientos_envases_insumos"),
                    "insumo" if tipo_item == "Insumo de limpieza" else "envase", item_id,
                )
                if tipo_movimiento in ("salida", "merma") and cantidad > saldo_previo:
                    st.warning(
                        f"⚠️ Vas a dejar el saldo en negativo: hay {saldo_previo:.0f} disponibles "
                        f"y estás sacando {cantidad:.0f}. Se va a guardar igual, pero revisa si "
                        f"falta registrar una compra anterior."
                    )
                movimiento_id = db.siguiente_id("movimientos_envases_insumos", "ENV", fecha)
                item_tipo = "insumo" if tipo_item == "Insumo de limpieza" else "envase"
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
                    ])
                    mermas_mostrar = mermas.merge(catalogo_todo, on="item_id", how="left")
                    st.dataframe(
                        mermas_mostrar[["fecha", "nombre", "cantidad", "causa", "costo_total", "observaciones"]],
                        use_container_width=True,
                    )
        else:
            st.info("Configura insumos y presentaciones en Catálogos para ver el inventario.")
