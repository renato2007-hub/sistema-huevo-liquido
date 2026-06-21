"""
Cuarto frio: ingreso del producto terminado que sale de pasteurizacion,
despacho a clientes (organizado por vehiculo de reparto, ya que un mismo
viaje normalmente lleva varias presentaciones para varios clientes), control
de inventario, y vista de que se cargo en cada vehiculo.
"""
import datetime
import streamlit as st
import pandas as pd
from utils.permisos import ve_costos


def render(db, username, rol):
    st.title("Cuarto frío")
    tab_ingreso, tab_despacho, tab_inventario, tab_vehiculos, tab_verificacion = st.tabs(
        ["Ingreso desde envasado", "Despacho a cliente", "Inventario actual", "🚚 Cargas por vehículo", "✅ Verificación de cargas"]
    )

    pasteurizado = db.get_df("pasteurizacion_envasado")
    clientes = db.get_df("clientes")
    presentaciones = db.get_df("presentaciones")
    vehiculos = db.get_df("vehiculos")
    usuarios_cat = db.get_df("usuarios")
    produccion_semi = db.get_df("produccion_semielaborados")

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
                pedidos_df = db.get_df("pedidos")
                opciones_pedido = [""]
                mapa_pedido_cliente = {}
                if not pedidos_df.empty:
                    prod_bool = pedidos_df["producido"].astype(str).str.upper().isin(["TRUE", "1", "SI", "SÍ"])
                    pedidos_pend = pedidos_df[~prod_bool]
                    nombres_cli_pedido = clientes.set_index("cliente_id")["nombre"] if not clientes.empty else {}
                    for _, pr in pedidos_pend.iterrows():
                        cli_nombre = nombres_cli_pedido.get(pr["cliente_id"], pr["cliente_id"])
                        etiqueta = f"{pr['pedido_id']} ({cli_nombre} - {pr['tipo_producto']}, {pr['cantidad_kg']}kg)"
                        opciones_pedido.append(etiqueta)
                        mapa_pedido_cliente[etiqueta] = (pr["pedido_id"], pr["cliente_id"])

                tabla_base = disponibles[
                    ["entrada_id", "lote_origen", "presentacion_nombre", "saldo", "fecha_vencimiento"]
                ].copy()
                tabla_base["cliente_id"] = ""
                tabla_base["cantidad_a_despachar"] = 0
                tabla_base["pedido_ref"] = ""
                opciones_cliente_nombres = [""] + list(clientes["nombre"])
                mapa_nombre_a_cliente_id = dict(zip(clientes["nombre"], clientes["cliente_id"]))
                tabla_editada = st.data_editor(
                    tabla_base,
                    use_container_width=True,
                    hide_index=True,
                    disabled=["entrada_id", "lote_origen", "presentacion_nombre", "saldo", "fecha_vencimiento"],
                    column_config={
                        "cliente_id": st.column_config.SelectboxColumn(
                            "Cliente", options=opciones_cliente_nombres,
                        ),
                        "cantidad_a_despachar": st.column_config.NumberColumn(
                            "Cantidad a despachar", min_value=0, step=1,
                        ),
                        "saldo": st.column_config.NumberColumn("Saldo disponible"),
                        "pedido_ref": st.column_config.SelectboxColumn(
                            "Pedido que cumple (opcional)", options=opciones_pedido,
                        ),
                    },
                    key=f"editor_despacho_{vehiculo_id}_{fecha}",
                )
                st.caption(
                    "💡 Si esta línea cumple un pedido pendiente, selecciónalo en 'Pedido que cumple' — "
                    "queda enlazado en Trazabilidad y el pedido se marca como producido automáticamente."
                )

                tabla_editada["cliente_id"] = tabla_editada["cliente_id"].apply(
                    lambda nombre: mapa_nombre_a_cliente_id.get(nombre, nombre)
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
                        pedidos_marcados = []
                        for _, linea in lineas.iterrows():
                            entrada_id_linea = linea["entrada_id"]
                            cantidad_linea = float(linea["cantidad_a_despachar"])
                            etiqueta_pedido = linea.get("pedido_ref", "")
                            pedido_id_real = ""
                            if etiqueta_pedido and etiqueta_pedido in mapa_pedido_cliente:
                                pedido_id_real, cliente_del_pedido = mapa_pedido_cliente[etiqueta_pedido]
                                if cliente_del_pedido != linea["cliente_id"]:
                                    st.warning(
                                        f"⚠️ El pedido {pedido_id_real} es de otro cliente distinto al "
                                        f"de esta línea — se guardó igual, pero revisa si fue un error."
                                    )
                            salida_id = db.siguiente_id("cuarto_frio_salidas", "SAL", fecha)
                            db.append_row("cuarto_frio_salidas", {
                                "salida_id": salida_id,
                                "fecha": fecha.isoformat(),
                                "entrada_id": entrada_id_linea,
                                "cliente_id": linea["cliente_id"],
                                "cantidad": cantidad_linea,
                                "vehiculo_id": vehiculo_id,
                                "pedido_ref": pedido_id_real,
                                "usuario": username,
                                "observaciones": observaciones,
                            })
                            saldo_actual = float(info_entradas.loc[entrada_id_linea, "saldo"])
                            db.update_row("cuarto_frio_entradas", "entrada_id", entrada_id_linea, {
                                "saldo": saldo_actual - cantidad_linea,
                            })
                            salidas_generadas.append(salida_id)
                            if pedido_id_real and pedido_id_real not in pedidos_marcados:
                                db.update_row("pedidos", "pedido_id", pedido_id_real, {"producido": True})
                                pedidos_marcados.append(pedido_id_real)
                        mensaje_pedidos = f" — pedido(s) {', '.join(pedidos_marcados)} marcado(s) como producido." if pedidos_marcados else ""
                        st.success(
                            f"Despacho registrado: {len(salidas_generadas)} línea(s) — "
                            f"{', '.join(salidas_generadas)}{mensaje_pedidos}"
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
                    pasteurizado[["lote_producto_id", "lote_semielaborado_id", "pasteurizado"]].rename(
                        columns={"lote_semielaborado_id": "lote_origen"}
                    ),
                    on="lote_producto_id", how="left",
                )
            else:
                inventario["lote_origen"] = ""
                inventario["pasteurizado"] = True
            columnas_inv = [
                "entrada_id", "lote_origen", "lote_producto_id", "presentacion_nombre",
                "saldo", "fecha_vencimiento",
            ]
            if ve_costos(rol):
                columnas_inv[5:5] = ["costo_unitario", "valor"]
            st.dataframe(inventario[columnas_inv], use_container_width=True)

            # ---- desglose en kg por tipo de producto y estado de pasteurizacion ----
            st.markdown("##### Kg disponibles en cuarto frío")
            inv_kg = inventario.copy()
            if not presentaciones.empty:
                inv_kg = inv_kg.merge(
                    presentaciones[["presentacion_id", "kg_nominal"]], on="presentacion_id", how="left",
                )
            else:
                inv_kg["kg_nominal"] = 0
            inv_kg["kg_nominal"] = pd.to_numeric(inv_kg["kg_nominal"], errors="coerce").fillna(0)
            if not produccion_semi.empty:
                inv_kg = inv_kg.merge(
                    produccion_semi[["lote_semielaborado_id", "tipo_producto"]].rename(
                        columns={"lote_semielaborado_id": "lote_origen"}
                    ),
                    on="lote_origen", how="left",
                )
            else:
                inv_kg["tipo_producto"] = ""
            inv_kg["tipo_producto"] = inv_kg["tipo_producto"].fillna("")
            inv_kg["pasteurizado"] = inv_kg["pasteurizado"].astype(str).str.upper().isin(["TRUE", "1", "SI", "SÍ"])
            inv_kg["kg"] = inv_kg["saldo"] * inv_kg["kg_nominal"]

            resumen_kg = inv_kg.groupby(["tipo_producto", "pasteurizado"])["kg"].sum().reset_index()
            resumen_kg = resumen_kg[resumen_kg["kg"] > 0]

            def _etiqueta(tipo, pasteurizado):
                tipo_legible = {"Huevo entero": "Huevo", "Clara": "Clara", "Yema": "Yema"}.get(tipo, tipo or "Producto")
                sufijo = "a" if tipo_legible in ("Clara", "Yema") else "o"
                if pasteurizado:
                    return f"{tipo_legible} pasteurizad{sufijo}"
                return f"{tipo_legible} sin pasteurizar"

            if resumen_kg.empty:
                st.info("No hay datos suficientes para calcular el desglose en kg.")
            else:
                cols = st.columns(len(resumen_kg))
                for col, (_, fila) in zip(cols, resumen_kg.iterrows()):
                    col.metric(_etiqueta(fila["tipo_producto"], fila["pasteurizado"]), f"{fila['kg']:,.1f} kg")

            if ve_costos(rol):
                st.write("")
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

    # ======================== VERIFICACION DE CARGAS ========================
    with tab_verificacion:
        st.caption(
            "Para cuando un conductor reporta que faltó algo por cargar — registra "
            "si la carga estuvo correcta o no, y queda asociado a quién hizo el despacho."
        )
        sub_registrar, sub_historial = st.tabs(["➕ Registrar verificación", "📊 Historial y conteo de errores"])

        def _nombre_usuario(username_login):
            if usuarios_cat.empty or "username" not in usuarios_cat.columns:
                return username_login
            fila = usuarios_cat[usuarios_cat["username"] == username_login]
            if fila.empty or not str(fila.iloc[0].get("nombre", "")).strip():
                return username_login
            return fila.iloc[0]["nombre"]

        with sub_registrar:
            salidas_v = db.get_df("cuarto_frio_salidas")
            if salidas_v.empty:
                st.info("Todavía no hay despachos registrados para verificar.")
            elif vehiculos.empty:
                st.info("Configura vehículos en Catálogos antes de usar esto.")
            else:
                c1, c2 = st.columns(2)
                fecha_v = c1.date_input("Fecha de la carga", value=datetime.date.today(), key="verif_fecha")
                vehiculo_v = c2.selectbox(
                    "Vehículo", vehiculos["vehiculo_id"],
                    format_func=lambda x: vehiculos.set_index("vehiculo_id").loc[x, "placa"],
                    key="verif_vehiculo",
                )

                despachos_dia = salidas_v[
                    (salidas_v["fecha"].astype(str) == fecha_v.isoformat())
                    & (salidas_v["vehiculo_id"].astype(str) == str(vehiculo_v))
                ]

                if despachos_dia.empty:
                    st.warning("No hay despachos registrados para esa fecha y vehículo — no hay nada que verificar todavía.")
                else:
                    usuarios_involucrados = sorted(despachos_dia["usuario"].dropna().unique().tolist())
                    st.markdown(f"**Despachos registrados ese día para este vehículo:** {len(despachos_dia)}")
                    st.dataframe(
                        despachos_dia[[c for c in ["salida_id", "cliente_id", "cantidad", "usuario"] if c in despachos_dia.columns]],
                        use_container_width=True, hide_index=True,
                    )

                    if len(usuarios_involucrados) > 1:
                        st.caption("Más de una persona registró despachos para este vehículo ese día — elige a quién corresponde la verificación.")
                    despachador_sel = st.selectbox(
                        "Despachador responsable", usuarios_involucrados,
                        format_func=_nombre_usuario, key="verif_despachador",
                    )

                    correcto = st.radio("¿La carga estuvo correcta?", ["✅ Sí, todo correcto", "❌ Hubo un error"], key="verif_correcto")
                    descripcion_error = ""
                    if correcto == "❌ Hubo un error":
                        descripcion_error = st.text_area(
                            "¿Qué faltó o estuvo mal? (ej. 'faltaron 10 fundas de clara para Cliente X')",
                            key="verif_descripcion",
                        )
                    observaciones_v = st.text_input("Observaciones (opcional)", key="verif_obs")

                    if st.button("💾 Guardar verificación", type="primary"):
                        if correcto == "❌ Hubo un error" and not descripcion_error.strip():
                            st.error("Describe qué faltó o estuvo mal antes de guardar.")
                        else:
                            verificacion_id = db.siguiente_id("verificacion_cargas", "VC", fecha_v)
                            db.append_row("verificacion_cargas", {
                                "verificacion_id": verificacion_id,
                                "fecha": fecha_v.isoformat(),
                                "vehiculo_id": vehiculo_v,
                                "correcto": correcto == "✅ Sí, todo correcto",
                                "despachador": despachador_sel,
                                "descripcion_error": descripcion_error,
                                "usuario": username,
                                "observaciones": observaciones_v,
                            })
                            st.success(f"Verificación {verificacion_id} guardada.")
                            st.rerun()

        with sub_historial:
            verif = db.get_df("verificacion_cargas")
            if verif.empty:
                st.info("No hay verificaciones registradas todavía.")
            else:
                verif = verif.copy()
                verif["correcto_bool"] = verif["correcto"].astype(str).str.upper().isin(["TRUE", "1", "SI", "SÍ"])
                verif["despachador_nombre"] = verif["despachador"].apply(_nombre_usuario)

                c1, c2 = st.columns(2)
                desde_v = c1.date_input("Desde", value=datetime.date.today() - datetime.timedelta(days=30), key="verif_hist_desde")
                hasta_v = c2.date_input("Hasta", value=datetime.date.today(), key="verif_hist_hasta")
                verif_periodo = verif[
                    (verif["fecha"].astype(str) >= desde_v.isoformat()) & (verif["fecha"].astype(str) <= hasta_v.isoformat())
                ]

                if verif_periodo.empty:
                    st.info("No hay verificaciones en ese período.")
                else:
                    total_verif = len(verif_periodo)
                    total_errores = (~verif_periodo["correcto_bool"]).sum()
                    c1, c2, c3 = st.columns(3)
                    c1.metric("Cargas verificadas", total_verif)
                    c2.metric("Con error", int(total_errores))
                    c3.metric("% correctas", f"{(total_verif - total_errores) / total_verif * 100:.0f}%" if total_verif else "—")

                    st.markdown("##### Conteo de errores por despachador")
                    conteo = verif_periodo.groupby("despachador_nombre").agg(
                        cargas_verificadas=("verificacion_id", "count"),
                        errores=("correcto_bool", lambda s: (~s).sum()),
                    ).reset_index().sort_values("errores", ascending=False)
                    st.dataframe(conteo, use_container_width=True, hide_index=True)
                    st.bar_chart(conteo.set_index("despachador_nombre")["errores"])

                    st.markdown("##### Detalle de errores")
                    errores_detalle = verif_periodo[~verif_periodo["correcto_bool"]]
                    if errores_detalle.empty:
                        st.success("🎉 Sin errores registrados en este período.")
                    else:
                        if not vehiculos.empty:
                            errores_detalle = errores_detalle.merge(
                                vehiculos[["vehiculo_id", "placa"]], on="vehiculo_id", how="left",
                            )
                        st.dataframe(
                            errores_detalle[[c for c in [
                                "fecha", "placa", "despachador_nombre", "descripcion_error", "observaciones",
                            ] if c in errores_detalle.columns]],
                            use_container_width=True, hide_index=True,
                        )
