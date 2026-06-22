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
    tab_nueva, tab_disponibles = st.tabs(["Nuevo lote envasado", "Producto terminado disponible"])

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
            columnas_disp = [
                "lote_producto_id", "fecha", "presentacion_id", "pasteurizado", "tapa_id",
                "etiqueta_id", "cantidad_cartones", "liner_id", "unidades_saldo",
            ]
            if ve_costos(rol):
                columnas_disp.append("costo_unitario")
            disponible_df = df[df["unidades_saldo"] > 0]
            st.dataframe(disponible_df[columnas_disp], use_container_width=True)

            st.markdown("##### Kg totales disponibles por producto")
            resumen_kg = disponible_df.copy()
            if not presentaciones.empty:
                resumen_kg = resumen_kg.merge(
                    presentaciones[["presentacion_id", "kg_nominal"]], on="presentacion_id", how="left",
                )
            else:
                resumen_kg["kg_nominal"] = 0
            resumen_kg["kg_nominal"] = pd.to_numeric(resumen_kg["kg_nominal"], errors="coerce").fillna(0)
            if not semielaborados.empty:
                resumen_kg = resumen_kg.merge(
                    semielaborados[["lote_semielaborado_id", "tipo_producto"]],
                    on="lote_semielaborado_id", how="left",
                )
            else:
                resumen_kg["tipo_producto"] = ""
            resumen_kg["tipo_producto"] = resumen_kg["tipo_producto"].fillna("Sin clasificar")
            resumen_kg["kg"] = resumen_kg["unidades_saldo"] * resumen_kg["kg_nominal"]

            por_tipo_kg = resumen_kg.groupby("tipo_producto")["kg"].sum()
            por_tipo_kg = por_tipo_kg[por_tipo_kg > 0]
            if por_tipo_kg.empty:
                st.info("No hay datos suficientes para calcular el total en kg.")
            else:
                cols_kg = st.columns(len(por_tipo_kg))
                for col, (tipo, kg) in zip(cols_kg, por_tipo_kg.items()):
                    col.metric(tipo, f"{kg:,.1f} kg")
