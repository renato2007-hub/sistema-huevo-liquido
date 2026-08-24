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
from utils.costing import costo_ponderado
from utils.permisos import ve_costos


def _render_nuevo_lote(db, username, rol, semielaborados, presentaciones, turnos, tapas, etiquetas, cartones, liners):
    if semielaborados.empty:
        st.warning("No hay lotes de semielaborado registrados todavía.")
        return
    semielaborados["kg_saldo"] = pd.to_numeric(semielaborados["kg_saldo"], errors="coerce").fillna(0)
    # >= 0.1 (no > 0): ignora residuos de redondeo de operaciones anteriores.
    disponibles = semielaborados[semielaborados["kg_saldo"] >= 0.1].copy()
    disponibles["tipo_producto"] = disponibles["tipo_producto"].astype(str)
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
    tipos_disponibles = sorted(disponibles["tipo_producto"].unique().tolist())
    tipo_producto_sel = st.selectbox("Tipo de producto", tipos_disponibles)
    pool = disponibles[disponibles["tipo_producto"] == tipo_producto_sel].copy()
    kg_disponible = round(float(pool["kg_saldo"].sum()), 2)
    st.info(
        f"📦 **{kg_disponible:.2f} kg disponibles** de {tipo_producto_sel} en tanques "
        f"({len(pool)} lote(s) — se descontarán FIFO por fecha de producción al guardar)."
    )

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
        "Kg a pasteurizar/envasar", min_value=0.0, max_value=kg_disponible, step=0.01,
        value=kg_disponible, key=f"kg_usado_{tipo_producto_sel}",
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

    # Vencimiento según tipo de producto: Clara pasteurizada 20 días,
    # Huevo entero y Yema pasteurizados 15 días.
    tipo_producto_lote = tipo_producto_sel
    dias_vencimiento = 20 if "clara" in tipo_producto_lote.lower() else 15
    fecha_vencimiento = st.date_input(
        "Fecha de vencimiento del producto",
        value=fecha + datetime.timedelta(days=dias_vencimiento),
        key=f"past_venc_{tipo_producto_sel}_{fecha}",
        help=(
            f"Sugerido: {dias_vencimiento} días para {tipo_producto_lote or 'este producto'} "
            f"(Clara: 20 días — Huevo y Yema: 15 días). Puedes ajustarla si hace falta. "
            f"El lote ingresa automáticamente a cuarto frío con esta fecha."
        ),
    )

    observaciones = st.text_area("Observaciones", "", key="past_obs")

    if st.button("Guardar lote de envasado"):
        if kg_usado <= 0:
            st.error("Ingresa una cantidad de kg mayor a cero.")
            return
        # ── Asignación FIFO por fecha de producción entre los tanques del pool ──
        pool_fifo = pool.copy()
        pool_fifo["fecha_dt"] = pd.to_datetime(pool_fifo["fecha"], errors="coerce")
        pool_fifo = pool_fifo.sort_values("fecha_dt", na_position="last")

        detalle_tanques = []
        restante = kg_usado
        for _, tanque in pool_fifo.iterrows():
            if restante <= 0:
                break
            saldo = float(tanque["kg_saldo"])
            if saldo <= 0:
                continue
            tomar = min(saldo, restante)
            detalle_tanques.append({
                "lote_semielaborado_id": tanque["lote_semielaborado_id"],
                "cantidad_a_tomar": tomar,
                "costo_cubeta": float(pd.to_numeric(tanque.get("costo_unitario_kg", 0), errors="coerce") or 0),
            })
            restante -= tomar

        if restante > 0.01:
            st.error(
                f"No hay suficientes kg en tanques de {tipo_producto_sel} para completar el "
                f"lote. Faltaron {restante:.2f} kg. Ajusta antes de guardar."
            )
            return

        lote_semielaborado_id = detalle_tanques[0]["lote_semielaborado_id"]
        costo_unitario_kg = costo_ponderado(detalle_tanques)

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
            # El lote ingresa completo y de inmediato a cuarto frío, por eso
            # el saldo "pendiente de ingresar" queda en 0 desde el inicio.
            "unidades_saldo": 0,
            "turno": turno_id,
            "usuario": username,
            "observaciones": observaciones,
        })

        for d in detalle_tanques:
            cantidad = float(d["cantidad_a_tomar"])
            costo_unit = float(d["costo_cubeta"])
            consumo_id = db.siguiente_id("consumo_semi_pasteurizacion", "CSP", fecha)
            db.append_row("consumo_semi_pasteurizacion", {
                "consumo_id": consumo_id,
                "fecha": fecha.isoformat(),
                "lote_semielaborado_id": d["lote_semielaborado_id"],
                "lote_producto_id": lote_producto_id,
                "kg_tomado": cantidad,
                "costo_unitario_aplicado": costo_unit,
                "costo_total_aplicado": cantidad * costo_unit,
                "usuario": username,
            })
            fila_tanque = disponibles[disponibles["lote_semielaborado_id"] == d["lote_semielaborado_id"]]
            if not fila_tanque.empty:
                saldo_actual = float(fila_tanque.iloc[0]["kg_saldo"])
                db.update_row("produccion_semielaborados", "lote_semielaborado_id", d["lote_semielaborado_id"], {
                    "kg_saldo": round(saldo_actual - cantidad, 2),
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

        # ── Ingreso automático a cuarto frío ──────────────────────────────
        entrada_id = db.siguiente_id("cuarto_frio_entradas", "CF", fecha)
        db.append_row("cuarto_frio_entradas", {
            "entrada_id": entrada_id,
            "fecha": fecha.isoformat(),
            "lote_producto_id": lote_producto_id,
            "presentacion_id": presentacion_id,
            "cantidad": unidades_reales,
            "costo_unitario": costo_unitario,
            "fecha_vencimiento": fecha_vencimiento.isoformat(),
            "saldo": unidades_reales,
            "usuario": username,
        })

        if ve_costos(rol):
            st.success(
                f"Lote {lote_producto_id} guardado — costo unitario {costo_unitario:,.2f} — "
                f"ingresado automáticamente a cuarto frío ({entrada_id})."
            )
        else:
            st.success(
                f"Lote {lote_producto_id} guardado e ingresado automáticamente "
                f"a cuarto frío ({entrada_id})."
            )



def render(db, username, rol):
    st.title("Pasteurización y envasado")
    # La pestaña "Producto terminado disponible" se eliminó: con el ingreso
    # automático a cuarto frío, esa información vive en Cuarto frío → Inventario actual.
    tab_nueva, tab_granel, tab_historial = st.tabs(
        ["Nuevo lote envasado", "📦 Pasar a granel", "📋 Historial"]
    )

    semielaborados = db.get_df("produccion_semielaborados")
    presentaciones = db.get_df("presentaciones")

    # Saldo en cuarto frío por lote de producto: como los lotes nuevos ingresan
    # automáticamente a cuarto frío (unidades_saldo = 0), el saldo real disponible
    # de un lote es: unidades_saldo (legado, aún no ingresado) + saldo en cuarto frío.
    cf_entradas = db.get_df("cuarto_frio_entradas")
    if cf_entradas.empty or "lote_producto_id" not in cf_entradas.columns:
        saldo_cf_por_lote = {}
    else:
        cf_entradas["saldo"] = pd.to_numeric(cf_entradas["saldo"], errors="coerce").fillna(0)
        saldo_cf_por_lote = cf_entradas.groupby("lote_producto_id")["saldo"].sum().to_dict()

    turnos = db.get_df("turnos")
    tapas = db.get_df("tapas")
    etiquetas = db.get_df("etiquetas")
    cartones = db.get_df("cartones")
    liners = db.get_df("liners")

    with tab_nueva:
        _render_nuevo_lote(db, username, rol, semielaborados, presentaciones, turnos, tapas, etiquetas, cartones, liners)

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
            semi_disp = semi[semi["kg_saldo"] >= 0.1]
            if semi_disp.empty:
                st.info("No hay kg disponibles en tanques para trasladar.")
            else:
                lote_sel = st.selectbox(
                    "Lote semielaborado a trasladar",
                    semi_disp["lote_semielaborado_id"],
                    format_func=lambda x: (
                        f"{x} — {semi_disp.set_index('lote_semielaborado_id').loc[x, 'tipo_producto']} "
                        f"({semi_disp.set_index('lote_semielaborado_id').loc[x, 'kg_saldo']:.2f} kg disponibles)"
                    ),
                    key="granel_lote_sel",
                )
                fila_lote = semi_disp.set_index("lote_semielaborado_id").loc[lote_sel]
                saldo_disp = round(float(fila_lote["kg_saldo"]), 2)
                tipo_producto_gr = str(fila_lote["tipo_producto"])

                if saldo_disp <= 0:
                    st.warning("⚠️ Este lote tiene saldo 0 kg — no hay nada disponible para trasladar.")
                else:
                    kg_a_trasladar = st.number_input(
                        f"Kg a trasladar al recipiente (máx {saldo_disp:.2f} kg)",
                        min_value=0.0, max_value=float(saldo_disp), value=float(saldo_disp), step=0.01,
                        key=f"granel_kg_{lote_sel}",
                    )
                    fecha_gr = st.date_input("Fecha de traslado", value=datetime.date.today(), key="granel_fecha")
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
                            "kg_saldo": round(saldo_disp - kg_a_trasladar, 2),
                        })
                        st.success(f"✅ {stock_id}: {kg_a_trasladar:.1f} kg de {tipo_producto_gr} trasladados a recipiente.")
                        st.rerun()

    with tab_historial:
        df_hist = db.get_df("pasteurizacion_envasado")
        if df_hist.empty:
            st.info("Todavía no hay lotes registrados.")
        else:
            df_hist["unidades_saldo"] = pd.to_numeric(df_hist["unidades_saldo"], errors="coerce").fillna(0)
            df_hist["unidades_saldo"] = (
                df_hist["unidades_saldo"]
                + df_hist["lote_producto_id"].map(saldo_cf_por_lote).fillna(0)
            )
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

            # Tanques de origen reales de cada lote (puede ser más de uno: pool FIFO
            # multi-tanque). Se incluye siempre el de referencia (lote_origen) por si
            # el lote se creó antes de este cambio y no tiene detalle.
            consumo_semi = db.get_df("consumo_semi_pasteurizacion")
            origenes_por_lote = {}
            if not consumo_semi.empty:
                for lpid, grupo in consumo_semi.groupby("lote_producto_id")["lote_semielaborado_id"]:
                    origenes_por_lote[lpid] = set(grupo.unique().tolist())
            for _, fila in df_hist[["lote_producto_id", "lote_origen"]].iterrows():
                origenes_por_lote.setdefault(fila["lote_producto_id"], set()).add(fila["lote_origen"])

            c1, c2 = st.columns(2)
            filtro_tipo = c1.selectbox("Tipo de producto", ["Todos"] + sorted(df_hist["tipo_producto"].unique().tolist()), key="hist_tipo")
            filtro_estado = c2.selectbox("Estado", ["Todos", "En stock", "Despachado completo"], key="hist_estado")
            c3, c4, c5 = st.columns(3)
            desde = c3.date_input("Desde", value=datetime.date.today() - datetime.timedelta(days=30), key="hist_pe_desde")
            hasta = c4.date_input("Hasta", value=datetime.date.today(), key="hist_pe_hasta")
            lotes_origen = ["Todos"] + sorted({l for tanques in origenes_por_lote.values() for l in tanques})
            filtro_origen = c5.selectbox("Lote de origen", lotes_origen, key="hist_pe_origen")
            df_mostrar = df_hist.copy()
            if filtro_tipo != "Todos":
                df_mostrar = df_mostrar[df_mostrar["tipo_producto"] == filtro_tipo]
            if filtro_estado == "En stock":
                df_mostrar = df_mostrar[df_mostrar["unidades_saldo"] > 0]
            elif filtro_estado == "Despachado completo":
                df_mostrar = df_mostrar[df_mostrar["unidades_saldo"] == 0]
            df_mostrar = df_mostrar[
                (df_mostrar["fecha"].astype(str) >= desde.isoformat()) &
                (df_mostrar["fecha"].astype(str) <= hasta.isoformat())
            ]
            if filtro_origen != "Todos":
                df_mostrar = df_mostrar[df_mostrar["lote_producto_id"].map(
                    lambda lpid: filtro_origen in origenes_por_lote.get(lpid, set())
                )]
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
