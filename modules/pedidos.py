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
from utils.permisos import puede_editar_pedidos

MEDIOS_RECEPCION = ["Correo", "WhatsApp", "Mensaje de texto", "Llamada", "Otro"]


def render(db, username, rol):
    st.title("🧾 Recepción de pedidos")
    nombres_tabs = ["➕ Registrar pedido", "🟡 Pendientes de producir", "📋 Todos los pedidos"]
    if puede_editar_pedidos(rol):
        nombres_tabs.append("✏️ Editar / eliminar")
    tabs_pedidos = st.tabs(nombres_tabs)
    tab_nuevo, tab_pendientes, tab_todos = tabs_pedidos[0], tabs_pedidos[1], tabs_pedidos[2]

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
                key="ped_cliente",
            )
            medio_recepcion = c2.selectbox("Medio de recepción", MEDIOS_RECEPCION, key="ped_medio")
            ciudad = c3.text_input("Ciudad", "Quito", key="ped_ciudad")
            c4, c5 = st.columns(2)
            pedido_cliente_ref = c4.text_input("N° de pedido del cliente (si lo dieron)", "", key="ped_ref")
            fecha_pedido = c5.date_input("Fecha del pedido", value=datetime.date.today(), key="ped_fecha")

        with st.container(border=True):
            st.markdown("##### 📅 Fechas")
            fecha_entrega = st.date_input("Fecha de entrega comprometida", value=datetime.date.today(), key="ped_entrega")
            st.caption("La fecha de producción planeada la asigna el jefe de planta desde 'Todos los pedidos'.")

        # ── Líneas de productos ──────────────────────────────────────────
        st.markdown("##### 🥚 Productos del pedido")
        st.caption("Agrega una línea por cada producto que pide el cliente.")

        clave_items = f"pedido_items_{cliente_id}"
        if clave_items not in st.session_state:
            st.session_state[clave_items] = []

        # Formulario de línea
        ca, cb, cc, cd = st.columns([2, 2, 1, 1])
        tipo_sel     = ca.selectbox("Tipo de producto", ["Huevo entero pasteurizado", "Clara pasteurizada", "Clara sin pasteurizar", "Yema pasteurizada"], key="ped_tipo")
        pres_sel     = cb.selectbox(
            "Presentación", presentaciones["presentacion_id"],
            format_func=lambda x: presentaciones.set_index("presentacion_id").loc[x, "nombre"],
            key="ped_pres",
        )
        unid_sel     = cc.number_input("Unidades", min_value=0, step=1, key="ped_unid")
        kg_nominal_l = float(presentaciones.set_index("presentacion_id").loc[pres_sel, "kg_nominal"])
        kg_sel       = cd.number_input("Kg", min_value=0.0,
                                        value=round(unid_sel * kg_nominal_l, 2) if unid_sel > 0 else 0.0,
                                        step=0.5, key="ped_kg")

        if st.button("➕ Agregar producto al pedido", use_container_width=True):
            if kg_sel <= 0 and unid_sel <= 0:
                st.error("Ingresa al menos unidades o kg.")
            else:
                pres_nombre = presentaciones.set_index("presentacion_id").loc[pres_sel, "nombre"]
                st.session_state[clave_items].append({
                    "tipo_producto": tipo_sel,
                    "presentacion_id": pres_sel,
                    "presentacion_nombre": pres_nombre,
                    "unidades_solicitadas": unid_sel,
                    "cantidad_kg": kg_sel if kg_sel > 0 else round(unid_sel * kg_nominal_l, 2),
                })
                st.rerun()

        # Tabla acumulada de productos
        items_acum = st.session_state[clave_items]
        if items_acum:
            st.markdown("**Productos agregados:**")
            df_items = pd.DataFrame(items_acum)[["tipo_producto","presentacion_nombre","unidades_solicitadas","cantidad_kg"]]
            df_items.columns = ["Producto","Presentación","Unidades","Kg"]
            st.dataframe(df_items, use_container_width=True, hide_index=True)
            st.info(f"Total: **{df_items['Kg'].sum():.1f} kg** en {len(items_acum)} línea(s)")

            ce, cf = st.columns(2)
            if ce.button("🗑️ Quitar última línea"):
                st.session_state[clave_items].pop()
                st.rerun()
            if cf.button("🗑️ Limpiar todo"):
                st.session_state[clave_items] = []
                st.rerun()

        observaciones = st.text_area("Observaciones generales", "", key="pedido_obs")

        if st.button("💾 Guardar pedido completo", type="primary", use_container_width=True):
            if not items_acum:
                st.error("Agrega al menos un producto antes de guardar.")
            else:
                pedido_id = db.siguiente_id("pedidos", "PED", fecha_pedido)
                # Guardar el pedido cabecera (resumen con primer producto para compatibilidad)
                primer = items_acum[0]
                db.append_row("pedidos", {
                    "pedido_id": pedido_id,
                    "pedido_cliente_ref": pedido_cliente_ref,
                    "cliente_id": cliente_id,
                    "medio_recepcion": medio_recepcion,
                    "ciudad": ciudad,
                    "tipo_producto": ", ".join(set(i["tipo_producto"] for i in items_acum)),
                    "presentacion_id": primer["presentacion_id"],
                    "unidades_solicitadas": sum(i["unidades_solicitadas"] for i in items_acum),
                    "cantidad_kg": sum(i["cantidad_kg"] for i in items_acum),
                    "fecha_pedido": fecha_pedido.isoformat(),
                    "fecha_produccion": "",
                    "fecha_entrega": fecha_entrega.isoformat(),
                    "producido": False,
                    "usuario": username,
                    "observaciones": observaciones,
                })
                # Guardar cada línea de producto en pedidos_items
                for item in items_acum:
                    item_id = db.siguiente_id("pedidos_items", "PI", fecha_pedido)
                    db.append_row("pedidos_items", {
                        "item_id": item_id,
                        "pedido_id": pedido_id,
                        "tipo_producto": item["tipo_producto"],
                        "presentacion_id": item["presentacion_id"],
                        "cantidad_kg": item["cantidad_kg"],
                        "unidades_solicitadas": item["unidades_solicitadas"],
                        "observaciones": "",
                    })
                st.session_state[clave_items] = []
                st.success(f"✅ Pedido {pedido_id} guardado con {len(items_acum)} producto(s).")
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
        hoy_str = datetime.date.today().isoformat()
        df["atrasado"] = (~df["producido_bool"]) & (df["fecha_entrega"].astype(str) < hoy_str)

        def _estado(row):
            if row["producido_bool"]:
                return "✅ Producido"
            if row["atrasado"]:
                return "🔴 Atrasado"
            return "🟡 Pendiente"

        df["estado"] = df.apply(_estado, axis=1)
        return df

    columnas_mostrar = [
        "pedido_id", "pedido_cliente_ref", "cliente_nombre", "ciudad", "medio_recepcion",
        "tipo_producto", "presentacion_nombre", "unidades_solicitadas", "cantidad_kg",
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
                atrasados = pendientes[pendientes["atrasado"]]
                if not atrasados.empty:
                    st.error(f"⚠️ {len(atrasados)} pedido(s) con fecha de entrega ya vencida.")

                st.metric("Pedidos pendientes de producir", len(pendientes))
                st.dataframe(
                    pendientes[["estado"] + columnas_mostrar].rename(columns={"estado": "Estado"}),
                    use_container_width=True, hide_index=True,
                )

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
            hoy_d = datetime.date.today()
            c1, c2 = st.columns(2)
            filtro_estado = c1.selectbox("Estado", ["Todos", "Pendientes", "Producidos"])
            filtro_cliente = c2.selectbox(
                "Cliente", ["Todos"] + sorted(df["cliente_nombre"].dropna().unique().tolist()),
            )
            c3, c4, c5 = st.columns(3)
            filtro_tipo_fecha = c3.selectbox(
                "Filtrar por fecha", ["Sin filtro", "Fecha de pedido", "Fecha de entrega"],
                key="filtro_tipo_fecha",
            )
            desde_f = hasta_f = None
            if filtro_tipo_fecha != "Sin filtro":
                desde_f = c4.date_input("Desde", value=hoy_d - datetime.timedelta(days=30), key="filtro_desde")
                hasta_f = c5.date_input("Hasta", value=hoy_d, key="filtro_hasta")

            df_mostrar = df.copy()
            if filtro_estado == "Pendientes":
                df_mostrar = df_mostrar[~df_mostrar["producido_bool"]]
            elif filtro_estado == "Producidos":
                df_mostrar = df_mostrar[df_mostrar["producido_bool"]]
            if filtro_cliente != "Todos":
                df_mostrar = df_mostrar[df_mostrar["cliente_nombre"] == filtro_cliente]
            if filtro_tipo_fecha == "Fecha de pedido" and desde_f and hasta_f:
                df_mostrar = df_mostrar[
                    (df_mostrar["fecha_pedido"].astype(str) >= desde_f.isoformat()) &
                    (df_mostrar["fecha_pedido"].astype(str) <= hasta_f.isoformat())
                ]
            elif filtro_tipo_fecha == "Fecha de entrega" and desde_f and hasta_f:
                df_mostrar = df_mostrar[
                    (df_mostrar["fecha_entrega"].astype(str) >= desde_f.isoformat()) &
                    (df_mostrar["fecha_entrega"].astype(str) <= hasta_f.isoformat())
                ]

            df_mostrar = df_mostrar.copy()
            atrasados_total = df_mostrar[df_mostrar["atrasado"]]
            if not atrasados_total.empty:
                st.error(f"⚠️ {len(atrasados_total)} pedido(s) con fecha de entrega ya vencida sin producir.")
            st.dataframe(
                df_mostrar.rename(columns={"estado": "Estado"})[["Estado"] + columnas_mostrar].sort_values("fecha_pedido", ascending=False),
                use_container_width=True, hide_index=True,
            )

            st.divider()
            st.markdown("##### 📅 Asignar fecha de producción (Jefe de planta)")
            st.caption("Selecciona un pedido sin fecha de producción asignada y defínela para planificar el turno.")
            sin_fecha_prod = df[df["fecha_produccion"].astype(str).str.strip().isin(["", "nan", "None", "NaT"])]
            if sin_fecha_prod.empty:
                st.success("✅ Todos los pedidos tienen fecha de producción asignada.")
            else:
                pedido_fp = st.selectbox(
                    "Pedido sin fecha de producción",
                    sin_fecha_prod["pedido_id"],
                    format_func=lambda x: (
                        f"{x} — {sin_fecha_prod.set_index('pedido_id').loc[x, 'cliente_id']} "
                        f"({sin_fecha_prod.set_index('pedido_id').loc[x, 'cantidad_kg']:.0f} kg, "
                        f"entrega: {sin_fecha_prod.set_index('pedido_id').loc[x, 'fecha_entrega']})"
                    ),
                    key="asignar_fp_sel",
                )
                nueva_fp = st.date_input("Fecha de producción planeada", value=datetime.date.today(), key="asignar_fp_fecha")
                if st.button("💾 Asignar fecha de producción"):
                    db.update_row("pedidos", "pedido_id", pedido_fp, {"fecha_produccion": nueva_fp.isoformat()})
                    st.success(f"✅ Pedido {pedido_fp} — fecha de producción asignada: {nueva_fp}.")
                    st.rerun()

            if puede_editar_pedidos(rol):
                st.divider()
                st.markdown("##### ↩️ Revertir pedido marcado como producido")
                st.caption("Solo admin y gerencia pueden revertir — úsalo si marcaste un pedido como producido por error.")
                producidos = df[df["producido_bool"]]
                if producidos.empty:
                    st.info("No hay pedidos marcados como producidos.")
                else:
                    pedido_rev = st.selectbox(
                        "Pedido a revertir",
                        producidos["pedido_id"],
                        format_func=lambda x: (
                            f"{x} — {producidos.set_index('pedido_id').loc[x, 'cliente_nombre']} "
                            f"({producidos.set_index('pedido_id').loc[x, 'cantidad_kg']:.1f} kg)"
                        ),
                        key="revertir_prod_sel",
                    )
                    st.warning(f"⚠️ Al revertir, el pedido **{pedido_rev}** volverá a estado Pendiente y aparecerá en el plan de producción.")
                    if st.button("↩️ Revertir a Pendiente", type="primary"):
                        db.update_row("pedidos", "pedido_id", pedido_rev, {"producido": False})
                        st.success(f"✅ Pedido {pedido_rev} revertido a Pendiente.")
                        st.rerun()

    # ======================== EDITAR / ELIMINAR (solo admin y gerencia) ========================
    if puede_editar_pedidos(rol):
        with tabs_pedidos[3]:
            st.caption("Disponible solo para administrador y gerencia.")
            df = _enriquecer(db.get_df("pedidos"))
            if df.empty:
                st.info("No hay pedidos registrados todavía.")
            else:
                pedido_sel = st.selectbox(
                    "Pedido a editar", df["pedido_id"].sort_values(ascending=False),
                    format_func=lambda x: (
                        f"{x} — {df.set_index('pedido_id').loc[x, 'cliente_nombre']} "
                        f"({df.set_index('pedido_id').loc[x, 'cantidad_kg']:.1f} kg)"
                    ),
                    key="editar_pedido_select",
                )
                fila = df.set_index("pedido_id").loc[pedido_sel]

                with st.form(f"form_editar_pedido_{pedido_sel}"):
                    c1, c2, c3 = st.columns(3)
                    cliente_id_e = c1.selectbox(
                        "Cliente", clientes["cliente_id"],
                        index=list(clientes["cliente_id"]).index(fila["cliente_id"]) if fila["cliente_id"] in list(clientes["cliente_id"]) else 0,
                        format_func=lambda x: clientes.set_index("cliente_id").loc[x, "nombre"],
                    )
                    medio_e = c2.selectbox(
                        "Medio de recepción", MEDIOS_RECEPCION,
                        index=MEDIOS_RECEPCION.index(fila["medio_recepcion"]) if fila["medio_recepcion"] in MEDIOS_RECEPCION else 0,
                    )
                    ciudad_e = c3.text_input("Ciudad", str(fila["ciudad"]))

                    c4, c5 = st.columns(2)
                    pedido_cliente_ref_e = c4.text_input("N° de pedido del cliente", str(fila.get("pedido_cliente_ref", "")))
                    fecha_pedido_e = c5.date_input("Fecha del pedido", value=pd.to_datetime(fila["fecha_pedido"]).date())

                    opciones_producto = ["Huevo entero", "Clara", "Yema"]
                    c6, c7, c8, c9 = st.columns(4)
                    tipo_producto_e = c6.selectbox(
                        "Producto", opciones_producto,
                        index=opciones_producto.index(fila["tipo_producto"]) if fila["tipo_producto"] in opciones_producto else 0,
                    )
                    presentacion_id_e = c7.selectbox(
                        "Presentación", presentaciones["presentacion_id"],
                        index=list(presentaciones["presentacion_id"]).index(fila["presentacion_id"]) if fila["presentacion_id"] in list(presentaciones["presentacion_id"]) else 0,
                        format_func=lambda x: presentaciones.set_index("presentacion_id").loc[x, "nombre"],
                    )
                    unidades_e = c8.number_input(
                        "N° de envases", min_value=0, step=1,
                        value=int(pd.to_numeric(fila.get("unidades_solicitadas", 0), errors="coerce") or 0),
                    )
                    cantidad_kg_e = c9.number_input(
                        "Cantidad (kg)", min_value=0.0, step=0.5, value=float(fila["cantidad_kg"]),
                    )

                    c10, c11 = st.columns(2)
                    fp_raw = fila.get("fecha_produccion", "")
                    try:
                        fp_valor = pd.to_datetime(fp_raw).date() if str(fp_raw).strip() not in ("", "nan", "None", "NaT") else datetime.date.today()
                    except Exception:
                        fp_valor = datetime.date.today()
                    fecha_produccion_e = c10.date_input("Fecha de producción planeada", value=fp_valor)
                    fecha_entrega_e = c11.date_input("Fecha de entrega", value=pd.to_datetime(fila["fecha_entrega"]).date())

                    producido_e = st.checkbox("Producido", value=bool(fila["producido_bool"]))
                    observaciones_e = st.text_area("Observaciones", str(fila.get("observaciones", "")))

                    guardar = st.form_submit_button("💾 Guardar cambios", type="primary")

                if guardar:
                    db.update_row("pedidos", "pedido_id", pedido_sel, {
                        "cliente_id": cliente_id_e,
                        "medio_recepcion": medio_e,
                        "ciudad": ciudad_e,
                        "pedido_cliente_ref": pedido_cliente_ref_e,
                        "fecha_pedido": fecha_pedido_e.isoformat(),
                        "tipo_producto": tipo_producto_e,
                        "presentacion_id": presentacion_id_e,
                        "unidades_solicitadas": unidades_e,
                        "cantidad_kg": cantidad_kg_e,
                        "fecha_produccion": fecha_produccion_e.isoformat(),
                        "fecha_entrega": fecha_entrega_e.isoformat(),
                        "producido": producido_e,
                        "observaciones": observaciones_e,
                    })
                    st.success(f"Pedido {pedido_sel} actualizado.")
                    st.rerun()

                st.divider()
                st.markdown("##### 🗑️ Eliminar pedido")
                confirmar = st.checkbox(f"Confirmo que quiero eliminar el pedido {pedido_sel}", key=f"confirmar_del_pedido_{pedido_sel}")
                if st.button("🗑️ Eliminar este pedido"):
                    if not confirmar:
                        st.error("Marca la casilla de confirmación antes de eliminar.")
                    else:
                        db.delete_row("pedidos", "pedido_id", pedido_sel)
                        st.success(f"Pedido {pedido_sel} eliminado.")
                        st.rerun()
