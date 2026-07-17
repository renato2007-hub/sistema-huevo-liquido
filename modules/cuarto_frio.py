"""
Cuarto frio: ingreso del producto terminado que sale de pasteurizacion,
despacho a clientes (organizado por vehiculo de reparto, ya que un mismo
viaje normalmente lleva varias presentaciones para varios clientes), control
de inventario, y vista de que se cargo en cada vehiculo.
"""
import datetime
import streamlit as st
import pandas as pd
from utils.permisos import ve_costos, es_despachador


def render(db, username, rol):
    st.title("❄️ Cuarto frío")

    if es_despachador(rol):
        # Despachador: solo ve despacho, inventario, stock a granel y cargas
        tab_despacho, tab_inventario, tab_granel_cf, tab_vehiculos = st.tabs([
            "🚚 Despacho a cliente", "📦 Inventario actual",
            "🫙 Stock a granel", "🚛 Cargas por vehículo",
        ])
        tab_ingreso = None
        tab_verificacion = None
    else:
        tab_ingreso, tab_despacho, tab_inventario, tab_granel_cf, tab_vehiculos, tab_verificacion = st.tabs(
            ["Ingreso desde envasado", "Despacho a cliente", "Inventario actual",
             "🫙 Stock a granel", "🚚 Cargas por vehículo", "✅ Verificación de cargas"]
        )

    pasteurizado = db.get_df("pasteurizacion_envasado")
    clientes = db.get_df("clientes")
    presentaciones = db.get_df("presentaciones")
    vehiculos = db.get_df("vehiculos")
    usuarios_cat = db.get_df("usuarios")
    produccion_semi = db.get_df("produccion_semielaborados")

    if tab_ingreso is not None:
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
                        st.rerun()

    with tab_despacho:
        entradas = db.get_df("cuarto_frio_entradas")
        if entradas.empty:
            st.info("No hay inventario en cuarto frío todavía.")
        elif clientes.empty:
            st.warning("Configura al menos un cliente en Catálogos.")
        elif vehiculos.empty or "vehiculo_id" not in vehiculos.columns:
            st.warning("Configura al menos un vehículo en Catálogos (verifica que la columna vehiculo_id exista en el Sheet).")
        else:
            entradas["saldo"] = pd.to_numeric(entradas["saldo"], errors="coerce").fillna(0)
            disponibles = entradas[entradas["saldo"] > 0].copy()
            if disponibles.empty:
                st.info("No hay saldo disponible para despachar.")
            else:
                disponibles["fecha_vencimiento"] = pd.to_datetime(disponibles["fecha_vencimiento"], errors="coerce")
                disponibles = disponibles.sort_values("fecha_vencimiento")
                if not presentaciones.empty:
                    disponibles = disponibles.merge(
                        presentaciones[["presentacion_id", "nombre"]].rename(columns={"nombre": "presentacion_nombre"}),
                        on="presentacion_id", how="left",
                    )
                    disponibles["presentacion_nombre"] = disponibles["presentacion_nombre"].fillna(disponibles["presentacion_id"])
                else:
                    disponibles["presentacion_nombre"] = disponibles["presentacion_id"]
                if not pasteurizado.empty:
                    disponibles = disponibles.merge(
                        pasteurizado[["lote_producto_id", "lote_semielaborado_id"]].rename(
                            columns={"lote_semielaborado_id": "lote_origen"}),
                        on="lote_producto_id", how="left",
                    )
                else:
                    disponibles["lote_origen"] = ""

                # ── Encabezado de la carga ──
                c1, c2 = st.columns(2)
                fecha = c1.date_input("Fecha de despacho", value=datetime.date.today(), key="cf_out_fecha")
                vehiculo_id = c2.selectbox(
                    "Vehículo que se carga",
                    vehiculos["vehiculo_id"],
                    format_func=lambda x: f"{vehiculos.set_index('vehiculo_id').loc[x, 'placa']} — {vehiculos.set_index('vehiculo_id').loc[x, 'descripcion']}",
                )
                personal = db.get_df("personal")
                despachador_personal_id = ""
                if not personal.empty:
                    opciones_desp = [""] + list(personal["personal_id"])
                    despachador_personal_id = st.selectbox(
                        "Trabajador responsable de la carga",
                        opciones_desp,
                        format_func=lambda x: "— Selecciona quien cargó el camión —" if x == "" else personal.set_index("personal_id").loc[x, "nombre"],
                        key="cf_despachador_personal",
                    )
                observaciones = st.text_input("Observaciones generales (opcional)", "", key="cf_out_obs")

                st.divider()

                # ── Pedidos pendientes ──
                pedidos_df = db.get_df("pedidos")
                opciones_pedido = ["— Sin pedido —"]
                mapa_pedido_cliente = {}
                if not pedidos_df.empty:
                    prod_bool = pedidos_df["producido"].astype(str).str.upper().isin(["TRUE", "1", "SI", "SÍ"])
                    pedidos_pend = pedidos_df[~prod_bool]
                    nombres_cli_ped = clientes.set_index("cliente_id")["nombre"] if not clientes.empty else {}
                    for _, pr in pedidos_pend.iterrows():
                        cli_nombre = nombres_cli_ped.get(pr["cliente_id"], pr["cliente_id"])
                        etiqueta = f"{pr['pedido_id']} ({cli_nombre} — {pr['tipo_producto']}, {pr['cantidad_kg']}kg)"
                        opciones_pedido.append(etiqueta)
                        mapa_pedido_cliente[etiqueta] = (pr["pedido_id"], pr["cliente_id"])

                # ── Opciones de lote disponible — descontando lo ya comprometido ──
                lineas_acum_pre = st.session_state.get(f"lineas_despacho_{vehiculo_id}_{fecha}", [])
                comprometido_por_lote = {}
                for l in lineas_acum_pre:
                    comprometido_por_lote[l["entrada_id"]] = comprometido_por_lote.get(l["entrada_id"], 0) + l["cantidad"]

                # Mapa de lote_origen desde pasteurizacion
                mapa_lote_origen = {}
                if not pasteurizado.empty and "lote_producto_id" in pasteurizado.columns and "lote_semielaborado_id" in pasteurizado.columns:
                    mapa_lote_origen = dict(zip(pasteurizado["lote_producto_id"], pasteurizado["lote_semielaborado_id"]))

                opciones_entrada = []
                mapa_entrada = {}
                mapa_entrada_info = {}
                for _, r in disponibles.iterrows():
                    saldo_libre = int(r["saldo"]) - int(comprometido_por_lote.get(r["entrada_id"], 0))
                    if saldo_libre <= 0:
                        continue
                    lote_origen = mapa_lote_origen.get(r.get("lote_producto_id", ""), r.get("lote_origen", ""))
                    etiqueta = f"{r['presentacion_nombre']} | lote: {lote_origen or r['entrada_id']} | disponible: {saldo_libre} | vence: {str(r['fecha_vencimiento'])[:10]}"
                    opciones_entrada.append(etiqueta)
                    mapa_entrada[etiqueta] = r["entrada_id"]
                    mapa_entrada_info[r["entrada_id"]] = {
                        "presentacion": r["presentacion_nombre"],
                        "lote_origen": lote_origen or r["entrada_id"],
                        "saldo_libre": saldo_libre,
                    }

                mapa_cliente_id = dict(zip(clientes["nombre"], clientes["cliente_id"]))

                # ── Acumulador en session_state ──
                clave_lineas = f"lineas_despacho_{vehiculo_id}_{fecha}"
                if clave_lineas not in st.session_state:
                    st.session_state[clave_lineas] = []

                # ── Formulario de una línea ──
                st.markdown("##### ➕ Agregar línea a la carga")
                if not opciones_entrada:
                    st.warning("No hay saldo disponible (o todo ya está comprometido en la carga actual).")
                else:
                    ca, cb, cc, cd = st.columns([2, 3, 1, 2])
                    cliente_sel = ca.selectbox("Cliente", ["— Elige —"] + list(clientes["nombre"]), key="desp_cliente")
                    entrada_sel = cb.selectbox("Lote / Presentación", opciones_entrada, key="desp_entrada")
                    cantidad_sel = cc.number_input("Cant.", min_value=1, step=1, key="desp_cantidad")
                    pedido_sel = cd.selectbox("Pedido (opcional)", opciones_pedido, key="desp_pedido")

                    if st.button("➕ Agregar a la carga", use_container_width=True):
                        if cliente_sel == "— Elige —":
                            st.error("Selecciona un cliente.")
                        else:
                            entrada_id_sel = mapa_entrada.get(entrada_sel, "")
                            info_ent = mapa_entrada_info.get(entrada_id_sel, {})
                            saldo_libre = info_ent.get("saldo_libre", 0)
                            if cantidad_sel > saldo_libre:
                                st.error(f"Solo quedan {saldo_libre} unidades disponibles de ese lote.")
                            else:
                                pedido_id_real = ""
                                if pedido_sel != "— Sin pedido —" and pedido_sel in mapa_pedido_cliente:
                                    pedido_id_real = mapa_pedido_cliente[pedido_sel][0]
                                st.session_state[clave_lineas].append({
                                    "cliente": cliente_sel,
                                    "cliente_id": mapa_cliente_id.get(cliente_sel, cliente_sel),
                                    "entrada_id": entrada_id_sel,
                                    "presentacion": info_ent.get("presentacion", ""),
                                    "lote_origen": info_ent.get("lote_origen", ""),
                                    "cantidad": cantidad_sel,
                                    "pedido_ref": pedido_id_real,
                                })
                                st.rerun()

                # ── Tabla acumulada ──
                lineas_acum = st.session_state[clave_lineas]
                if lineas_acum:
                    st.markdown("##### 📋 Carga armada hasta ahora")
                    df_acum = pd.DataFrame(lineas_acum)
                    df_acum["Pedido"] = df_acum["pedido_ref"].replace("", "—")
                    df_acum = df_acum.rename(columns={
                        "cliente": "Cliente",
                        "presentacion": "Presentación",
                        "lote_origen": "Lote origen",
                        "cantidad": "Unidades",
                    })[["Cliente", "Presentación", "Lote origen", "Unidades", "Pedido"]]
                    st.dataframe(df_acum, use_container_width=True, hide_index=True)
                    st.info(f"📦 Total: **{sum(l['cantidad'] for l in lineas_acum)} unidades** en {len(lineas_acum)} línea(s)")

                    ce, cf = st.columns(2)
                    if ce.button("🗑️ Borrar última línea"):
                        st.session_state[clave_lineas].pop()
                        st.rerun()
                    if cf.button("🗑️ Vaciar toda la carga"):
                        st.session_state[clave_lineas] = []
                        st.rerun()

                    st.write("")
                    if st.button("💾 Registrar despacho completo", type="primary", use_container_width=True):
                        if not despachador_personal_id:
                            st.error("Selecciona el trabajador responsable antes de guardar.")
                        else:
                            salidas_generadas = []
                            pedidos_marcados = []
                            for linea in lineas_acum:
                                salida_id = db.siguiente_id("cuarto_frio_salidas", "SAL", fecha)
                                db.append_row("cuarto_frio_salidas", {
                                    "salida_id": salida_id,
                                    "fecha": fecha.isoformat(),
                                    "entrada_id": linea["entrada_id"],
                                    "cliente_id": linea["cliente_id"],
                                    "cantidad": linea["cantidad"],
                                    "vehiculo_id": vehiculo_id,
                                    "despachador": despachador_personal_id,
                                    "pedido_ref": linea["pedido_ref"],
                                    "usuario": username,
                                    "observaciones": observaciones,
                                })
                                saldo_actual = float(entradas.set_index("entrada_id").loc[linea["entrada_id"], "saldo"])
                                db.update_row("cuarto_frio_entradas", "entrada_id", linea["entrada_id"], {
                                    "saldo": saldo_actual - linea["cantidad"],
                                })
                                salidas_generadas.append(salida_id)
                                if linea["pedido_ref"] and linea["pedido_ref"] not in pedidos_marcados:
                                    db.update_row("pedidos", "pedido_id", linea["pedido_ref"], {"producido": True})
                                    pedidos_marcados.append(linea["pedido_ref"])

                            st.session_state[clave_lineas] = []
                            msg = f"✅ Despacho registrado: {len(salidas_generadas)} línea(s)"
                            if pedidos_marcados:
                                msg += f" — pedido(s) {', '.join(pedidos_marcados)} marcado(s) como producido(s)"
                            st.success(msg)
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
            st.dataframe(inventario[columnas_inv], use_container_width=True, hide_index=True)

            # ── Visualización estilo Power BI ──────────────────────────────
            import plotly.graph_objects as go
            from plotly.subplots import make_subplots

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
            inv_kg["tipo_producto"] = inv_kg["tipo_producto"].fillna("Sin clasificar")
            inv_kg["pasteurizado_bool"] = inv_kg.get("pasteurizado", pd.Series(dtype=str)).astype(str).str.upper().isin(["TRUE","1","SI","SÍ"])
            inv_kg["kg"] = inv_kg["saldo"] * inv_kg["kg_nominal"]

            def _etiqueta(tipo, past):
                t = {"Huevo entero":"Huevo","Clara":"Clara","Yema":"Yema"}.get(tipo, tipo or "Producto")
                s = "a" if t in ("Clara","Yema") else "o"
                return f"{t} pasteurizad{s}" if past else f"{t} sin pasteurizar"

            inv_kg["etiqueta"] = inv_kg.apply(lambda r: _etiqueta(r["tipo_producto"], r["pasteurizado_bool"]), axis=1)

            # ── KPIs ──
            st.write("")
            total_unidades = int(inventario["saldo"].sum())
            total_kg = inv_kg["kg"].sum()
            n_presentaciones = inventario["presentacion_nombre"].nunique()
            n_lotes = inventario["lote_producto_id"].nunique()
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("📦 Total unidades", f"{total_unidades:,}")
            k2.metric("⚖️ Total kg", f"{total_kg:,.1f} kg")
            k3.metric("🏷️ Presentaciones", f"{n_presentaciones}")
            k4.metric("🔢 Lotes en stock", f"{n_lotes}")

            st.write("")
            col_bar, col_dona = st.columns([3, 2])

            # Barras por presentación
            with col_bar:
                st.markdown("**Unidades por presentación**")
                pres_g = inventario.groupby("presentacion_nombre")["saldo"].sum().reset_index()
                pres_g = pres_g[pres_g["saldo"] > 0].sort_values("saldo", ascending=True)
                COLS = ["#1565c0","#2e7d32","#f9a825","#6a1b9a","#D9740C","#00695c","#c62828"]
                fig_bar = go.Figure(go.Bar(
                    x=pres_g["saldo"].tolist(),
                    y=pres_g["presentacion_nombre"].tolist(),
                    orientation="h",
                    marker_color=[COLS[i % len(COLS)] for i in range(len(pres_g))],
                    text=pres_g["saldo"].apply(lambda v: f"{int(v)}").tolist(),
                    textposition="outside",
                    hovertemplate="%{y}: %{x} unidades<extra></extra>",
                ))
                fig_bar.update_layout(
                    height=max(220, len(pres_g) * 52),
                    margin=dict(l=10, r=60, t=10, b=20),
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                    xaxis=dict(showgrid=True, gridcolor="#f0f0f0"),
                )
                st.plotly_chart(fig_bar, use_container_width=True)

            # Dona por tipo de producto en kg
            with col_dona:
                st.markdown("**Kg por tipo de producto**")
                prod_g = inv_kg.groupby("etiqueta")["kg"].sum().reset_index()
                prod_g = prod_g[prod_g["kg"] > 0]
                if not prod_g.empty:
                    fig_dona = go.Figure(go.Pie(
                        labels=prod_g["etiqueta"].tolist(),
                        values=prod_g["kg"].tolist(),
                        hole=0.5,
                        marker_colors=COLS[:len(prod_g)],
                        textinfo="label+percent",
                        hovertemplate="%{label}: %{value:,.1f} kg<extra></extra>",
                    ))
                    fig_dona.update_layout(
                        height=280,
                        showlegend=False,
                        margin=dict(l=10, r=10, t=10, b=10),
                        paper_bgcolor="rgba(0,0,0,0)",
                    )
                    st.plotly_chart(fig_dona, use_container_width=True)
                else:
                    st.info("Sin datos de kg.")

            st.write("")
            st.markdown("##### Unidades por presentación y producto")
            resumen_unid = inv_kg.groupby(["tipo_producto", "presentacion_nombre"])["saldo"].sum().reset_index()
            resumen_unid = resumen_unid[resumen_unid["saldo"] > 0].sort_values(["tipo_producto", "presentacion_nombre"])
            if not resumen_unid.empty:
                cols_u = st.columns(len(resumen_unid))
                for col, (_, fila) in zip(cols_u, resumen_unid.iterrows()):
                    col.metric(
                        f"{fila['tipo_producto']}\n{fila['presentacion_nombre']}",
                        f"{int(fila['saldo'])} unid."
                    )

    with tab_granel_cf:
        stock_df = db.get_df("stock_a_granel")
        hoy_cf = datetime.date.today()
        limite_venc = (hoy_cf + datetime.timedelta(days=1)).isoformat()
        hoy_str_cf = hoy_cf.isoformat()

        if stock_df.empty:
            st.info("No hay stock a granel en cuarto frío.")
        else:
            stock_df["kg_saldo"] = pd.to_numeric(stock_df["kg_saldo"], errors="coerce").fillna(0)
            stock_df["kg_inicial"] = pd.to_numeric(stock_df["kg_inicial"], errors="coerce").fillna(0)
            stock_activo = stock_df[stock_df["kg_saldo"] > 0].copy()

            if stock_activo.empty:
                st.success("No hay stock a granel activo — todo fue procesado o desechado.")
            else:
                stock_activo["dias"] = stock_activo["fecha_entrada"].apply(
                    lambda f: (hoy_cf - datetime.date.fromisoformat(str(f)[:10])).days
                )
                stock_activo["alerta"] = stock_activo["dias"].apply(
                    lambda d: "🔴 VENCIDO" if d >= 2 else ("🟡 Vence hoy" if d == 1 else "🟢 OK")
                )
                vencidos = stock_activo[stock_activo["dias"] >= 2]
                proximos = stock_activo[stock_activo["dias"] == 1]
                if not vencidos.empty:
                    st.error(f"⚠️ {len(vencidos)} recipiente(s) con más de 2 días — hay que envasar o desechar de inmediato.")
                if not proximos.empty:
                    st.warning(f"🟡 {len(proximos)} recipiente(s) vencen hoy — último día para usarlos.")

                st.dataframe(
                    stock_activo[["stock_id", "fecha_entrada", "lote_origen", "tipo_producto",
                                  "kg_inicial", "kg_saldo", "dias", "alerta"]].rename(columns={
                        "stock_id": "Recipiente", "fecha_entrada": "Fecha entrada",
                        "lote_origen": "Lote origen", "tipo_producto": "Tipo",
                        "kg_inicial": "Kg inicial", "kg_saldo": "Kg disponibles",
                        "dias": "Días en CF", "alerta": "Estado",
                    }),
                    use_container_width=True, hide_index=True,
                )

        st.divider()
        st.markdown("##### Acción sobre un recipiente")
        stock_df2 = db.get_df("stock_a_granel")
        if not stock_df2.empty:
            stock_df2["kg_saldo"] = pd.to_numeric(stock_df2["kg_saldo"], errors="coerce").fillna(0)
            stock_activo2 = stock_df2[stock_df2["kg_saldo"] > 0]
        else:
            stock_activo2 = pd.DataFrame()

        if stock_activo2.empty:
            st.info("No hay recipientes activos para operar.")
        else:
            stock_sel = st.selectbox(
                "Selecciona el recipiente",
                stock_activo2["stock_id"],
                format_func=lambda x: (
                    f"{x} — {stock_activo2.set_index('stock_id').loc[x, 'tipo_producto']} "
                    f"({stock_activo2.set_index('stock_id').loc[x, 'kg_saldo']:.1f} kg)"
                ),
                key="granel_accion_sel",
            )
            fila_stock = stock_activo2.set_index("stock_id").loc[stock_sel]
            kg_disp_stock = float(fila_stock["kg_saldo"])
            tipo_stock = str(fila_stock["tipo_producto"])
            lote_origen_stock = str(fila_stock["lote_origen"])

            accion = st.radio(
                "¿Qué quieres hacer con este recipiente?",
                ["🔄 Pasar a producción (próximo turno)", "🗑️ Desechar"],
                key="granel_accion",
            )

            kg_accion = st.number_input(
                f"Kg a procesar (máx {kg_disp_stock:.1f} kg)",
                min_value=0.1, max_value=kg_disp_stock, value=kg_disp_stock, step=0.1,
                key="granel_kg_accion",
            )

            if accion == "🔄 Pasar a producción (próximo turno)":
                st.caption(
                    "Se creará un nuevo lote semielaborado con esos kg — "
                    "aparecerá en el inventario de tanques para el siguiente turno."
                )
                fecha_retorno = st.date_input("Fecha de retorno a producción", value=hoy_cf, key="granel_fecha_retorno")
                if st.button("🔄 Confirmar retorno a producción", type="primary"):
                    nuevo_lote_id = db.siguiente_id("produccion_semielaborados", lote_origen_stock[:2], fecha_retorno)
                    db.append_row("produccion_semielaborados", {
                        "lote_semielaborado_id": nuevo_lote_id,
                        "fecha": fecha_retorno.isoformat(),
                        "orden_produccion": "",
                        "tipo_producto": tipo_stock,
                        "categoria_id": "",
                        "cubetas_totales": 0,
                        "kg_teorico_bruto": 0,
                        "kg_liquido_teorico": 0,
                        "kg_real": kg_accion,
                        "clara_teorica_kg": 0, "clara_real_kg": 0,
                        "yema_teorica_kg": 0, "yema_real_kg": 0,
                        "cascara_teorica_kg": 0, "cascara_real_kg": 0,
                        "agua_litros": 0,
                        "costo_huevo": 0, "costo_insumos": 0, "costo_mano_obra": 0,
                        "costo_total": 0, "costo_unitario_kg": 0,
                        "kg_saldo": kg_accion,
                        "balance_masa_pct": 0, "turno": "",
                        "usuario": username,
                        "observaciones": f"Retorno desde recipiente {stock_sel}",
                    })
                    nuevo_saldo_stock = kg_disp_stock - kg_accion
                    db.update_row("stock_a_granel", "stock_id", stock_sel, {"kg_saldo": nuevo_saldo_stock})
                    st.success(f"✅ {kg_accion:.1f} kg devueltos a producción como lote {nuevo_lote_id}.")
                    st.rerun()

            else:  # Desechar
                causa_desc = st.text_area("Motivo del desecho", key="granel_motivo_deseche")
                if st.button("🗑️ Confirmar desecho", type="primary"):
                    if not causa_desc.strip():
                        st.error("Escribe el motivo del desecho antes de confirmar.")
                    else:
                        db.update_row("stock_a_granel", "stock_id", stock_sel, {
                            "kg_saldo": kg_disp_stock - kg_accion,
                            "observaciones": f"Desechado: {causa_desc}",
                        })
                        st.success(f"🗑️ {kg_accion:.1f} kg de {tipo_stock} desechados del recipiente {stock_sel}.")
                        st.rerun()

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
    if tab_verificacion is not None:
        with tab_verificacion:
            st.caption(
                "Para cuando un conductor reporta que faltó algo por cargar — registra "
                "si la carga estuvo correcta o no, y queda asociado a quién hizo el despacho."
            )
            sub_registrar, sub_historial = st.tabs(["➕ Registrar verificación", "📊 Historial y conteo de errores"])

            def _nombre_usuario(username_login):
                if not username_login or str(username_login).strip() == "":
                    return "Sin registrar"
                if usuarios_cat.empty or "username" not in usuarios_cat.columns:
                    return str(username_login)
                fila = usuarios_cat[usuarios_cat["username"].astype(str).str.lower() == str(username_login).lower()]
                if fila.empty or not str(fila.iloc[0].get("nombre", "")).strip():
                    return str(username_login)
                return str(fila.iloc[0]["nombre"])

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
                        personal = db.get_df("personal")
                        mapa_personal_nombre = dict(zip(personal["personal_id"], personal["nombre"])) if not personal.empty else {}

                        despachadores_unicos = sorted(
                            d for d in despachos_dia["despachador"].dropna().unique() if d
                        )
                        if not despachadores_unicos:
                            despachadores_unicos = sorted(despachos_dia["usuario"].dropna().unique().tolist())

                        st.markdown(f"**Despachos registrados ese día para este vehículo:** {len(despachos_dia)}")
                        st.dataframe(
                            despachos_dia[[c for c in ["salida_id", "cliente_id", "cantidad", "despachador", "usuario"] if c in despachos_dia.columns]],
                            use_container_width=True, hide_index=True,
                        )

                        def _nombre_desp(pid):
                            return mapa_personal_nombre.get(pid, pid)

                        if len(despachadores_unicos) > 1:
                            st.caption("Más de una persona registró despachos para este vehículo ese día — elige a quién corresponde la verificación.")
                        despachador_sel = st.selectbox(
                            "Despachador responsable", despachadores_unicos,
                            format_func=_nombre_desp, key="verif_despachador",
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
                    personal_hist = db.get_df("personal")
                    mapa_personal_hist = dict(zip(personal_hist["personal_id"], personal_hist["nombre"])) if not personal_hist.empty else {}
                    verif["despachador_nombre"] = verif["despachador"].apply(lambda x: mapa_personal_hist.get(str(x), str(x)) if x else "Sin registrar")

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
