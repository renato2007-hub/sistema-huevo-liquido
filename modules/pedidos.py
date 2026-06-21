"""
Recepcion de pedidos: lo que gerencia registra cada dia conforme van llegando
pedidos de clientes por distintos medios (correo, WhatsApp, mensaje de texto,
llamada). No es parte de la cadena de costeo/produccion -- es un registro de
DEMANDA, para poder ver de un vistazo que falta producir y que ya se cumplio.

Cada pedido lleva un numero secuencial propio (PED-AAAAMMDD-NNN) ademas del
numero que el cliente haya usado de su lado (si lo dio), y un estado simple
de "producido: si/no" que se actualiza a mano conforme se va cumpliendo.
"""
import datetime
import streamlit as st
import pandas as pd

MEDIOS_RECEPCION = ["Correo", "WhatsApp", "Mensaje de texto", "Llamada", "Otro"]


def render(db, username, rol):
    st.title("🧾 Recepción de pedidos")
    tab_nuevo, tab_pendientes, tab_todos = st.tabs(
        ["➕ Registrar pedido", "🟡 Pendientes de producir", "📋 Todos los pedidos"]
    )

    clientes = db.get_df("clientes")
    presentaciones = db.get_df("presentaciones")

    # ======================== REGISTRAR PEDIDO ========================
    with tab_nuevo:
        if clientes.empty:
            st.warning("Configura al menos un cliente en Catálogos → Clientes antes de registrar pedidos.")
            return
        if presentaciones.empty:
            st.warning("Configura al menos una presentación en Catálogos → Presentaciones antes de registrar pedidos.")
            return

        with st.container(border=True):
            st.markdown("##### 📋 Datos del pedido")
            c1, c2, c3 = st.columns(3)
            cliente_id = c1.selectbox(
                "Cliente", clientes["cliente_id"],
                format_func=lambda x: clientes.set_index("cliente_id").loc[x, "nombre"],
            )
            medio_recepcion = c2.selectbox("Medio de recepción", MEDIOS_RECEPCION)
            ciudad = c3.text_input("Ciudad", "Quito")

            c4, c5 = st.columns(2)
            pedido_cliente_ref = c4.text_input("N° de pedido del cliente (si lo dieron)", "")
            fecha_pedido = c5.date_input("Fecha en que hicieron el pedido", value=datetime.date.today())

        with st.container(border=True):
            st.markdown("##### 🥚 Qué piden")
            c1, c2, c3 = st.columns(3)
            tipo_producto = c1.selectbox("Producto", ["Huevo entero", "Clara", "Yema"])
            presentacion_id = c2.selectbox(
                "Presentación", presentaciones["presentacion_id"],
                format_func=lambda x: presentaciones.set_index("presentacion_id").loc[x, "nombre"],
            )
            cantidad_kg = c3.number_input("Cantidad (kg)", min_value=0.0, step=0.5)

            kg_nominal = float(presentaciones.set_index("presentacion_id").loc[presentacion_id, "kg_nominal"])
            if cantidad_kg > 0 and kg_nominal > 0:
                st.caption(f"≈ {cantidad_kg / kg_nominal:.1f} unidades de esa presentación")

        with st.container(border=True):
            st.markdown("##### 📅 Fechas comprometidas")
            c1, c2 = st.columns(2)
            fecha_produccion = c1.date_input("Fecha de producción planeada", value=datetime.date.today())
            fecha_entrega = c2.date_input("Fecha en que se debe entregar", value=datetime.date.today())

        observaciones = st.text_area("Observaciones", "", key="pedido_obs")

        if st.button("💾 Guardar pedido", type="primary", use_container_width=True):
            if cantidad_kg <= 0:
                st.error("La cantidad en kg debe ser mayor a 0.")
            else:
                pedido_id = db.siguiente_id("pedidos", "PED", fecha_pedido)
                db.append_row("pedidos", {
                    "pedido_id": pedido_id,
                    "pedido_cliente_ref": pedido_cliente_ref,
                    "cliente_id": cliente_id,
                    "medio_recepcion": medio_recepcion,
                    "ciudad": ciudad,
                    "tipo_producto": tipo_producto,
                    "presentacion_id": presentacion_id,
                    "cantidad_kg": cantidad_kg,
                    "fecha_pedido": fecha_pedido.isoformat(),
                    "fecha_produccion": fecha_produccion.isoformat(),
                    "fecha_entrega": fecha_entrega.isoformat(),
                    "producido": False,
                    "usuario": username,
                    "observaciones": observaciones,
                })
                st.success(f"✅ Pedido {pedido_id} guardado.")
                st.rerun()

    # ======================== helper para mostrar tablas ========================
    def _enriquecer(df):
        if df.empty:
            return df
        df = df.copy()
        if not clientes.empty:
            df = df.merge(
                clientes[["cliente_id", "nombre"]].rename(columns={"nombre": "cliente_nombre"}),
                on="cliente_id", how="left",
            )
            df["cliente_nombre"] = df["cliente_nombre"].fillna(df["cliente_id"])
        else:
            df["cliente_nombre"] = df["cliente_id"]
        if not presentaciones.empty:
            df = df.merge(
                presentaciones[["presentacion_id", "nombre"]].rename(columns={"nombre": "presentacion_nombre"}),
                on="presentacion_id", how="left",
            )
            df["presentacion_nombre"] = df["presentacion_nombre"].fillna(df["presentacion_id"])
        else:
            df["presentacion_nombre"] = df["presentacion_id"]
        df["producido_bool"] = df["producido"].astype(str).str.upper().isin(["TRUE", "1", "SI", "SÍ"])
        df["cantidad_kg"] = pd.to_numeric(df["cantidad_kg"], errors="coerce").fillna(0)
        return df

    columnas_mostrar = [
        "pedido_id", "pedido_cliente_ref", "cliente_nombre", "ciudad", "medio_recepcion",
        "tipo_producto", "presentacion_nombre", "cantidad_kg",
        "fecha_pedido", "fecha_produccion", "fecha_entrega", "observaciones",
    ]

    # ======================== PENDIENTES DE PRODUCIR ========================
    with tab_pendientes:
        df = _enriquecer(db.get_df("pedidos"))
        if df.empty:
            st.info("No hay pedidos registrados todavía.")
        else:
            pendientes = df[~df["producido_bool"]].sort_values("fecha_entrega")
            if pendientes.empty:
                st.success("🎉 No hay pedidos pendientes — todo lo registrado ya está marcado como producido.")
            else:
                hoy = datetime.date.today().isoformat()
                atrasados = pendientes[pendientes["fecha_entrega"] < hoy]
                if not atrasados.empty:
                    st.error(f"⚠️ {len(atrasados)} pedido(s) con fecha de entrega ya vencida.")

                st.metric("Pedidos pendientes de producir", len(pendientes))
                st.dataframe(pendientes[columnas_mostrar], use_container_width=True, hide_index=True)

                st.write("")
                st.markdown("##### ✅ Marcar pedido como producido")
                pedido_sel = st.selectbox(
                    "Pedido", pendientes["pedido_id"],
                    format_func=lambda x: (
                        f"{x} — {pendientes.set_index('pedido_id').loc[x, 'cliente_nombre']} "
                        f"({pendientes.set_index('pedido_id').loc[x, 'cantidad_kg']:.1f} kg)"
                    ),
                )
                if st.button("✅ Marcar como producido"):
                    db.update_row("pedidos", "pedido_id", pedido_sel, {"producido": True})
                    st.success(f"Pedido {pedido_sel} marcado como producido.")
                    st.rerun()

    # ======================== TODOS LOS PEDIDOS ========================
    with tab_todos:
        df = _enriquecer(db.get_df("pedidos"))
        if df.empty:
            st.info("No hay pedidos registrados todavía.")
        else:
            c1, c2 = st.columns(2)
            filtro_estado = c1.selectbox("Estado", ["Todos", "Pendientes", "Producidos"])
            filtro_cliente = c2.selectbox(
                "Cliente", ["Todos"] + sorted(df["cliente_nombre"].dropna().unique().tolist()),
            )

            df_mostrar = df.copy()
            if filtro_estado == "Pendientes":
                df_mostrar = df_mostrar[~df_mostrar["producido_bool"]]
            elif filtro_estado == "Producidos":
                df_mostrar = df_mostrar[df_mostrar["producido_bool"]]
            if filtro_cliente != "Todos":
                df_mostrar = df_mostrar[df_mostrar["cliente_nombre"] == filtro_cliente]

            df_mostrar = df_mostrar.copy()
            df_mostrar["Producido"] = df_mostrar["producido_bool"].map({True: "✅ Sí", False: "🟡 No"})
            st.dataframe(
                df_mostrar[["Producido"] + columnas_mostrar].sort_values("fecha_pedido", ascending=False),
                use_container_width=True, hide_index=True,
            )
