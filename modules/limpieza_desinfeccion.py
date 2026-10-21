"""
Limpieza y desinfeccion: registro de saneamiento de la planta -- lavado de
huevos, pasteurizador, equipos, gavetas, pisos, etc. -- separado del proceso
productivo. Cuantifica agua e insumos usados por area y deja constancia de
verificacion (util para auditorias BPM/HACCP). Los insumos consumidos aqui
descuentan del mismo inventario de Bodega de envases e insumos.
"""
import datetime
import unicodedata
import streamlit as st
import pandas as pd
from utils.permisos import ve_costos, es_admin
from utils.bitacora import log_cambio

# Una limpieza normal usa gramos o pocos kg de cada insumo. Cantidades por
# encima de esto casi siempre son un error de unidad (g o mg en vez de kg),
# asi que se pide confirmacion antes de guardar.
LIMITE_KG_SOSPECHOSO = 10.0


def render(db, username, rol):
    st.title("🧽 Limpieza y desinfección")
    tab_nueva, tab_historial, tab_resumen, tab_corregir = st.tabs(
        ["➕ Registrar limpieza", "📋 Historial", "📊 Resumen por área", "✏️ Corregir / eliminar"]
    )

    areas = db.get_df("areas_limpieza")
    insumos = db.get_df("insumos")
    personal = db.get_df("personal")
    turnos = db.get_df("turnos")

    # ======================== REGISTRAR LIMPIEZA ========================
    with tab_nueva:
        if areas.empty:
            st.warning(
                "Configura al menos un área en Catálogos → Áreas de limpieza "
                "antes de registrar (ej. Lavado de huevos, Pasteurizador, Pisos)."
            )
            return

        if turnos.empty:
            st.warning("Configura al menos un turno en Catálogos → Turnos antes de registrar.")
            return

        with st.container(border=True):
            st.markdown("##### 📋 Datos generales")
            c1, c2, c3, c4 = st.columns(4)
            fecha = c1.date_input("Fecha", value=datetime.date.today(), key="limp_fecha")
            area_id = c2.selectbox(
                "Área / equipo limpiado", areas["area_id"],
                format_func=lambda x: areas.set_index("area_id").loc[x, "nombre"],
            )
            tipo_limpieza = c3.selectbox("Tipo", ["Limpieza", "Desinfección", "Limpieza y desinfección"])
            turno_id = c4.selectbox(
                "Turno", turnos["turno_id"],
                format_func=lambda x: turnos.set_index("turno_id").loc[x, "nombre"],
            )

        st.write("")

        with st.container(border=True):
            st.markdown("##### 💧 Agua e insumos usados")
            agua_litros = st.number_input("Agua usada (litros)", min_value=0.0, step=1.0)
            st.caption("Insumos usados — deja la tabla vacía si esta limpieza fue solo con agua")
            opciones_insumo_nombres = list(insumos["nombre"]) if not insumos.empty else []
            mapa_nombre_a_insumo_id = dict(zip(insumos["nombre"], insumos["insumo_id"])) if not insumos.empty else {}
            df_insumos_input = st.data_editor(
                pd.DataFrame({"insumo_id": pd.Series(dtype="object"), "cantidad": pd.Series(dtype="float")}),
                num_rows="dynamic", use_container_width=True, hide_index=True,
                key=f"editor_limp_insumos_{area_id}_{fecha}",
                column_config={
                    "insumo_id": st.column_config.SelectboxColumn("Insumo", options=opciones_insumo_nombres),
                    "cantidad": st.column_config.NumberColumn("Cantidad (kg)", min_value=0.0, step=0.01, format="%.2f"),
                },
            )
            cant_max = pd.to_numeric(df_insumos_input["cantidad"], errors="coerce").max()
            confirma_cantidad_alta = True
            if pd.notna(cant_max) and cant_max > LIMITE_KG_SOSPECHOSO:
                st.warning(
                    f"⚠️ Estás registrando **{cant_max:,.2f} kg** de un insumo. Las cantidades van en "
                    "**kilogramos** — si mediste en gramos, divide para 1.000 (ej. 40 g = 0.04 kg)."
                )
                confirma_cantidad_alta = st.checkbox(
                    "Confirmo que la cantidad está en kg y es correcta", key="limp_confirma_kg",
                )

        st.write("")

        with st.container(border=True):
            st.markdown("##### ✅ Responsable y verificación")
            c1, c2 = st.columns([2, 1])
            responsable_id = ""
            with c1:
                if not personal.empty:
                    opciones_resp = [""] + list(personal["personal_id"])
                    responsable_id = st.selectbox(
                        "Responsable (opcional)", opciones_resp,
                        format_func=lambda x: "—" if x == "" else personal.set_index("personal_id").loc[x, "nombre"],
                    )
                else:
                    st.caption("No hay personal configurado en Catálogos todavía.")
            with c2:
                st.write("")
                st.write("")
                verificado = st.checkbox("✅ Verificado / inspeccionado OK")
            observaciones = st.text_area("Observaciones", "", key="limp_obs")

        st.write("")
        guardar = st.button("💾 Guardar registro de limpieza", use_container_width=True, type="primary")

        if guardar and not confirma_cantidad_alta:
            st.error("Revisa la cantidad de insumos (¿está en kg?) o confirma que es correcta antes de guardar.")
        elif guardar:
            detalle_insumos = []
            costo_insumos_total = 0.0
            for _, fila in df_insumos_input.iterrows():
                if pd.isna(fila.get("insumo_id")) or not fila.get("insumo_id"):
                    continue
                insumo_id_real = mapa_nombre_a_insumo_id.get(fila["insumo_id"], fila["insumo_id"])
                costo_unit = float(insumos.set_index("insumo_id").loc[insumo_id_real, "costo_unitario"])
                cant = round(float(fila["cantidad"]), 2) if pd.notna(fila.get("cantidad")) else 0.0
                costo_insumos_total += costo_unit * cant
                detalle_insumos.append({
                    "insumo_id": insumo_id_real, "cantidad": cant, "costo_calculado": costo_unit * cant,
                })

            if agua_litros <= 0 and not detalle_insumos:
                st.error("Registra al menos el agua usada o algún insumo.")
                return

            limpieza_id = db.siguiente_id("limpieza_desinfeccion", "LIMP", fecha)

            for di in detalle_insumos:
                detalle_id = db.siguiente_id("limpieza_insumos", "LI", fecha)
                db.append_row("limpieza_insumos", {
                    "detalle_id": detalle_id,
                    "limpieza_id": limpieza_id,
                    **di,
                })
                movimiento_id = db.siguiente_id("movimientos_envases_insumos", "ENV", fecha)
                db.append_row("movimientos_envases_insumos", {
                    "movimiento_id": movimiento_id,
                    "fecha": fecha.isoformat(),
                    "item_tipo": "insumo",
                    "item_id": di["insumo_id"],
                    "tipo_movimiento": "salida",
                    "cantidad": di["cantidad"],
                    "costo_unitario": di["costo_calculado"] / di["cantidad"] if di["cantidad"] else 0,
                    "costo_total": di["costo_calculado"],
                    "modulo_destino": "Limpieza y desinfección",
                    "usuario": username,
                    "observaciones": limpieza_id,
                })

            db.append_row("limpieza_desinfeccion", {
                "limpieza_id": limpieza_id,
                "fecha": fecha.isoformat(),
                "area_id": area_id,
                "tipo_limpieza": tipo_limpieza,
                "agua_litros": agua_litros,
                "costo_insumos": costo_insumos_total,
                "costo_total": costo_insumos_total,
                "personal_id": responsable_id,
                "turno": turno_id,
                "verificado": verificado,
                "usuario": username,
                "observaciones": observaciones,
            })

            if ve_costos(rol):
                st.success(f"✅ Registro {limpieza_id} guardado — costo insumos {costo_insumos_total:,.2f}")
            else:
                st.success(f"✅ Registro {limpieza_id} guardado.")

    # ======================== HISTORIAL ========================
    with tab_historial:
        df = db.get_df("limpieza_desinfeccion")
        if df.empty:
            st.info("Todavía no hay registros de limpieza.")
        else:
            if not areas.empty:
                df = df.merge(
                    areas[["area_id", "nombre"]].rename(columns={"nombre": "area_nombre"}),
                    on="area_id", how="left",
                )
            else:
                df["area_nombre"] = df["area_id"]

            df["agua_litros"] = pd.to_numeric(df["agua_litros"], errors="coerce").fillna(0)

            hoy = datetime.date.today()
            f1, f2, f3 = st.columns(3)
            desde = f1.date_input("Desde", value=hoy - datetime.timedelta(days=30), key="limp_hist_desde")
            hasta = f2.date_input("Hasta", value=hoy, key="limp_hist_hasta")
            filtro_area = f3.selectbox(
                "Filtrar por área", ["Todas"] + sorted(df["area_nombre"].dropna().unique().tolist()),
            )
            fechas = pd.to_datetime(df["fecha"], errors="coerce").dt.date
            df_mostrar = df[(fechas >= desde) & (fechas <= hasta)]
            if filtro_area != "Todas":
                df_mostrar = df_mostrar[df_mostrar["area_nombre"] == filtro_area]

            if df_mostrar.empty:
                st.info("No hay registros de limpieza con esos filtros.")
            else:
                # Una columna por insumo con los kg usados en cada limpieza
                # (sale de limpieza_insumos, el detalle de cada registro).
                detalle = db.get_df("limpieza_insumos")
                columnas_insumos = []
                if not detalle.empty:
                    detalle = detalle[detalle["limpieza_id"].isin(df_mostrar["limpieza_id"])].copy()
                if not detalle.empty:
                    detalle["cantidad"] = pd.to_numeric(detalle["cantidad"], errors="coerce").fillna(0)
                    mapa_insumo = (
                        dict(zip(insumos["insumo_id"], insumos["nombre"])) if not insumos.empty else {}
                    )
                    detalle["insumo"] = (
                        detalle["insumo_id"].map(mapa_insumo).fillna(detalle["insumo_id"]) + " (kg)"
                    )
                    pivote = detalle.pivot_table(
                        index="limpieza_id", columns="insumo", values="cantidad", aggfunc="sum",
                    ).round(2)
                    # orden alfabetico sin que la tilde mande "Ácido" al final
                    columnas_insumos = sorted(
                        pivote.columns,
                        key=lambda c: unicodedata.normalize("NFD", c).encode("ascii", "ignore").decode().lower(),
                    )
                    df_mostrar = df_mostrar.merge(pivote, left_on="limpieza_id", right_index=True, how="left")

                c1, c2, c3 = st.columns(3)
                with c1.container(border=True):
                    st.metric("Registros en el período", f"{len(df_mostrar):,}")
                with c2.container(border=True):
                    st.metric("Agua usada", f"{df_mostrar['agua_litros'].sum():,.0f} L")
                with c3.container(border=True):
                    verificados = df_mostrar["verificado"].astype(str).str.upper().eq("TRUE").sum()
                    st.metric("Verificados", f"{verificados} de {len(df_mostrar)}")

                if columnas_insumos:
                    totales = df_mostrar[columnas_insumos].sum()
                    st.caption(
                        "🧴 **Insumos usados en el período:** "
                        + " · ".join(f"{c.replace(' (kg)', '')}: **{v:,.2f} kg**" for c, v in totales.items())
                    )

                columnas_base = ["fecha", "area_nombre", "tipo_limpieza", "agua_litros"]
                columnas_base += columnas_insumos
                if ve_costos(rol):
                    columnas_base.append("costo_insumos")
                columnas_base += ["verificado", "observaciones"]
                columnas = [c for c in columnas_base if c in df_mostrar.columns]
                with st.container(border=True):
                    st.dataframe(
                        df_mostrar[columnas].sort_values("fecha", ascending=False).rename(columns={
                            "fecha": "Fecha", "area_nombre": "Área", "tipo_limpieza": "Tipo",
                            "agua_litros": "Agua (L)", "costo_insumos": "Costo insumos",
                            "verificado": "Verificado", "observaciones": "Observaciones",
                        }),
                        use_container_width=True, hide_index=True,
                        column_config={
                            c: st.column_config.NumberColumn(format="%.2f") for c in columnas_insumos
                        },
                    )

    # ======================== RESUMEN POR AREA ========================
    with tab_resumen:
        df = db.get_df("limpieza_desinfeccion")
        if df.empty:
            st.info("No hay datos todavía.")
        else:
            df["agua_litros"] = pd.to_numeric(df["agua_litros"], errors="coerce").fillna(0)
            df["costo_insumos"] = pd.to_numeric(df["costo_insumos"], errors="coerce").fillna(0)
            if not areas.empty:
                df = df.merge(
                    areas[["area_id", "nombre"]].rename(columns={"nombre": "area_nombre"}),
                    on="area_id", how="left",
                )
            else:
                df["area_nombre"] = df["area_id"]

            resumen = df.groupby("area_nombre").agg(
                agua_total_litros=("agua_litros", "sum"),
                costo_total_insumos=("costo_insumos", "sum"),
                registros=("limpieza_id", "count"),
            ).reset_index()

            if ve_costos(rol):
                c1, c2 = st.columns(2)
                with c1.container(border=True):
                    st.metric("💧 Agua total usada (todas las áreas)", f"{df['agua_litros'].sum():,.0f} L")
                with c2.container(border=True):
                    st.metric("🧴 Costo total en insumos de limpieza", f"{df['costo_insumos'].sum():,.2f}")
            else:
                with st.container(border=True):
                    st.metric("💧 Agua total usada (todas las áreas)", f"{df['agua_litros'].sum():,.0f} L")

            st.write("")
            with st.container(border=True):
                st.markdown("##### Detalle por área")
                columnas_resumen = [c for c in resumen.columns if ve_costos(rol) or "costo" not in c]
                st.dataframe(resumen[columnas_resumen], use_container_width=True, hide_index=True)

            st.write("")
            with st.container(border=True):
                st.markdown("##### 💧 Agua usada por área")
                st.bar_chart(resumen.set_index("area_nombre")["agua_total_litros"])

    # ======================== CORREGIR / ELIMINAR ========================
    with tab_corregir:
        _render_corregir(db, username, rol, insumos, areas)


def _render_corregir(db, username, rol, insumos, areas):
    df = db.get_df("limpieza_desinfeccion")
    if df.empty:
        st.info("No hay registros todavía.")
        return

    hoy = datetime.date.today()
    f1, f2 = st.columns(2)
    desde = f1.date_input("Desde", value=hoy - datetime.timedelta(days=30), key="limp_corr_desde")
    hasta = f2.date_input("Hasta", value=hoy, key="limp_corr_hasta")
    fechas = pd.to_datetime(df["fecha"], errors="coerce").dt.date
    df = df[(fechas >= desde) & (fechas <= hasta)].sort_values(["fecha", "limpieza_id"], ascending=False)
    if df.empty:
        st.info("No hay registros en ese período.")
        return

    mapa_area = dict(zip(areas["area_id"], areas["nombre"])) if not areas.empty else {}
    mapa_insumo = dict(zip(insumos["insumo_id"], insumos["nombre"])) if not insumos.empty else {}
    costo_insumo = (
        dict(zip(insumos["insumo_id"], pd.to_numeric(insumos["costo_unitario"], errors="coerce").fillna(0)))
        if not insumos.empty else {}
    )
    # Etiquetas armadas fila por fila: si un limpieza_id quedo repetido en la
    # hoja, .loc devolvia varias filas y la pantalla se caia (TypeError).
    etiquetas = {}
    for _, r in df.iterrows():
        etiquetas.setdefault(
            r["limpieza_id"],
            f"{r['limpieza_id']} — {r['fecha']} — {mapa_area.get(r['area_id'], r['area_id'])}",
        )
    limpieza_sel = st.selectbox(
        "Selecciona el registro", list(etiquetas), key="corregir_limpieza_select",
        format_func=lambda x: etiquetas[x],
    )
    insumos_rel = db.get_df("limpieza_insumos")
    insumos_rel = (
        insumos_rel[insumos_rel["limpieza_id"] == limpieza_sel].copy()
        if not insumos_rel.empty else insumos_rel
    )

    # ── Corregir cantidades (cualquier rol) ──
    with st.container(border=True):
        st.markdown("##### ✏️ Corregir cantidades de insumos")
        if insumos_rel.empty:
            st.write("Este registro no tiene insumos asociados (solo agua).")
        else:
            st.caption(
                "Corrige aquí si se registró en gramos o miligramos en vez de **kg** "
                "(ej. 40 g = 0.04 kg). El inventario de bodega y el costo de la "
                "limpieza se ajustan solos, y el cambio queda en la bitácora."
            )
            insumos_rel["cantidad"] = pd.to_numeric(insumos_rel["cantidad"], errors="coerce").fillna(0)
            tabla = pd.DataFrame({
                "detalle_id": insumos_rel["detalle_id"].values,
                "Insumo": insumos_rel["insumo_id"].map(mapa_insumo).fillna(insumos_rel["insumo_id"]).values,
                "Registrado (kg)": insumos_rel["cantidad"].values,
                "Correcto (kg)": insumos_rel["cantidad"].values,
            })
            editada = st.data_editor(
                tabla, hide_index=True, use_container_width=True,
                disabled=["detalle_id", "Insumo", "Registrado (kg)"],
                column_config={
                    "detalle_id": None,
                    "Registrado (kg)": st.column_config.NumberColumn(format="%.2f"),
                    "Correcto (kg)": st.column_config.NumberColumn(min_value=0.0, step=0.01, format="%.2f"),
                },
                key=f"limp_corr_editor_{limpieza_sel}",
            )
            motivo = st.text_input(
                "Motivo de la corrección (obligatorio)",
                placeholder="ej. 'se registró en gramos, eran 0.04 kg'",
                key=f"limp_corr_motivo_{limpieza_sel}",
            )
            if st.button("💾 Guardar corrección", type="primary", key=f"limp_corr_guardar_{limpieza_sel}"):
                editada["Correcto (kg)"] = pd.to_numeric(editada["Correcto (kg)"], errors="coerce").fillna(0).round(2)
                cambios = editada[(editada["Correcto (kg)"] - editada["Registrado (kg)"]).abs() > 1e-9]
                if cambios.empty:
                    st.warning("No cambiaste ninguna cantidad.")
                elif not motivo.strip():
                    st.error("Escribe el motivo de la corrección.")
                else:
                    _guardar_correccion(
                        db, username, limpieza_sel, insumos_rel, cambios, costo_insumo, motivo.strip(),
                    )
                    st.success(f"✅ Registro {limpieza_sel} corregido ({len(cambios)} insumo(s)).")
                    st.rerun()

    # ── Eliminar (solo administrador) ──
    with st.container(border=True):
        st.markdown("##### 🗑️ Eliminar registro")
        if not es_admin(rol):
            st.caption("🔒 Eliminar un registro completo está disponible solo para el administrador.")
            return
        st.caption(
            "Si te equivocaste al registrar una limpieza, elimínala aquí — "
            "esto devuelve automáticamente los insumos a bodega."
        )
        confirmar = st.checkbox(
            f"Confirmo que quiero eliminar el registro {limpieza_sel}", key="confirmar_del_limpieza",
        )
        if st.button("🗑️ Eliminar este registro"):
            if not confirmar:
                st.error("Marca la casilla de confirmación antes de eliminar.")
            else:
                movimientos = db.get_df("movimientos_envases_insumos")
                movimientos_a_revertir = movimientos[
                    (movimientos["observaciones"].astype(str) == str(limpieza_sel))
                    & (movimientos["modulo_destino"] == "Limpieza y desinfección")
                ]
                for mid in movimientos_a_revertir["movimiento_id"]:
                    db.delete_row("movimientos_envases_insumos", "movimiento_id", mid)
                db.delete_rows_where("limpieza_insumos", "limpieza_id", limpieza_sel)
                db.delete_row("limpieza_desinfeccion", "limpieza_id", limpieza_sel)
                st.success(f"Registro {limpieza_sel} eliminado y los insumos fueron devueltos a bodega.")
                st.rerun()


def _guardar_correccion(db, username, limpieza_id, insumos_rel, cambios, costo_insumo, motivo):
    """Corrige la cantidad de uno o mas insumos de una limpieza en las tres
    tablas que la guardan: el detalle (limpieza_insumos), la salida de bodega
    (movimientos_envases_insumos) y el costo del registro (limpieza_desinfeccion)."""
    movimientos = db.get_df("movimientos_envases_insumos")
    movs = movimientos[
        (movimientos["observaciones"].astype(str) == str(limpieza_id))
        & (movimientos["modulo_destino"] == "Limpieza y desinfección")
    ]
    # Si una limpieza tuviera dos lineas del mismo insumo, cada linea de
    # detalle se empareja con su salida de bodega en el mismo orden.
    movs_por_insumo = {k: list(g["movimiento_id"]) for k, g in movs.groupby("item_id")}
    detalle_por_insumo = {k: list(g["detalle_id"]) for k, g in insumos_rel.groupby("insumo_id")}
    mov_de_detalle = {}
    for insumo_id, detalles in detalle_por_insumo.items():
        for det, mov in zip(detalles, movs_por_insumo.get(insumo_id, [])):
            mov_de_detalle[det] = mov

    fila_det = insumos_rel.set_index("detalle_id")
    for _, c in cambios.iterrows():
        det = c["detalle_id"]
        insumo_id = fila_det.loc[det, "insumo_id"]
        nueva = float(c["Correcto (kg)"])
        # Se conserva el costo unitario con el que se registro la limpieza
        # (el del catalogo pudo cambiar despues); solo si no se puede
        # deducir se usa el costo actual del catalogo.
        cant_ant = float(pd.to_numeric(fila_det.loc[det, "cantidad"], errors="coerce") or 0)
        costo_ant = float(pd.to_numeric(fila_det.loc[det, "costo_calculado"], errors="coerce") or 0)
        costo_unit = costo_ant / cant_ant if cant_ant > 0 else float(costo_insumo.get(insumo_id, 0))
        db.update_row("limpieza_insumos", "detalle_id", det, {
            "cantidad": nueva, "costo_calculado": round(costo_unit * nueva, 4),
        })
        if det in mov_de_detalle:
            db.update_row("movimientos_envases_insumos", "movimiento_id", mov_de_detalle[det], {
                "cantidad": nueva,
                "costo_unitario": costo_unit,
                "costo_total": round(costo_unit * nueva, 4),
            })
        log_cambio(
            db, username, modulo="Limpieza y desinfección", tabla="limpieza_insumos",
            id_registro=f"{limpieza_id} / {det}", accion="edicion", campo=f"cantidad {insumo_id} (kg)",
            valor_antes=f"{float(c['Registrado (kg)']):.2f}", valor_despues=f"{nueva:.2f}", motivo=motivo,
        )

    # Costo total del registro con las cantidades ya corregidas
    detalle_nuevo = db.get_df("limpieza_insumos")
    detalle_nuevo = detalle_nuevo[detalle_nuevo["limpieza_id"] == limpieza_id]
    costo_total = float(pd.to_numeric(detalle_nuevo["costo_calculado"], errors="coerce").fillna(0).sum())
    db.update_row("limpieza_desinfeccion", "limpieza_id", limpieza_id, {
        "costo_insumos": round(costo_total, 4), "costo_total": round(costo_total, 4),
    })
