"""
Pasteurizacion y envasado: toma kg de los tanques de semielaborado y los
convierte en producto terminado en la presentacion elegida (0.5, 1, 2, 3.8,
5 kg, etc.). El producto terminado resultante queda disponible para
ingresar al modulo de cuarto frio.
"""
import datetime
import streamlit as st
import pandas as pd
from modules.bodega_envases_insumos import _saldo_actual
from utils.permisos import ve_costos


def _render_nuevo_lote(db, username, rol, semielaborados, presentaciones, turnos, tapas, etiquetas, cartones, liners):
    if semielaborados.empty:
        st.warning("No hay lotes de semielaborado registrados todavía.")
        return
    semielaborados["kg_saldo"] = pd.to_numeric(semielaborados["kg_saldo"], errors="coerce").fillna(0)
    disponibles = semielaborados[semielaborados["kg_saldo"] > 0]
    if disponibles.empty:
        st.warning("No hay saldo disponible en tanques de semielaborado.")
        return
    if presentaciones.empty:
        st.warning("Configura al menos una presentación en Catálogos.")
        return

    fecha = st.date_input("Fecha", value=datetime.date.today(), key="past_fecha")
    if turnos.empty:
        st.warning("Configura al menos un turno en Catálogos → Turnos antes de registrar.")
        return
    turno_id = st.selectbox(
        "Turno", turnos["turno_id"],
        format_func=lambda x: turnos.set_index("turno_id").loc[x, "nombre"],
    )
    lote_semielaborado_id = st.selectbox(
        "Lote de semielaborado",
        disponibles["lote_semielaborado_id"],
        format_func=lambda x: (
            f"{x} — saldo {disponibles.set_index('lote_semielaborado_id').loc[x, 'kg_saldo']:.1f} kg"
        ),
    )
    fila_lote = disponibles.set_index("lote_semielaborado_id").loc[lote_semielaborado_id]
    kg_disponible = float(fila_lote["kg_saldo"])
    costo_unitario_kg = float(fila_lote["costo_unitario_kg"])

    presentacion_id = st.selectbox(
        "Presentación",
        presentaciones["presentacion_id"],
        format_func=lambda x: presentaciones.set_index("presentacion_id").loc[x, "nombre"],
    )
    fila_pres = presentaciones.set_index("presentacion_id").loc[presentacion_id]
    kg_nominal = float(fila_pres["kg_nominal"])
    costo_envase_unitario = float(fila_pres["costo_envase_unitario"])

    pasteurizado = st.checkbox(
        "🔥 Este lote se pasteuriza", value=True,
        help="Desmarca solo para venta de clara líquida cruda sin pasteurizar a clientes que la piden así.",
    )
    if not pasteurizado:
        st.caption("⚠️ Se va a registrar como **sin pasteurizar** — clara/huevo crudo directo del tanque.")

    es_pet = str(fila_pres.get("tipo_envase", "")).strip() == "PET"
    tapa_id = ""
    costo_tapa_unitario = 0.0
    if es_pet:
        if tapas.empty:
            st.warning("Esta presentación es PET pero no hay tapas configuradas en Catálogos → Tapas (PET).")
            return
        tapa_id = st.selectbox(
            "Color de tapa", tapas["tapa_id"],
            format_func=lambda x: tapas.set_index("tapa_id").loc[x, "color"],
        )
        costo_tapa_unitario = float(tapas.set_index("tapa_id").loc[tapa_id, "costo_unitario"])

    if etiquetas.empty:
        st.warning("No hay etiquetas configuradas en Catálogos → Etiquetas.")
        return
    etiqueta_id = st.selectbox(
        "Etiqueta", etiquetas["etiqueta_id"],
        format_func=lambda x: etiquetas.set_index("etiqueta_id").loc[x, "nombre"],
    )
    costo_etiqueta_unitario = float(etiquetas.set_index("etiqueta_id").loc[etiqueta_id, "costo_unitario"])

    kg_usado = st.number_input(
        "Kg a pasteurizar/envasar", min_value=0.0, max_value=kg_disponible, step=0.5,
        key=f"kg_usado_{lote_semielaborado_id}",
    )
    unidades_teoricas = int(kg_usado / kg_nominal) if kg_nominal else 0
    st.caption(f"Unidades teóricas a esa presentación: {unidades_teoricas}")
    unidades_reales = st.number_input(
        "Unidades reales obtenidas", min_value=0, step=1, value=unidades_teoricas
    )
    usa_carton = st.checkbox("📦 Este lote se empaca en cartones (solo para ciertos clientes)")
    carton_id = ""
    cantidad_cartones = 0
    costo_carton_unitario = 0.0
    if usa_carton:
        if cartones.empty:
            st.warning("No hay cartones configurados en Catálogos → Cartones.")
            return
        carton_id = st.selectbox(
            "Tipo de cartón", cartones["carton_id"],
            format_func=lambda x: (
                f"{cartones.set_index('carton_id').loc[x, 'nombre']} "
                f"(capacidad {cartones.set_index('carton_id').loc[x, 'capacidad']:.0f})"
            ),
        )
        capacidad_carton = float(cartones.set_index("carton_id").loc[carton_id, "capacidad"])
        costo_carton_unitario = float(cartones.set_index("carton_id").loc[carton_id, "costo_unitario"])
        sugerido = int(-(-unidades_reales // capacidad_carton)) if capacidad_carton else 0  # redondeo hacia arriba
        cantidad_cartones = st.number_input(
            "Cantidad de cartones usados", min_value=0, step=1, value=sugerido,
        )
        cubiertas = cantidad_cartones * capacidad_carton
        if cubiertas != unidades_reales:
            st.caption(
                f"⚠️ {cantidad_cartones:.0f} cartón(es) × {capacidad_carton:.0f} = "
                f"{cubiertas:.0f} unidades — tienes {unidades_reales:.0f} unidades reales. "
                f"Revisa si está bien (puede haber unidades sueltas sin cartón, está permitido)."
            )

    usa_liner = st.checkbox("🔘 Este lote usa liner de aluminio (solo para ciertos envases)")
    liner_id = ""
    costo_liner_unitario = 0.0
    if usa_liner:
        if liners.empty:
            st.warning("No hay liners configurados en Catálogos → Liners de aluminio.")
            return
        liner_id = st.selectbox(
            "Tipo de liner", liners["liner_id"],
            format_func=lambda x: liners.set_index("liner_id").loc[x, "nombre"],
        )
        costo_liner_unitario = float(liners.set_index("liner_id").loc[liner_id, "costo_unitario"])

    observaciones = st.text_area("Observaciones", "", key="past_obs")

    if st.button("Guardar lote de envasado"):
        saldo_envase_previo = _saldo_actual(
            db.get_df("movimientos_envases_insumos"), "envase", presentacion_id
        )
        if unidades_reales > saldo_envase_previo:
            st.warning(
                f"⚠️ Hay {saldo_envase_previo:.0f} envases de esta presentación en bodega, "
                f"pero estás usando {unidades_reales:.0f}. Se va a guardar igual, pero revisa "
                f"si falta registrar una compra de envases."
            )
        costo_semielaborado = kg_usado * costo_unitario_kg
        costo_envases = unidades_reales * costo_envase_unitario
        costo_tapas = unidades_reales * costo_tapa_unitario if es_pet else 0.0
        costo_etiquetas = unidades_reales * costo_etiqueta_unitario
        costo_cartones = cantidad_cartones * costo_carton_unitario if usa_carton else 0.0
        costo_liners = unidades_reales * costo_liner_unitario if usa_liner else 0.0
        costo_total = (
            costo_semielaborado + costo_envases + costo_tapas
            + costo_etiquetas + costo_cartones + costo_liners
        )
        costo_unitario = costo_total / unidades_reales if unidades_reales > 0 else 0

        lote_producto_id = db.siguiente_id("pasteurizacion_envasado", "PROD", fecha)
        db.append_row("pasteurizacion_envasado", {
            "lote_producto_id": lote_producto_id,
            "fecha": fecha.isoformat(),
            "lote_semielaborado_id": lote_semielaborado_id,
            "presentacion_id": presentacion_id,
            "kg_usado": kg_usado,
            "unidades_teoricas": unidades_teoricas,
            "unidades_reales": unidades_reales,
            "pasteurizado": pasteurizado,
            "costo_semielaborado": costo_semielaborado,
            "costo_envases": costo_envases,
            "tapa_id": tapa_id,
            "costo_tapas": costo_tapas,
            "etiqueta_id": etiqueta_id,
            "costo_etiquetas": costo_etiquetas,
            "carton_id": carton_id,
            "cantidad_cartones": cantidad_cartones,
            "costo_cartones": costo_cartones,
            "liner_id": liner_id,
            "costo_liners": costo_liners,
            "costo_total": costo_total,
            "costo_unitario": costo_unitario,
            "unidades_saldo": unidades_reales,
            "turno": turno_id,
            "usuario": username,
            "observaciones": observaciones,
        })

        db.update_row("produccion_semielaborados", "lote_semielaborado_id", lote_semielaborado_id, {
            "kg_saldo": kg_disponible - kg_usado,
        })

        movimiento_id = db.siguiente_id("movimientos_envases_insumos", "ENV", fecha)
        db.append_row("movimientos_envases_insumos", {
            "movimiento_id": movimiento_id,
            "fecha": fecha.isoformat(),
            "item_tipo": "envase",
            "item_id": presentacion_id,
            "tipo_movimiento": "salida",
            "cantidad": unidades_reales,
            "costo_unitario": costo_envase_unitario,
            "costo_total": costo_envases,
            "modulo_destino": "Pasteurización y envasado",
            "usuario": username,
            "observaciones": lote_producto_id,
        })

        if es_pet:
            saldo_tapa_previo = _saldo_actual(db.get_df("movimientos_envases_insumos"), "tapa", tapa_id)
            if unidades_reales > saldo_tapa_previo:
                st.warning(
                    f"⚠️ Hay {saldo_tapa_previo:.0f} tapas de este color en bodega, pero estás "
                    f"usando {unidades_reales:.0f}. Se va a guardar igual, pero revisa si falta "
                    f"registrar una compra de tapas."
                )
            movimiento_tapa_id = db.siguiente_id("movimientos_envases_insumos", "ENV", fecha)
            db.append_row("movimientos_envases_insumos", {
                "movimiento_id": movimiento_tapa_id,
                "fecha": fecha.isoformat(),
                "item_tipo": "tapa",
                "item_id": tapa_id,
                "tipo_movimiento": "salida",
                "cantidad": unidades_reales,
                "costo_unitario": costo_tapa_unitario,
                "costo_total": costo_tapas,
                "modulo_destino": "Pasteurización y envasado",
                "usuario": username,
                "observaciones": lote_producto_id,
            })

        saldo_etiqueta_previo = _saldo_actual(db.get_df("movimientos_envases_insumos"), "etiqueta", etiqueta_id)
        if unidades_reales > saldo_etiqueta_previo:
            st.warning(
                f"⚠️ Hay {saldo_etiqueta_previo:.0f} etiquetas de este tipo en bodega, pero estás "
                f"usando {unidades_reales:.0f}. Se va a guardar igual, pero revisa si falta "
                f"registrar una compra de etiquetas."
            )
        movimiento_etiqueta_id = db.siguiente_id("movimientos_envases_insumos", "ENV", fecha)
        db.append_row("movimientos_envases_insumos", {
            "movimiento_id": movimiento_etiqueta_id,
            "fecha": fecha.isoformat(),
            "item_tipo": "etiqueta",
            "item_id": etiqueta_id,
            "tipo_movimiento": "salida",
            "cantidad": unidades_reales,
            "costo_unitario": costo_etiqueta_unitario,
            "costo_total": costo_etiquetas,
            "modulo_destino": "Pasteurización y envasado",
            "usuario": username,
            "observaciones": lote_producto_id,
        })

        if usa_carton and cantidad_cartones > 0:
            saldo_carton_previo = _saldo_actual(db.get_df("movimientos_envases_insumos"), "carton", carton_id)
            if cantidad_cartones > saldo_carton_previo:
                st.warning(
                    f"⚠️ Hay {saldo_carton_previo:.0f} cartones de este tipo en bodega, pero "
                    f"estás usando {cantidad_cartones:.0f}. Se va a guardar igual, pero revisa "
                    f"si falta registrar una compra de cartones."
                )
            movimiento_carton_id = db.siguiente_id("movimientos_envases_insumos", "ENV", fecha)
            db.append_row("movimientos_envases_insumos", {
                "movimiento_id": movimiento_carton_id,
                "fecha": fecha.isoformat(),
                "item_tipo": "carton",
                "item_id": carton_id,
                "tipo_movimiento": "salida",
                "cantidad": cantidad_cartones,
                "costo_unitario": costo_carton_unitario,
                "costo_total": costo_cartones,
                "modulo_destino": "Pasteurización y envasado",
                "usuario": username,
                "observaciones": lote_producto_id,
            })

        if usa_liner:
            saldo_liner_previo = _saldo_actual(db.get_df("movimientos_envases_insumos"), "liner", liner_id)
            if unidades_reales > saldo_liner_previo:
                st.warning(
                    f"⚠️ Hay {saldo_liner_previo:.0f} liners de este tipo en bodega, pero estás "
                    f"usando {unidades_reales:.0f}. Se va a guardar igual, pero revisa si falta "
                    f"registrar una compra de liners."
                )
            movimiento_liner_id = db.siguiente_id("movimientos_envases_insumos", "ENV", fecha)
            db.append_row("movimientos_envases_insumos", {
                "movimiento_id": movimiento_liner_id,
                "fecha": fecha.isoformat(),
                "item_tipo": "liner",
                "item_id": liner_id,
                "tipo_movimiento": "salida",
                "cantidad": unidades_reales,
                "costo_unitario": costo_liner_unitario,
                "costo_total": costo_liners,
                "modulo_destino": "Pasteurización y envasado",
                "usuario": username,
                "observaciones": lote_producto_id,
            })

        if ve_costos(rol):
            st.success(f"Lote {lote_producto_id} guardado — costo unitario {costo_unitario:,.2f}")
        else:
            st.success(f"Lote {lote_producto_id} guardado.")



def render(db, username, rol):
    st.title("Pasteurización y envasado")
    tab_nueva, tab_disponibles, tab_granel, tab_historial = st.tabs(["Nuevo lote envasado", "Producto terminado disponible", "📦 Pasar a granel", "📋 Historial"])

    semielaborados = db.get_df("produccion_semielaborados")
    presentaciones = db.get_df("presentaciones")
    turnos = db.get_df("turnos")
    tapas = db.get_df("tapas")
    etiquetas = db.get_df("etiquetas")
    cartones = db.get_df("cartones")
    liners = db.get_df("liners")

    with tab_nueva:
        _render_nuevo_lote(db, username, rol, semielaborados, presentaciones, turnos, tapas, etiquetas, cartones, liners)

    with tab_disponibles:
        df = db.get_df("pasteurizacion_envasado")
        if df.empty:
            st.info("Todavía no hay lotes de producto terminado.")
        else:
            df["unidades_saldo"] = pd.to_numeric(df["unidades_saldo"], errors="coerce").fillna(0)
            disponible_df = df[df["unidades_saldo"] > 0].copy()
            disponible_df["estado"] = disponible_df["pasteurizado"].astype(str).str.upper().isin(
                ["TRUE", "1", "SI", "SÍ"]
            ).map({True: "✅ Pasteurizado", False: "🔴 Sin pasteurizar"})
            if not semielaborados.empty:
                disponible_df = disponible_df.merge(
                    semielaborados[["lote_semielaborado_id", "tipo_producto"]],
                    on="lote_semielaborado_id", how="left",
                )
                disponible_df["tipo_producto"] = disponible_df["tipo_producto"].fillna("—")
            else:
                disponible_df["tipo_producto"] = "—"
            disponible_df = disponible_df.rename(columns={"lote_semielaborado_id": "lote_origen"})
            columnas_disp = [
                "lote_producto_id", "fecha", "lote_origen", "tipo_producto",
                "presentacion_id", "estado", "etiqueta_id", "unidades_saldo",
            ]
            if ve_costos(rol):
                columnas_disp.append("costo_unitario")
            st.dataframe(disponible_df[[c for c in columnas_disp if c in disponible_df.columns]], use_container_width=True)

            st.markdown("##### Kg totales disponibles por producto")
            resumen_kg = disponible_df.copy()
            if not presentaciones.empty:
                resumen_kg = resumen_kg.merge(
                    presentaciones[["presentacion_id", "kg_nominal"]], on="presentacion_id", how="left",
                )
            else:
                resumen_kg["kg_nominal"] = 0
            resumen_kg["kg_nominal"] = pd.to_numeric(resumen_kg["kg_nominal"], errors="coerce").fillna(0)
            # tipo_producto ya viene en disponible_df del merge anterior — no re-mergear
            if "tipo_producto" not in resumen_kg.columns:
                resumen_kg["tipo_producto"] = "Sin clasificar"
            resumen_kg["tipo_producto"] = resumen_kg["tipo_producto"].fillna("Sin clasificar")
            resumen_kg["kg"] = resumen_kg["unidades_saldo"] * resumen_kg["kg_nominal"]
            resumen_kg["pasteurizado_bool"] = resumen_kg["pasteurizado"].astype(str).str.upper().isin(["TRUE", "1", "SI", "SÍ"])

            def _nombre_producto(tipo, past):
                nombre = {"Huevo entero": "Huevo entero", "Clara": "Clara", "Yema": "Yema"}.get(tipo, tipo)
                return f"{nombre} pasteurizado{'a' if nombre in ('Clara','Yema') else ''}" if past else f"{nombre} sin pasteurizar"

            resumen_kg["nombre_producto"] = resumen_kg.apply(
                lambda r: _nombre_producto(r["tipo_producto"], r["pasteurizado_bool"]), axis=1
            )
            por_tipo_kg = resumen_kg.groupby("nombre_producto")["kg"].sum()
            por_tipo_kg = por_tipo_kg[por_tipo_kg > 0]
            if por_tipo_kg.empty:
                st.info("No hay datos suficientes para calcular el total en kg.")
            else:
                cols_kg = st.columns(len(por_tipo_kg))
                for col, (nombre, kg) in zip(cols_kg, por_tipo_kg.items()):
                    col.metric(nombre, f"{kg:,.1f} kg")

    with tab_granel:
        st.caption(
            "Traslada kg que no se van a envasar ahora a un recipiente de acero inoxidable "
            "en cuarto frío — quedan como stock a granel para el siguiente turno (máx. 2 días)."
        )
        semi = db.get_df("produccion_semielaborados")
        if semi.empty:
            st.info("No hay lotes de semielaborado todavía.")
        else:
            semi["kg_saldo"] = pd.to_numeric(semi["kg_saldo"], errors="coerce").fillna(0)
            semi_disp = semi[semi["kg_saldo"] > 0]
            if semi_disp.empty:
                st.info("No hay kg disponibles en tanques para trasladar.")
            else:
                lote_sel = st.selectbox(
                    "Lote semielaborado a trasladar",
                    semi_disp["lote_semielaborado_id"],
                    format_func=lambda x: (
                        f"{x} — {semi_disp.set_index('lote_semielaborado_id').loc[x, 'tipo_producto']} "
                        f"({semi_disp.set_index('lote_semielaborado_id').loc[x, 'kg_saldo']:.1f} kg disponibles)"
                    ),
                    key="granel_lote_sel",
                )
                fila_lote = semi_disp.set_index("lote_semielaborado_id").loc[lote_sel]
                saldo_disp = float(fila_lote["kg_saldo"])
                if saldo_disp <= 0:
                    st.warning("Este lote no tiene kg disponibles para trasladar.")
                else:
                    tipo_producto_gr = str(fila_lote["tipo_producto"])

                    kg_a_trasladar = st.number_input(
                        f"Kg a trasladar al recipiente (máx {saldo_disp:.1f} kg)",
                        min_value=0.1, max_value=saldo_disp, value=saldo_disp, step=0.1,
                        key="granel_kg",
                    )
                    obs_gr = st.text_input("Observaciones (opcional)", "", key="granel_obs")

                    st.info(
                        f"Se guardarán **{kg_a_trasladar:.1f} kg** de **{tipo_producto_gr}** "
                        f"en recipiente de acero inoxidable. Tendrán máximo **2 días** para envasar o desechar."
                    )

                    if st.button("📦 Trasladar a recipiente", type="primary", use_container_width=True):
                        stock_id = db.siguiente_id("stock_a_granel", "GR", fecha_gr)
                        db.append_row("stock_a_granel", {
                            "stock_id": stock_id,
                            "fecha_entrada": fecha_gr.isoformat(),
                            "lote_origen": lote_sel,
                            "tipo_producto": tipo_producto_gr,
                            "kg_inicial": kg_a_trasladar,
                            "kg_saldo": kg_a_trasladar,
                            "usuario": username,
                            "observaciones": obs_gr,
                        })
                        db.update_row("produccion_semielaborados", "lote_semielaborado_id", lote_sel, {
                            "kg_saldo": saldo_disp - kg_a_trasladar,
                        })
                    st.success(f"✅ {stock_id}: {kg_a_trasladar:.1f} kg de {tipo_producto_gr} trasladados a recipiente.")
                    st.rerun()

    with tab_historial:
        df_hist = db.get_df("pasteurizacion_envasado")
        if df_hist.empty:
            st.info("Todavía no hay lotes registrados.")
        else:
            df_hist["unidades_saldo"] = pd.to_numeric(df_hist["unidades_saldo"], errors="coerce").fillna(0)
            df_hist["unidades_reales"] = pd.to_numeric(df_hist["unidades_reales"], errors="coerce").fillna(0)
            df_hist["estado"] = df_hist["pasteurizado"].astype(str).str.upper().isin(
                ["TRUE", "1", "SI", "SÍ"]
            ).map({True: "✅ Pasteurizado", False: "🔴 Sin pasteurizar"})
            if not semielaborados.empty:
                df_hist = df_hist.merge(
                    semielaborados[["lote_semielaborado_id", "tipo_producto"]],
                    on="lote_semielaborado_id", how="left",
                )
                df_hist["tipo_producto"] = df_hist["tipo_producto"].fillna("—")
            else:
                df_hist["tipo_producto"] = "—"
            df_hist = df_hist.rename(columns={"lote_semielaborado_id": "lote_origen"})
            df_hist["unidades_despachadas"] = df_hist["unidades_reales"] - df_hist["unidades_saldo"]
            df_hist["saldo_estado"] = df_hist["unidades_saldo"].apply(
                lambda s: "✅ Despachado completo" if s == 0 else f"🟡 {int(s)} en stock"
            )
            c1, c2 = st.columns(2)
            filtro_tipo = c1.selectbox("Tipo de producto", ["Todos"] + sorted(df_hist["tipo_producto"].unique().tolist()), key="hist_tipo")
            filtro_estado = c2.selectbox("Estado", ["Todos", "En stock", "Despachado completo"], key="hist_estado")
            df_mostrar = df_hist.copy()
            if filtro_tipo != "Todos":
                df_mostrar = df_mostrar[df_mostrar["tipo_producto"] == filtro_tipo]
            if filtro_estado == "En stock":
                df_mostrar = df_mostrar[df_mostrar["unidades_saldo"] > 0]
            elif filtro_estado == "Despachado completo":
                df_mostrar = df_mostrar[df_mostrar["unidades_saldo"] == 0]
            cols_hist = ["lote_producto_id", "fecha", "lote_origen", "tipo_producto",
                         "presentacion_id", "estado", "unidades_reales", "unidades_despachadas", "saldo_estado"]
            st.dataframe(
                df_mostrar[[c for c in cols_hist if c in df_mostrar.columns]].sort_values("fecha", ascending=False),
                use_container_width=True, hide_index=True,
            )
            st.caption(
                f"Total: {len(df_mostrar)} lotes | "
                f"En stock: {(df_mostrar['unidades_saldo'] > 0).sum()} | "
                f"Despachados: {(df_mostrar['unidades_saldo'] == 0).sum()}"
            )
