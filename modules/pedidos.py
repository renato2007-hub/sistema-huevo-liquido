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
from utils.bitacora import log_cambio, log_cambios_multiples

MEDIOS_RECEPCION = ["Correo", "WhatsApp", "Mensaje de texto", "Llamada", "Otro"]


def render(db, username, rol):
    st.title("🧾 Recepción de pedidos")
    nombres_tabs = ["➕ Registrar pedido", "⚡ Extra a pedido existente",
                    "🟡 Pendientes de producir", "📋 Todos los pedidos"]
    if puede_editar_pedidos(rol):
        nombres_tabs.append("✏️ Editar / eliminar")
    tabs_pedidos = st.tabs(nombres_tabs)
    tab_nuevo = tabs_pedidos[0]
    tab_extra = tabs_pedidos[1]
    tab_pendientes = tabs_pedidos[2]
    tab_todos = tabs_pedidos[3]

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
        # kg se calcula automaticamente: unidades * kg_nominal de la presentacion
        kg_sel       = round(unid_sel * kg_nominal_l, 2)
        cd.metric("Kg (auto)", f"{kg_sel:.1f}", help=f"{unid_sel} × {kg_nominal_l:g} kg por unidad")

        if st.button("➕ Agregar producto al pedido", use_container_width=True):
            if unid_sel <= 0:
                st.error("Ingresa el número de unidades.")
            else:
                pres_nombre = presentaciones.set_index("presentacion_id").loc[pres_sel, "nombre"]
                st.session_state[clave_items].append({
                    "tipo_producto": tipo_sel,
                    "presentacion_id": pres_sel,
                    "presentacion_nombre": pres_nombre,
                    "unidades_solicitadas": unid_sel,
                    "cantidad_kg": kg_sel,
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

        # Vincular a orden de produccion en curso (para pedidos extra/urgentes)
        produccion_reciente = db.get_df("produccion_semielaborados")
        ordenes_recientes = []
        if not produccion_reciente.empty and "orden_produccion" in produccion_reciente.columns:
            hoy_d = datetime.date.today()
            desde_d = (hoy_d - datetime.timedelta(days=2)).isoformat()
            prod_rec = produccion_reciente[
                (produccion_reciente["fecha"].astype(str) >= desde_d)
                & (produccion_reciente["orden_produccion"].astype(str).str.strip() != "")
            ]
            ordenes_recientes = sorted(set(prod_rec["orden_produccion"].astype(str).tolist()))
        opciones_orden = ["(no vincular)"] + ordenes_recientes
        orden_vinculada = st.selectbox(
            "Vincular a orden de producción en curso (opcional)",
            opciones_orden, index=0, key="pedido_orden_vinc",
            help="Úsalo si este pedido es extra y se va a producir sumándose a una orden ya en curso. El jefe de planta verá el aviso al producir.",
        )

        if st.button("💾 Guardar pedido completo", type="primary", use_container_width=True):
            if not items_acum:
                st.error("Agrega al menos un producto antes de guardar.")
            else:
                pedido_id = db.siguiente_id("pedidos", "PED", fecha_pedido)
                # Cabecera del pedido: solo datos del cliente y del pedido en si.
                # Los productos con sus fechas van en pedidos_lineas.
                db.append_row("pedidos", {
                    "pedido_id": pedido_id,
                    "pedido_cliente_ref": pedido_cliente_ref,
                    "cliente_id": cliente_id,
                    "medio_recepcion": medio_recepcion,
                    "ciudad": ciudad,
                    "tipo_producto": ", ".join(sorted(set(i["tipo_producto"] for i in items_acum))),
                    "presentacion_id": "",
                    "unidades_solicitadas": sum(i["unidades_solicitadas"] for i in items_acum),
                    "cantidad_kg": sum(i["cantidad_kg"] for i in items_acum),
                    "fecha_pedido": fecha_pedido.isoformat(),
                    "fecha_produccion": "",
                    "fecha_entrega": fecha_entrega.isoformat(),
                    "producido": False,
                    "estado": "pendiente",
                    "urgente": False,
                    "orden_produccion_vinculada": "",
                    "usuario": username,
                    "observaciones": observaciones,
                })
                # Cada producto es una linea independiente (planificable
                # y marcable como producida por separado).
                for item in items_acum:
                    linea_id = db.siguiente_id("pedidos_lineas", "PL", fecha_pedido)
                    db.append_row("pedidos_lineas", {
                        "linea_id": linea_id,
                        "pedido_id": pedido_id,
                        "tipo_producto": item["tipo_producto"],
                        "presentacion_id": item["presentacion_id"],
                        "unidades": item["unidades_solicitadas"],
                        "cantidad_kg": item["cantidad_kg"],
                        "fecha_produccion": "",
                        "fecha_entrega": fecha_entrega.isoformat(),
                        "producido": False,
                        "orden_produccion_vinculada": orden_vinculada if orden_vinculada != "(no vincular)" else "",
                        "observaciones": "",
                    })
                st.session_state[clave_items] = []
                st.success(f"✅ Pedido {pedido_id} guardado con {len(items_acum)} línea(s) — cada una se planifica por separado.")
                st.rerun()

    # ======================== EXTRA A PEDIDO EXISTENTE ========================
    def _refrescar_resumen_productos(db, pedido_id, nuevo_tipo):
        """Devuelve el string 'tipo_producto' de la cabecera con todos los
        tipos de producto que ahora tiene el pedido (incluido el nuevo)."""
        lineas_actuales = db.get_df("pedidos_lineas")
        if lineas_actuales.empty:
            return nuevo_tipo
        tipos_actuales = set(
            lineas_actuales[lineas_actuales["pedido_id"] == pedido_id]["tipo_producto"].astype(str).tolist()
        )
        tipos_actuales.add(nuevo_tipo)
        return ", ".join(sorted(tipos_actuales))

    with tab_extra:
        st.caption(
            "Cuando un cliente llama pidiendo agregar una cantidad extra a un "
            "pedido ya registrado, úsalo aquí: se crea una **línea nueva** en "
            "el pedido existente que se planifica por separado. Ideal para "
            "pedidos de última hora que se suman a una orden en curso."
        )
        pedidos_cab_extra = db.get_df("pedidos")
        if pedidos_cab_extra.empty:
            st.info("No hay pedidos existentes. Crea uno desde 'Registrar pedido' primero.")
        else:
            # Filtrar pedidos activos (no cancelados)
            if "estado" in pedidos_cab_extra.columns:
                estado_norm = pedidos_cab_extra["estado"].fillna("pendiente").astype(str).str.strip().str.lower()
                pedidos_activos_extra = pedidos_cab_extra[estado_norm != "cancelado"].copy()
            else:
                pedidos_activos_extra = pedidos_cab_extra.copy()
            if pedidos_activos_extra.empty:
                st.info("No hay pedidos activos.")
            elif clientes.empty or presentaciones.empty:
                st.warning("Necesitas clientes y presentaciones configurados en Catálogos.")
            else:
                # Traer nombres de cliente para el selector
                mapa_cli_extra = dict(zip(clientes["cliente_id"], clientes["nombre"])) if not clientes.empty else {}
                pedidos_activos_extra["cli_nombre"] = pedidos_activos_extra["cliente_id"].map(mapa_cli_extra).fillna(pedidos_activos_extra["cliente_id"])
                pedidos_activos_extra = pedidos_activos_extra.sort_values("fecha_pedido", ascending=False)

                pedido_extra_id = st.selectbox(
                    "Pedido a extender",
                    pedidos_activos_extra["pedido_id"],
                    format_func=lambda x: (
                        f"{x} — {pedidos_activos_extra.set_index('pedido_id').loc[x, 'cli_nombre']} "
                        f"(entrega: {pedidos_activos_extra.set_index('pedido_id').loc[x, 'fecha_entrega']})"
                    ),
                    key="extra_ped_sel",
                )
                fila_pedido = pedidos_activos_extra.set_index("pedido_id").loc[pedido_extra_id]

                # Mostrar resumen breve del pedido
                st.info(
                    f"📋 **{fila_pedido['cli_nombre']}** — Fecha pedido: {fila_pedido['fecha_pedido']} — "
                    f"Entrega original: **{fila_pedido['fecha_entrega']}**"
                )

                # Formulario de la línea extra
                c1, c2 = st.columns(2)
                tipo_extra = c1.selectbox(
                    "Tipo de producto",
                    ["Huevo entero pasteurizado", "Clara pasteurizada", "Clara sin pasteurizar", "Yema pasteurizada"],
                    key="extra_tipo",
                )
                pres_extra = c2.selectbox(
                    "Presentación", presentaciones["presentacion_id"],
                    format_func=lambda x: presentaciones.set_index("presentacion_id").loc[x, "nombre"],
                    key="extra_pres",
                )
                c3, c4 = st.columns(2)
                unid_extra = c3.number_input("Unidades", min_value=0, step=1, key="extra_unid")
                kg_nom_extra = float(presentaciones.set_index("presentacion_id").loc[pres_extra, "kg_nominal"])
                kg_extra = round(unid_extra * kg_nom_extra, 2)
                c4.metric("Kg (auto)", f"{kg_extra:.1f}", help=f"{unid_extra} × {kg_nom_extra:g} kg por unidad")

                c5, c6 = st.columns(2)
                try:
                    fecha_entrega_default = datetime.date.fromisoformat(str(fila_pedido["fecha_entrega"]))
                except (ValueError, TypeError):
                    fecha_entrega_default = datetime.date.today()
                fecha_entrega_extra = c5.date_input(
                    "Fecha de entrega", value=fecha_entrega_default, key=f"extra_fe_{pedido_extra_id}",
                )
                fecha_produccion_extra = c6.date_input(
                    "Fecha de producción (opcional)", value=datetime.date.today(), key="extra_fp",
                )
                sin_fp = st.checkbox("Sin asignar fecha de producción todavía", value=False, key="extra_sinfp")

                # Vincular a orden en curso
                produccion_reciente_e = db.get_df("produccion_semielaborados")
                ordenes_recientes_e = []
                if not produccion_reciente_e.empty and "orden_produccion" in produccion_reciente_e.columns:
                    hoy_d = datetime.date.today()
                    desde_d = (hoy_d - datetime.timedelta(days=2)).isoformat()
                    prod_rec_e = produccion_reciente_e[
                        (produccion_reciente_e["fecha"].astype(str) >= desde_d)
                        & (produccion_reciente_e["orden_produccion"].astype(str).str.strip() != "")
                    ]
                    ordenes_recientes_e = sorted(set(prod_rec_e["orden_produccion"].astype(str).tolist()))
                opciones_orden_e = ["(no vincular)"] + ordenes_recientes_e
                orden_vinc_extra = st.selectbox(
                    "Vincular a orden de producción en curso (opcional)",
                    opciones_orden_e, index=0, key="extra_orden_vinc",
                    help="Si esta línea extra va a producirse sumándose a una orden ya en curso, aparecerá en el aviso del jefe de planta.",
                )
                obs_extra = st.text_input("Observaciones (opcional)", "", key="extra_obs")
                marcar_urg = st.checkbox("⚡ Marcar el pedido como URGENTE al agregar esta línea", value=False, key="extra_urg")

                if st.button("➕ Agregar línea extra al pedido", type="primary", use_container_width=True):
                    if unid_extra <= 0:
                        st.error("Ingresa el número de unidades.")
                    else:
                        linea_id_new = db.siguiente_id("pedidos_lineas", "PL", datetime.date.today())
                        db.append_row("pedidos_lineas", {
                            "linea_id": linea_id_new,
                            "pedido_id": pedido_extra_id,
                            "tipo_producto": tipo_extra,
                            "presentacion_id": pres_extra,
                            "unidades": unid_extra,
                            "cantidad_kg": kg_extra,
                            "fecha_produccion": "" if sin_fp else fecha_produccion_extra.isoformat(),
                            "fecha_entrega": fecha_entrega_extra.isoformat(),
                            "producido": False,
                            "orden_produccion_vinculada": orden_vinc_extra if orden_vinc_extra != "(no vincular)" else "",
                            "observaciones": obs_extra,
                        })
                        # Actualizar cabecera: sumar kg y unidades, marcar urgente si corresponde
                        kg_prev = float(pd.to_numeric(fila_pedido.get("cantidad_kg", 0), errors="coerce") or 0)
                        unid_prev = float(pd.to_numeric(fila_pedido.get("unidades_solicitadas", 0), errors="coerce") or 0)
                        cambios_cab = {
                            "cantidad_kg": kg_prev + kg_extra,
                            "unidades_solicitadas": unid_prev + unid_extra,
                            # Refrescar el resumen textual de productos incluidos
                            "tipo_producto": _refrescar_resumen_productos(db, pedido_extra_id, tipo_extra),
                        }
                        if marcar_urg:
                            cambios_cab["urgente"] = True
                        # Anotar en observaciones que hubo extra
                        obs_cab_prev = str(fila_pedido.get("observaciones", "") or "")
                        marca_extra = (
                            f"EXTRA {datetime.date.today().isoformat()} por {username}: "
                            f"+{unid_extra} un. de {tipo_extra} ({kg_extra:.1f} kg)"
                        )
                        cambios_cab["observaciones"] = (obs_cab_prev + " | " + marca_extra).strip(" |")
                        db.update_row("pedidos", "pedido_id", pedido_extra_id, cambios_cab)
                        st.success(
                            f"✅ Línea extra agregada al pedido {pedido_extra_id}: "
                            f"{unid_extra} un. de {tipo_extra} ({kg_extra:.1f} kg)."
                            + (" ⚡ Pedido marcado urgente." if marcar_urg else "")
                        )
                        st.rerun()


    # ======================== helper: lineas con todos los datos ========================
    def _cargar_lineas():
        """Carga pedidos_lineas y hace JOIN con pedidos (cabecera), clientes
        y presentaciones. Devuelve un DataFrame de LINEAS enriquecidas."""
        lineas = db.get_df("pedidos_lineas")
        pedidos_cab = db.get_df("pedidos")
        if lineas.empty:
            return pd.DataFrame()
        lineas = lineas.copy()
        lineas["cantidad_kg"] = pd.to_numeric(lineas["cantidad_kg"], errors="coerce").fillna(0)
        lineas["unidades"] = pd.to_numeric(lineas["unidades"], errors="coerce").fillna(0)
        lineas["producido_bool"] = lineas["producido"].astype(str).str.upper().isin(["TRUE","1","SI","SÍ"])
        # JOIN con cabecera (para traer cliente, estado del pedido, urgente, ref cliente)
        cols_cab = ["pedido_id", "cliente_id", "fecha_pedido", "pedido_cliente_ref",
                    "medio_recepcion", "ciudad", "estado", "urgente", "observaciones"]
        cols_cab = [c for c in cols_cab if c in pedidos_cab.columns]
        pedidos_cab_c = pedidos_cab[cols_cab].copy() if not pedidos_cab.empty else pd.DataFrame(columns=cols_cab)
        if not pedidos_cab_c.empty:
            pedidos_cab_c = pedidos_cab_c.rename(columns={"observaciones": "obs_pedido"})
            lineas = lineas.merge(pedidos_cab_c, on="pedido_id", how="left")
        # JOIN con clientes
        if not clientes.empty:
            lineas = lineas.merge(
                clientes[["cliente_id", "nombre"]].rename(columns={"nombre": "cliente_nombre"}),
                on="cliente_id", how="left",
            )
            lineas["cliente_nombre"] = lineas["cliente_nombre"].fillna(lineas["cliente_id"])
        else:
            lineas["cliente_nombre"] = lineas["cliente_id"]
        # JOIN con presentaciones
        if not presentaciones.empty:
            lineas = lineas.merge(
                presentaciones[["presentacion_id", "nombre"]].rename(columns={"nombre": "presentacion_nombre"}),
                on="presentacion_id", how="left",
            )
            lineas["presentacion_nombre"] = lineas["presentacion_nombre"].fillna(lineas["presentacion_id"])
        else:
            lineas["presentacion_nombre"] = lineas["presentacion_id"]
        # Estado del pedido (heredado en la linea)
        if "estado" in lineas.columns:
            lineas["estado_pedido"] = (
                lineas["estado"].fillna("pendiente").astype(str).str.strip().str.lower()
                .replace({"": "pendiente", "nan": "pendiente", "none": "pendiente"})
            )
        else:
            lineas["estado_pedido"] = "pendiente"
        if "urgente" in lineas.columns:
            lineas["urgente_bool"] = lineas["urgente"].astype(str).str.upper().isin(["TRUE","1","SI","SÍ"])
        else:
            lineas["urgente_bool"] = False
        # Atrasada: la linea no producida cuya fecha de entrega ya paso
        hoy_str = datetime.date.today().isoformat()
        lineas["atrasada"] = (
            (~lineas["producido_bool"])
            & (lineas["estado_pedido"] != "cancelado")
            & (lineas["fecha_entrega"].astype(str) < hoy_str)
        )

        def _estado_linea(row):
            if row["estado_pedido"] == "cancelado":
                return "❌ Cancelado"
            if row["producido_bool"]:
                return "✅ Producido"
            if row["atrasada"]:
                base = "🔴 Atrasado"
            else:
                base = "🟡 Pendiente"
            if row["urgente_bool"]:
                return "⚡ URGENTE " + base
            return base

        lineas["estado_display"] = lineas.apply(_estado_linea, axis=1)
        # Etiqueta descriptiva para selectboxes: "PED-... — CLIENTE — Producto (X kg, entrega: Y)"
        def _etq(row):
            return (
                f"{row['pedido_id']} — {row['cliente_nombre']} — "
                f"{row['tipo_producto']} ({row['cantidad_kg']:.1f} kg, entrega: {row['fecha_entrega']})"
            )
        lineas["etiqueta"] = lineas.apply(_etq, axis=1)
        return lineas

    # ======================== PENDIENTES DE PRODUCIR ========================
    with tab_pendientes:
        lineas = _cargar_lineas()
        if lineas.empty:
            st.info("No hay pedidos registrados.")
        else:
            pendientes = lineas[
                (~lineas["producido_bool"]) & (lineas["estado_pedido"] != "cancelado")
            ].sort_values(["urgente_bool", "fecha_entrega"], ascending=[False, True])
            if pendientes.empty:
                st.success("🎉 No hay pedidos pendientes de producir.")
            else:
                pendientes = pendientes.copy()
                sin_fp_bool = pendientes["fecha_produccion"].astype(str).str.strip().isin(["", "nan", "None"])
                pendientes["fp_estado"] = pendientes["fecha_produccion"].where(~sin_fp_bool, "🔴 Sin asignar")

                col_m1, col_m2 = st.columns(2)
                col_m1.metric("Kg pendientes de producir", f"{pendientes['cantidad_kg'].sum():,.1f}")
                col_m2.metric("Sin fecha de producción asignada", f"{int(sin_fp_bool.sum())} de {len(pendientes)}")
                if sin_fp_bool.any():
                    st.warning(
                        f"⚠️ {int(sin_fp_bool.sum())} pedido(s) pendiente(s) todavía no tienen "
                        f"fecha de producción — asígnala más abajo para que aparezcan en el "
                        f"calendario de producción."
                    )
                else:
                    st.success("✅ Todos los pedidos pendientes ya tienen fecha de producción asignada.")

                cols_pend = ["pedido_id", "cliente_nombre", "tipo_producto",
                             "presentacion_nombre", "unidades", "cantidad_kg",
                             "fecha_entrega", "fp_estado", "estado_display"]
                cols_pend = [c for c in cols_pend if c in pendientes.columns]
                st.dataframe(
                    pendientes[cols_pend].rename(columns={
                        "pedido_id": "Pedido", "cliente_nombre": "Cliente",
                        "tipo_producto": "Producto", "presentacion_nombre": "Presentación",
                        "unidades": "Unidades", "cantidad_kg": "Kg",
                        "fecha_entrega": "Entrega", "fp_estado": "F. producción",
                        "estado_display": "Estado",
                    }),
                    use_container_width=True, hide_index=True,
                )

                # -- Asignar fecha de produccion a una linea --
                st.divider()
                st.markdown("**📅 Asignar fecha de producción**")
                lineas_sin_fp = pendientes[
                    pendientes["fecha_produccion"].astype(str).str.strip().isin(["", "nan", "None"])
                ]
                if lineas_sin_fp.empty:
                    st.caption("No hay líneas sin fecha de producción asignada.")
                else:
                    linea_fp = st.selectbox(
                        "Línea",
                        lineas_sin_fp["linea_id"],
                        format_func=lambda x: lineas_sin_fp.set_index("linea_id").loc[x, "etiqueta"],
                        key="lin_fp_sel",
                    )
                    nueva_fp = st.date_input(
                        "Fecha de producción", value=datetime.date.today(), key="lin_fp_fecha",
                    )
                    if st.button("💾 Asignar fecha", key="btn_lin_fp"):
                        db.update_row("pedidos_lineas", "linea_id", linea_fp,
                                      {"fecha_produccion": nueva_fp.isoformat()})
                        st.success(f"✅ Fecha de producción asignada a la línea {linea_fp}.")
                        st.rerun()

                # -- Revertir fecha de produccion ya asignada (por si fue un error) --
                st.divider()
                st.markdown("**↩️ Revertir fecha de producción**")
                lineas_con_fp = pendientes[~sin_fp_bool]
                if lineas_con_fp.empty:
                    st.caption("No hay líneas con fecha de producción asignada todavía.")
                else:
                    linea_rev = st.selectbox(
                        "Línea",
                        lineas_con_fp["linea_id"],
                        format_func=lambda x: (
                            f"{lineas_con_fp.set_index('linea_id').loc[x, 'etiqueta']} "
                            f"— asignada: {lineas_con_fp.set_index('linea_id').loc[x, 'fecha_produccion']}"
                        ),
                        key="lin_rev_sel",
                    )
                    if st.button("↩️ Quitar fecha de producción", key="btn_lin_rev"):
                        if db.update_row("pedidos_lineas", "linea_id", linea_rev, {"fecha_produccion": ""}):
                            st.success(f"✅ Fecha de producción quitada de la línea {linea_rev} — ya puedes reasignarla.")
                            st.rerun()
                        else:
                            st.error(
                                f"⚠️ No se pudo encontrar la línea '{linea_rev}' para actualizarla -- "
                                f"no se aplicó ningún cambio."
                            )

    # ======================== TODOS LOS PEDIDOS ========================
    with tab_todos:
        lineas = _cargar_lineas()
        if lineas.empty:
            st.info("No hay pedidos registrados.")
        else:
            # Filtros
            c1, c2, c3 = st.columns(3)
            filtro_estado = c1.selectbox("Estado", ["Todos", "Pendientes", "Producidos", "Cancelados"], key="lin_estado")
            filtro_producto = c2.selectbox(
                "Producto",
                ["Todos"] + sorted(lineas["tipo_producto"].dropna().unique().tolist()),
                key="lin_prod",
            )
            filtro_cliente = c3.selectbox(
                "Cliente",
                ["Todos"] + sorted(lineas["cliente_nombre"].dropna().unique().tolist()),
                key="lin_cli",
            )
            c4, c5 = st.columns(2)
            desde_ped = c4.date_input(
                "Desde (fecha del pedido)",
                value=datetime.date.today() - datetime.timedelta(days=30), key="lin_desde",
            )
            hasta_ped = c5.date_input("Hasta (fecha del pedido)", value=datetime.date.today(), key="lin_hasta")

            df_mostrar = lineas.copy()
            if filtro_estado != "Cancelados":
                df_mostrar = df_mostrar[df_mostrar["estado_pedido"] != "cancelado"]
            if filtro_estado == "Pendientes":
                df_mostrar = df_mostrar[~df_mostrar["producido_bool"]]
            elif filtro_estado == "Producidos":
                df_mostrar = df_mostrar[df_mostrar["producido_bool"]]
            elif filtro_estado == "Cancelados":
                df_mostrar = df_mostrar[df_mostrar["estado_pedido"] == "cancelado"]
            if filtro_producto != "Todos":
                df_mostrar = df_mostrar[df_mostrar["tipo_producto"] == filtro_producto]
            if filtro_cliente != "Todos":
                df_mostrar = df_mostrar[df_mostrar["cliente_nombre"] == filtro_cliente]
            df_mostrar = df_mostrar[
                (df_mostrar["fecha_pedido"].astype(str) >= desde_ped.isoformat())
                & (df_mostrar["fecha_pedido"].astype(str) <= hasta_ped.isoformat())
            ]

            cols_show = ["pedido_id", "cliente_nombre", "tipo_producto",
                         "presentacion_nombre", "unidades", "cantidad_kg",
                         "fecha_pedido", "fecha_entrega", "fecha_produccion",
                         "estado_display"]
            cols_show = [c for c in cols_show if c in df_mostrar.columns]
            st.dataframe(
                df_mostrar[cols_show].rename(columns={
                    "pedido_id": "Pedido", "cliente_nombre": "Cliente",
                    "tipo_producto": "Producto", "presentacion_nombre": "Presentación",
                    "unidades": "Unidades", "cantidad_kg": "Kg",
                    "fecha_pedido": "F. pedido", "fecha_entrega": "F. entrega",
                    "fecha_produccion": "F. producción", "estado_display": "Estado",
                }).sort_values("F. pedido", ascending=False),
                use_container_width=True, hide_index=True,
            )

            # ---------- Acciones por linea ----------
            st.divider()
            st.markdown("##### 🎯 Acciones por línea")

            no_can = lineas[lineas["estado_pedido"] != "cancelado"]
            if no_can.empty:
                st.info("No hay líneas activas.")
            else:
                # -- Marcar linea como producida --
                st.markdown("**✅ Marcar línea como producida**")
                lineas_no_prod = no_can[~no_can["producido_bool"]]
                if lineas_no_prod.empty:
                    st.caption("Todas las líneas ya están producidas.")
                else:
                    linea_pro = st.selectbox(
                        "Línea",
                        lineas_no_prod["linea_id"],
                        format_func=lambda x: lineas_no_prod.set_index("linea_id").loc[x, "etiqueta"],
                        key="lin_pro_sel",
                    )
                    if st.button("✅ Marcar producida", key="btn_lin_pro"):
                        db.update_row("pedidos_lineas", "linea_id", linea_pro, {"producido": True})
                        # Si TODAS las lineas del pedido estan producidas, marcar el pedido tambien
                        pedido_asoc = lineas_no_prod.set_index("linea_id").loc[linea_pro, "pedido_id"]
                        todas_lineas_del_pedido = lineas[lineas["pedido_id"] == pedido_asoc]
                        no_producidas_restantes = todas_lineas_del_pedido[
                            (todas_lineas_del_pedido["linea_id"] != linea_pro)
                            & (~todas_lineas_del_pedido["producido_bool"])
                        ]
                        if no_producidas_restantes.empty:
                            db.update_row("pedidos", "pedido_id", pedido_asoc, {"producido": True})
                            st.success(f"✅ Línea marcada y pedido {pedido_asoc} completado.")
                        else:
                            st.success(
                                f"✅ Línea marcada — quedan {len(no_producidas_restantes)} "
                                f"línea(s) del pedido {pedido_asoc} por producir."
                            )
                        st.rerun()

                # -- Revertir fecha de produccion / marca de producido (por si fue un error) --
                # Muestra la linea si tiene fecha asignada O si quedo marcada
                # producida (aunque ya no tenga fecha -- puede pasar si se
                # revirtio la fecha antes de que existiera este arreglo, y
                # el "producido" se quedo a medias sin desmarcar).
                st.markdown("**↩️ Revertir fecha de producción / marca de producido**")
                tiene_fp_todos = ~no_can["fecha_produccion"].astype(str).str.strip().isin(["", "nan", "None"])
                lineas_con_fp_todos = no_can[tiene_fp_todos | no_can["producido_bool"]]
                if lineas_con_fp_todos.empty:
                    st.caption("No hay líneas con fecha de producción o marca de producido para revertir.")
                else:
                    linea_rev_todos = st.selectbox(
                        "Línea",
                        lineas_con_fp_todos["linea_id"],
                        format_func=lambda x: (
                            f"{lineas_con_fp_todos.set_index('linea_id').loc[x, 'etiqueta']} "
                            f"— fecha: {lineas_con_fp_todos.set_index('linea_id').loc[x, 'fecha_produccion'] or 'sin asignar'}"
                            + (" (✅ producido)" if lineas_con_fp_todos.set_index('linea_id').loc[x, 'producido_bool'] else "")
                        ),
                        key="lin_rev_todos_sel",
                    )
                    if st.button("↩️ Revertir esta línea", key="btn_lin_rev_todos"):
                        fila_rev = lineas_con_fp_todos.set_index("linea_id").loc[linea_rev_todos]
                        estaba_producida = bool(fila_rev["producido_bool"])
                        tenia_fecha = str(fila_rev["fecha_produccion"] or "").strip() not in ("", "nan", "None")
                        cambios_rev = {"fecha_produccion": ""}
                        if estaba_producida:
                            # Tambien se desmarca como producida -- si no, la
                            # linea queda sin fecha pero invisible en
                            # "Pendientes de producir" (que excluye producidas).
                            cambios_rev["producido"] = False
                        actualizado_rev = db.update_row(
                            "pedidos_lineas", "linea_id", linea_rev_todos, cambios_rev
                        )
                        if not actualizado_rev:
                            st.error(
                                f"⚠️ No se pudo encontrar la línea '{linea_rev_todos}' para actualizarla "
                                f"-- no se aplicó ningún cambio. Puede haber un ID duplicado o inconsistente "
                                f"en pedidos_lineas; revísalo antes de seguir."
                            )
                        else:
                            if estaba_producida:
                                # Si el pedido completo se habia marcado producido
                                # por tener todas sus lineas listas, tambien se
                                # revierte -- ya no es cierto con esta linea de vuelta.
                                pedido_asoc_rev = fila_rev["pedido_id"]
                                db.update_row("pedidos", "pedido_id", pedido_asoc_rev, {"producido": False})
                            partes_msg = []
                            if tenia_fecha:
                                partes_msg.append("se quitó la fecha de producción")
                            if estaba_producida:
                                partes_msg.append("se desmarcó como producida (y el pedido, si estaba completo)")
                            st.success(
                                f"✅ Línea {linea_rev_todos}: " + " y ".join(partes_msg) + " — "
                                f"ya puedes reasignarla desde Pendientes de producir."
                            )
                            st.rerun()

            # -- Acciones a nivel PEDIDO: urgente + cancelar --
            st.divider()
            st.markdown("##### ⚡ Acciones a nivel pedido (afecta todas sus líneas)")
            pedidos_activos = lineas[lineas["estado_pedido"] != "cancelado"].drop_duplicates("pedido_id")
            if pedidos_activos.empty:
                st.info("No hay pedidos activos.")
            else:
                col_u, col_c = st.columns(2)

                with col_u:
                    st.markdown("**⚡ Marcar / desmarcar pedido urgente**")
                    pedido_urg = st.selectbox(
                        "Pedido",
                        pedidos_activos["pedido_id"].unique(),
                        format_func=lambda x: (
                            f"{'⚡ ' if bool(pedidos_activos.set_index('pedido_id').loc[x, 'urgente_bool']) else ''}"
                            f"{x} — {pedidos_activos.set_index('pedido_id').loc[x, 'cliente_nombre']}"
                        ),
                        key="urg_ped_sel",
                    )
                    ya_urg = bool(pedidos_activos.set_index("pedido_id").loc[pedido_urg, "urgente_bool"])
                    if ya_urg:
                        if st.button("⚪ Desmarcar urgente", key="btn_desurg_ped"):
                            db.update_row("pedidos", "pedido_id", pedido_urg, {"urgente": False})
                            st.success(f"{pedido_urg} ya no es urgente.")
                            st.rerun()
                    else:
                        if st.button("⚡ Marcar URGENTE", type="primary", key="btn_urg_ped"):
                            db.update_row("pedidos", "pedido_id", pedido_urg, {"urgente": True})
                            st.success(f"⚡ {pedido_urg} marcado urgente.")
                            st.rerun()

                with col_c:
                    st.markdown("**❌ Cancelar pedido**")
                    pedido_can = st.selectbox(
                        "Pedido",
                        pedidos_activos["pedido_id"].unique(),
                        format_func=lambda x: (
                            f"{x} — {pedidos_activos.set_index('pedido_id').loc[x, 'cliente_nombre']}"
                        ),
                        key="can_ped_sel",
                    )
                    motivo_can = st.text_input("Motivo (obligatorio)", "", key="can_motivo")
                    if st.button("❌ Cancelar", key="btn_can_ped"):
                        if not motivo_can.strip():
                            st.error("Escribe el motivo.")
                        else:
                            fila = db.get_df("pedidos")
                            fila = fila[fila["pedido_id"] == pedido_can]
                            obs_prev = str(fila.iloc[0].get("observaciones", "") or "") if not fila.empty else ""
                            marca = (
                                f"CANCELADO {datetime.date.today().isoformat()} por "
                                f"{username}. Motivo: {motivo_can.strip()}"
                            )
                            db.update_row("pedidos", "pedido_id", pedido_can, {
                                "estado": "cancelado",
                                "observaciones": (obs_prev + " | " + marca).strip(" |"),
                            })
                            log_cambio(
                                db, username,
                                modulo="Recepcion de pedidos", tabla="pedidos",
                                id_registro=pedido_can, accion="cancelacion",
                                motivo=motivo_can.strip(),
                            )
                            st.success(f"❌ Pedido {pedido_can} cancelado (todas sus líneas).")
                            st.rerun()

    # ======================== EDITAR / ELIMINAR (solo admin y gerencia) ========================
    if puede_editar_pedidos(rol):
        with tabs_pedidos[4]:
            st.caption("Solo administrador y gerencia. Puedes editar campos de una línea o eliminar el pedido completo.")

            lineas = _cargar_lineas()
            if lineas.empty:
                st.info("No hay pedidos registrados.")
            else:
                # ---- Editar linea ----
                st.markdown("##### ✏️ Editar línea de pedido")
                linea_sel = st.selectbox(
                    "Línea", lineas["linea_id"],
                    format_func=lambda x: lineas.set_index("linea_id").loc[x, "etiqueta"],
                    key="ed_lin_sel",
                )
                fila_l = lineas.set_index("linea_id").loc[linea_sel]
                with st.form(f"form_ed_lin_{linea_sel}"):
                    c1, c2 = st.columns(2)
                    tipo_e = c1.selectbox(
                        "Producto",
                        ["Huevo entero pasteurizado", "Clara pasteurizada", "Clara sin pasteurizar", "Yema pasteurizada"],
                        index=(["Huevo entero pasteurizado", "Clara pasteurizada", "Clara sin pasteurizar", "Yema pasteurizada"].index(fila_l["tipo_producto"]) if fila_l["tipo_producto"] in ["Huevo entero pasteurizado", "Clara pasteurizada", "Clara sin pasteurizar", "Yema pasteurizada"] else 0),
                    )
                    pres_ids_e = presentaciones["presentacion_id"].tolist() if not presentaciones.empty else [""]
                    try:
                        idx_pres = pres_ids_e.index(fila_l["presentacion_id"])
                    except (ValueError, KeyError):
                        idx_pres = 0
                    pres_e = c2.selectbox(
                        "Presentación", pres_ids_e,
                        format_func=lambda x: presentaciones.set_index("presentacion_id").loc[x, "nombre"] if not presentaciones.empty and x in presentaciones["presentacion_id"].values else x,
                        index=idx_pres,
                    )
                    c3, c4 = st.columns(2)
                    unid_e = c3.number_input("Unidades", min_value=0.0, step=1.0, value=float(fila_l["unidades"]))
                    kg_e = c4.number_input("Cantidad (kg)", min_value=0.0, step=0.5, value=float(fila_l["cantidad_kg"]))
                    c5, c6 = st.columns(2)
                    try:
                        fe_val = datetime.date.fromisoformat(str(fila_l["fecha_entrega"]))
                    except (ValueError, TypeError):
                        fe_val = datetime.date.today()
                    fe_e = c5.date_input("Fecha de entrega", value=fe_val)
                    fp_str = str(fila_l.get("fecha_produccion", "") or "").strip()
                    try:
                        fp_val = datetime.date.fromisoformat(fp_str) if fp_str else None
                    except ValueError:
                        fp_val = None
                    fp_e = c6.date_input("Fecha de producción (opcional)", value=fp_val if fp_val else datetime.date.today())
                    fp_sin_asignar = st.checkbox("Dejar fecha de producción sin asignar", value=(fp_val is None))
                    prod_e = st.checkbox("¿Producida?", value=bool(fila_l["producido_bool"]))
                    obs_e = st.text_input("Observaciones de la línea", value=str(fila_l.get("observaciones", "") or ""))
                    guardar_l = st.form_submit_button("💾 Guardar cambios en la línea", type="primary")
                    if guardar_l:
                        cambios = {
                            "tipo_producto": tipo_e,
                            "presentacion_id": pres_e,
                            "unidades": unid_e,
                            "cantidad_kg": kg_e,
                            "fecha_entrega": fe_e.isoformat(),
                            "fecha_produccion": "" if fp_sin_asignar else fp_e.isoformat(),
                            "producido": prod_e,
                            "observaciones": obs_e,
                        }
                        db.update_row("pedidos_lineas", "linea_id", linea_sel, cambios)
                        log_cambios_multiples(
                            db, username,
                            modulo="Recepcion de pedidos", tabla="pedidos_lineas",
                            id_registro=linea_sel,
                            cambios={
                                "tipo_producto": (fila_l["tipo_producto"], tipo_e),
                                "presentacion_id": (fila_l["presentacion_id"], pres_e),
                                "unidades": (float(fila_l["unidades"]), unid_e),
                                "cantidad_kg": (float(fila_l["cantidad_kg"]), kg_e),
                                "fecha_entrega": (fila_l["fecha_entrega"], fe_e.isoformat()),
                                "fecha_produccion": (fp_str, "" if fp_sin_asignar else fp_e.isoformat()),
                                "producido": (bool(fila_l["producido_bool"]), prod_e),
                            },
                            motivo="Edicion de linea desde formulario",
                        )
                        st.success(f"Línea {linea_sel} actualizada.")
                        st.rerun()

                st.divider()
                st.markdown("##### 🗑️ Eliminar pedido completo (cabecera + todas sus líneas)")
                pedido_del = st.selectbox(
                    "Pedido a eliminar",
                    lineas["pedido_id"].drop_duplicates(),
                    format_func=lambda x: (
                        f"{x} — {lineas[lineas['pedido_id']==x]['cliente_nombre'].iloc[0]} — "
                        f"{len(lineas[lineas['pedido_id']==x])} línea(s)"
                    ),
                    key="del_ped_sel",
                )
                confirm = st.checkbox(f"Confirmo que quiero eliminar el pedido {pedido_del} y todas sus líneas", key=f"conf_del_{pedido_del}")
                if st.button("🗑️ Eliminar pedido"):
                    if not confirm:
                        st.error("Marca la casilla de confirmación.")
                    else:
                        # Eliminar todas las lineas
                        for lid in lineas[lineas["pedido_id"] == pedido_del]["linea_id"]:
                            db.delete_row("pedidos_lineas", "linea_id", lid)
                        db.delete_row("pedidos", "pedido_id", pedido_del)
                        log_cambio(
                            db, username,
                            modulo="Recepcion de pedidos", tabla="pedidos",
                            id_registro=pedido_del, accion="eliminacion",
                            motivo=f"Pedido y sus lineas eliminados",
                        )
                        st.success(f"Pedido {pedido_del} y todas sus líneas eliminados.")
                        st.rerun()
