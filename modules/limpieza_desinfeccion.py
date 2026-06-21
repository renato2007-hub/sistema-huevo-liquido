"""
Limpieza y desinfeccion: registro de saneamiento de la planta -- lavado de
huevos, pasteurizador, equipos, gavetas, pisos, etc. -- separado del proceso
productivo. Cuantifica agua e insumos usados por area y deja constancia de
verificacion (util para auditorias BPM/HACCP). Los insumos consumidos aqui
descuentan del mismo inventario de Bodega de envases e insumos.
"""
import datetime
import streamlit as st
import pandas as pd
from utils.permisos import ve_costos, es_admin


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
                column_config={"insumo_id": st.column_config.SelectboxColumn("Insumo", options=opciones_insumo_nombres)},
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

        if guardar:
            detalle_insumos = []
            costo_insumos_total = 0.0
            for _, fila in df_insumos_input.iterrows():
                if pd.isna(fila.get("insumo_id")) or not fila.get("insumo_id"):
                    continue
                insumo_id_real = mapa_nombre_a_insumo_id.get(fila["insumo_id"], fila["insumo_id"])
                costo_unit = float(insumos.set_index("insumo_id").loc[insumo_id_real, "costo_unitario"])
                cant = float(fila["cantidad"]) if pd.notna(fila.get("cantidad")) else 0.0
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

            c1, c2, c3 = st.columns(3)
            with c1.container(border=True):
                st.metric("Total de registros", f"{len(df):,}")
            with c2.container(border=True):
                st.metric("Agua total usada", f"{df['agua_litros'].sum():,.0f} L")
            with c3.container(border=True):
                verificados = df["verificado"].astype(str).str.upper().eq("TRUE").sum()
                st.metric("Verificados", f"{verificados} de {len(df)}")

            st.write("")
            filtro_area = st.selectbox(
                "Filtrar por área", ["Todas"] + sorted(df["area_nombre"].dropna().unique().tolist()),
            )
            df_mostrar = df if filtro_area == "Todas" else df[df["area_nombre"] == filtro_area]

            columnas_base = ["fecha", "area_nombre", "tipo_limpieza", "agua_litros", "verificado", "observaciones"]
            if ve_costos(rol):
                columnas_base.insert(4, "costo_insumos")
            columnas = [c for c in columnas_base if c in df_mostrar.columns]
            with st.container(border=True):
                st.dataframe(
                    df_mostrar[columnas].sort_values("fecha", ascending=False),
                    use_container_width=True, hide_index=True,
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
        if not es_admin(rol):
            st.error("🔒 Esta función está disponible solo para el administrador.")
            return
        st.caption(
            "Si te equivocaste al registrar una limpieza, elimínala aquí — "
            "esto devuelve automáticamente los insumos a bodega."
        )
        df = db.get_df("limpieza_desinfeccion")
        if df.empty:
            st.info("No hay registros todavía.")
        else:
            with st.container(border=True):
                limpieza_sel = st.selectbox(
                    "Selecciona el registro a corregir", df["limpieza_id"], key="corregir_limpieza_select",
                )
                insumos_rel = db.get_df("limpieza_insumos")
                insumos_rel = insumos_rel[insumos_rel["limpieza_id"] == limpieza_sel]

                if not insumos_rel.empty:
                    st.write("↩️ Insumos que vuelven a bodega si eliminas este registro:")
                    st.dataframe(insumos_rel[["insumo_id", "cantidad"]], use_container_width=True, hide_index=True)
                else:
                    st.write("Este registro no tiene insumos asociados (solo agua).")

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
