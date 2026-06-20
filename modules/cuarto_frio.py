"""
Cuarto frio: ingreso del producto terminado que sale de pasteurizacion,
despacho a clientes (organizado por vehiculo de reparto, ya que un mismo
viaje normalmente lleva varias presentaciones para varios clientes), control
de inventario, y vista de que se cargo en cada vehiculo.
"""
import datetime
import streamlit as st
import pandas as pd


def render(db, username):
    st.title("Cuarto frío")
    tab_ingreso, tab_despacho, tab_inventario, tab_vehiculos = st.tabs(
        ["Ingreso desde envasado", "Despacho a cliente", "Inventario actual", "🚚 Cargas por vehículo"]
    )

    pasteurizado = db.get_df("pasteurizacion_envasado")
    clientes = db.get_df("clientes")
    presentaciones = db.get_df("presentaciones")
    vehiculos = db.get_df("vehiculos")

    with tab_ingreso:
        if pasteurizado.empty:
            st.info("No hay lotes de producto terminado todavía.")
        else:
            pasteurizado["unidades_saldo"] = pd.to_numeric(
                pasteurizado["unidades_saldo"], errors="coerce"
            ).fillna(0)
            disponibles = pasteurizado[pasteurizado["unidades_saldo"] > 0].copy()
            if disponibles.empty:
                st.info("No hay producto terminado pendiente de ingresar a cuarto frío.")
            else:
                if not presentaciones.empty:
                    disponibles = disponibles.merge(
                        presentaciones[["presentacion_id", "nombre"]].rename(
                            columns={"nombre": "presentacion_nombre"}
                        ),
                        on="presentacion_id", how="left",
                    )
                else:
                    disponibles["presentacion_nombre"] = disponibles["presentacion_id"]
                disponibles["presentacion_nombre"] = disponibles["presentacion_nombre"].fillna(
                    disponibles["presentacion_id"]
                )

                fecha = st.date_input("Fecha de ingreso", value=datetime.date.today(), key="cf_in_fecha")
                info_lotes = disponibles.set_index("lote_producto_id")
                lote_producto_id = st.selectbox(
                    "Lote de producto terminado",
                    disponibles["lote_producto_id"],
                    format_func=lambda x: (
                        f"{x} — {info_lotes.loc[x, 'presentacion_nombre']} — "
                        f"{info_lotes.loc[x, 'unidades_saldo']:.0f} unidades — "
                        f"origen {info_lotes.loc[x, 'lote_semielaborado_id']}"
                    ),
                )
                fila = disponibles.set_index("lote_producto_id").loc[lote_producto_id]
                cantidad_max = float(fila["unidades_saldo"])
                cantidad = st.number_input(
                    "Cantidad a ingresar", min_value=0.0, max_value=cantidad_max, value=cantidad_max
                )
                fecha_vencimiento = st.date_input(
                    "Fecha de vencimiento del producto",
                    value=datetime.date.today() + datetime.timedelta(days=30),
                    key="cf_venc",
                )
                if st.button("Registrar ingreso a cuarto frío"):
                    entrada_id = db.siguiente_id("cuarto_frio_entradas", "CF", fecha)
                    db.append_row("cuarto_frio_entradas", {
                        "entrada_id": entrada_id,
                        "fecha": fecha.isoformat(),
                        "lote_producto_id": lote_producto_id,
                        "presentacion_id": fila["presentacion_id"],
                        "cantidad": cantidad,
                        "costo_unitario": fila["costo_unitario"],
                        "fecha_vencimiento": fecha_vencimiento.isoformat(),
                        "saldo": cantidad,
                        "usuario": username,
                    })
                    db.update_row("pasteurizacion_envasado", "lote_producto_id", lote_producto_id, {
                        "unidades_saldo": cantidad_max - cantidad,
                    })
                    st.success(f"Ingreso {entrada_id} registrado en cuarto frío.")

    with tab_despacho:
        entradas = db.get_df("cuarto_frio_entradas")
        if entradas.empty:
            st.info("No hay inventario en cuarto frío todavía.")
        elif clientes.empty:
            st.warning("Configura al menos un cliente en Catálogos.")
        elif vehiculos.empty:
            st.warning("Configura al menos un vehículo en Catálogos antes de registrar un despacho.")
        else:
            entradas["saldo"] = pd.to_numeric(entradas["saldo"], errors="coerce").fillna(0)
            disponibles = entradas[entradas["saldo"] > 0].copy()
            if disponibles.empty:
                st.info("No hay saldo disponible para despachar.")
            else:
                disponibles["fecha_vencimiento"] = pd.to_datetime(
                    disponibles["fecha_vencimiento"], errors="coerce"
                )
                disponibles = disponibles.sort_values("fecha_vencimiento")
                if not presentaciones.empty:
                    disponibles = disponibles.merge(
                        presentaciones[["presentacion_id", "nombre"]].rename(
                            columns={"nombre": "presentacion_nombre"}
                        ),
                        on="presentacion_id", how="left",
                    )
                    disponibles["presentacion_nombre"] = disponibles["presentacion_nombre"].fillna(
                        disponibles["presentacion_id"]
                    )
                else:
                    disponibles["presentacion_nombre"] = disponibles["presentacion_id"]
                if not pasteurizado.empty:
                    disponibles = disponibles.merge(
                        pasteurizado[["lote_producto_id", "lote_semielaborado_id"]].rename(
                            columns={"lote_semielaborado_id": "lote_origen"}
                        ),
                        on="lote_producto_id", how="left",
                    )
                else:
                    disponibles["lote_origen"] = ""

                fecha = st.date_input("Fecha de despacho", value=datetime.date.today(), key="cf_out_fecha")
                vehiculo_id = st.selectbox(
                    "Vehículo que se carga",
                    vehiculos["vehiculo_id"],
                    format_func=lambda x: (
                        f"{vehiculos.set_index('vehiculo_id').loc[x, 'placa']} — "
                        f"{vehiculos.set_index('vehiculo_id').loc[x, 'descripcion']}"
                    ),
                )

                st.markdown(
                    "**Arma la carga de este vehículo**: por cada línea que vayas a despachar, "
                    "elige el cliente y la cantidad — puedes combinar varios clientes y "
                    "presentaciones distintas en una misma carga (deja en 0 / sin cliente "
                    "lo que no vayas a despachar)."
                )
                tabla_base = disponibles[
                    ["entrada_id", "lote_origen", "presentacion_nombre", "saldo", "fecha_vencimiento"]
                ].copy()
                tabla_base["cliente_id"] = ""
                tabla_base["cantidad_a_despachar"] = 0
                tabla_editada = st.data_editor(
                    tabla_base,
                    use_container_width=True,
                    hide_index=True,
                    disabled=["entrada_id", "lote_origen", "presentacion_nombre", "saldo", "fecha_vencimiento"],
                    column_config={
                        "cliente_id": st.column_config.SelectboxColumn(
                            "Cliente (cliente_id)", options=[""] + list(clientes["cliente_id"]),
                        ),
                        "cantidad_a_despachar": st.column_config.NumberColumn(
                            "Cantidad a despachar", min_value=0, step=1,
                        ),
                        "saldo": st.column_config.NumberColumn("Saldo disponible"),
                    },
                    key=f"editor_despacho_{vehiculo_id}_{fecha}",
                )

                tabla_editada["cantidad_a_despachar"] = pd.to_numeric(
                    tabla_editada["cantidad_a_despachar"], errors="coerce"
                ).fillna(0)
                lineas = tabla_editada[tabla_editada["cantidad_a_despachar"] > 0]
                excedidas = lineas[lineas["cantidad_a_despachar"] > lineas["saldo"]]
                sin_cliente = lineas[lineas["cliente_id"] == ""]

                if not excedidas.empty:
                    st.error(
                        "⚠️ Hay línea(s) donde la cantidad a despachar supera el saldo "
                        "disponible de ese lote. Corrige antes de guardar."
                    )
                if not sin_cliente.empty:
                    st.error("⚠️ Hay línea(s) con cantidad pero sin cliente seleccionado. Corrige antes de guardar.")
                if not lineas.empty and excedidas.empty and sin_cliente.empty:
                    nombres_clientes = clientes.set_index("cliente_id")["nombre"]
                    resumen = lineas.groupby("cliente_id")["cantidad_a_despachar"].sum()
                    texto_resumen = ", ".join(
                        f"{nombres_clientes.get(c, c)}: {int(v)}" for c, v in resumen.items()
                    )
                    st.info(f"📦 Carga total: **{int(lineas['cantidad_a_despachar'].sum())} unidades** — {texto_resumen}")

                observaciones = st.text_area("Observaciones", "", key="cf_out_obs")

                if st.button("Registrar despacho"):
                    if lineas.empty:
                        st.error("Ingresa una cantidad mayor a cero en al menos una línea.")
                    elif not excedidas.empty or not sin_cliente.empty:
                        st.error("Corrige las líneas marcadas en rojo antes de guardar.")
                    else:
                        info_entradas = disponibles.set_index("entrada_id")
                        salidas_generadas = []
                        for _, linea in lineas.iterrows():
                            entrada_id_linea = linea["entrada_id"]
                            cantidad_linea = float(linea["cantidad_a_despachar"])
                            salida_id = db.siguiente_id("cuarto_frio_salidas", "SAL", fecha)
                            db.append_row("cuarto_frio_salidas", {
                                "salida_id": salida_id,
                                "fecha": fecha.isoformat(),
                                "entrada_id": entrada_id_linea,
                                "cliente_id": linea["cliente_id"],
                                "cantidad": cantidad_linea,
                                "vehiculo_id": vehiculo_id,
                                "usuario": username,
                                "observaciones": observaciones,
                            })
                            saldo_actual = float(info_entradas.loc[entrada_id_linea, "saldo"])
                            db.update_row("cuarto_frio_entradas", "entrada_id", entrada_id_linea, {
                                "saldo": saldo_actual - cantidad_linea,
                            })
                            salidas_generadas.append(salida_id)
                        st.success(
                            f"Despacho registrado: {len(salidas_generadas)} línea(s) — "
                            f"{', '.join(salidas_generadas)}"
                        )
                        st.rerun()

    with tab_inventario:
        entradas = db.get_df("cuarto_frio_entradas")
        if entradas.empty:
            st.info("Sin inventario todavía.")
        else:
            entradas["saldo"] = pd.to_numeric(entradas["saldo"], errors="coerce").fillna(0)
            inventario = entradas[entradas["saldo"] > 0].copy()
            inventario["costo_unitario"] = pd.to_numeric(inventario["costo_unitario"], errors="coerce")
            inventario["valor"] = inventario["saldo"] * inventario["costo_unitario"]
            if not presentaciones.empty:
                inventario = inventario.merge(
                    presentaciones[["presentacion_id", "nombre"]].rename(
                        columns={"nombre": "presentacion_nombre"}
                    ),
                    on="presentacion_id", how="left",
                )
                inventario["presentacion_nombre"] = inventario["presentacion_nombre"].fillna(
                    inventario["presentacion_id"]
                )
            else:
                inventario["presentacion_nombre"] = inventario["presentacion_id"]
            if not pasteurizado.empty:
                inventario = inventario.merge(
                    pasteurizado[["lote_producto_id", "lote_semielaborado_id"]].rename(
                        columns={"lote_semielaborado_id": "lote_origen"}
                    ),
                    on="lote_producto_id", how="left",
                )
            else:
                inventario["lote_origen"] = ""
            st.dataframe(
                inventario[[
                    "entrada_id", "lote_origen", "lote_producto_id", "presentacion_nombre",
                    "saldo", "costo_unitario", "valor", "fecha_vencimiento",
                ]],
                use_container_width=True,
            )
            st.metric("Valor total en cuarto frío", f"{inventario['valor'].sum():,.2f}")

    with tab_vehiculos:
        salidas = db.get_df("cuarto_frio_salidas")
        if salidas.empty:
            st.info("Todavía no hay despachos registrados.")
        elif vehiculos.empty:
            st.info("Configura vehículos en Catálogos para poder filtrar por vehículo.")
        else:
            col1, col2 = st.columns(2)
            filtro_vehiculo = col1.selectbox(
                "Vehículo",
                ["Todos"] + list(vehiculos["vehiculo_id"]),
                format_func=lambda x: (
                    "Todos los vehículos" if x == "Todos"
                    else f"{vehiculos.set_index('vehiculo_id').loc[x, 'placa']} — "
                         f"{vehiculos.set_index('vehiculo_id').loc[x, 'descripcion']}"
                ),
            )
            filtro_fecha = col2.date_input("Fecha", value=datetime.date.today(), key="cf_veh_fecha")
            ver_todas_fechas = st.checkbox("Ver todas las fechas (ignorar el filtro de fecha)")

            vista = salidas.copy()
            if not ver_todas_fechas:
                vista = vista[vista["fecha"].astype(str) == filtro_fecha.isoformat()]
            if filtro_vehiculo != "Todos":
                vista = vista[vista["vehiculo_id"].astype(str) == str(filtro_vehiculo)]

            if vista.empty:
                st.info("No hay despachos para ese filtro.")
            else:
                entradas_ref = db.get_df("cuarto_frio_entradas")
                if not entradas_ref.empty:
                    vista = vista.merge(
                        entradas_ref[["entrada_id", "presentacion_id", "lote_producto_id"]], on="entrada_id", how="left",
                    )
                    if not presentaciones.empty:
                        vista = vista.merge(
                            presentaciones[["presentacion_id", "nombre", "kg_nominal"]].rename(
                                columns={"nombre": "presentacion_nombre"}
                            ),
                            on="presentacion_id", how="left",
                        )
                    if not pasteurizado.empty:
                        vista = vista.merge(
                            pasteurizado[["lote_producto_id", "lote_semielaborado_id"]].rename(
                                columns={"lote_semielaborado_id": "lote_origen"}
                            ),
                            on="lote_producto_id", how="left",
                        )
                if not clientes.empty:
                    vista = vista.merge(
                        clientes[["cliente_id", "nombre"]].rename(columns={"nombre": "cliente_nombre"}),
                        on="cliente_id", how="left",
                    )
                if not vehiculos.empty:
                    vista = vista.merge(
                        vehiculos[["vehiculo_id", "placa"]], on="vehiculo_id", how="left",
                    )

                vista["cantidad"] = pd.to_numeric(vista["cantidad"], errors="coerce").fillna(0)
                if "kg_nominal" in vista.columns:
                    vista["kg_nominal"] = pd.to_numeric(vista["kg_nominal"], errors="coerce").fillna(0)
                    vista["kg"] = vista["cantidad"] * vista["kg_nominal"]
                else:
                    vista["kg"] = 0.0

                columnas_mostrar = [c for c in [
                    "fecha", "placa", "cliente_nombre", "lote_origen",
                    "presentacion_nombre", "cantidad", "kg", "salida_id",
                ] if c in vista.columns]
                st.dataframe(vista[columnas_mostrar], use_container_width=True)

                cantidad_total = vista["cantidad"].sum()
                kg_total = vista["kg"].sum()
                col_m1, col_m2 = st.columns(2)
                col_m1.metric("Total de unidades cargadas (según filtro)", f"{int(cantidad_total)}")
                col_m2.metric("Total en kg (según filtro)", f"{kg_total:,.1f} kg")

                if "cliente_nombre" in vista.columns:
                    st.markdown("**Resumen por cliente:**")
                    resumen_cliente = vista.groupby("cliente_nombre").agg(
                        unidades=("cantidad", "sum"), kg=("kg", "sum"),
                    )
                    st.dataframe(resumen_cliente, use_container_width=True)
