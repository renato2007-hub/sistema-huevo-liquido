"""
Solicitud de MP e insumos: ordenes de compra que se mandan a hacer a
proveedores -- materiales de limpieza, quimicos, envases, y otros (tapas/
etiquetas/cartones/liners). Cada solicitud tiene un encabezado (n de OC,
fechas, proveedor recomendado, si ya se recibio o no) y una lista de items
de cualquiera de las 4 categorias.

No mueve inventario por si sola -- es planificacion/seguimiento de compras,
no un movimiento de bodega. Cuando el pedido llegue fisicamente, se sigue
registrando la entrada real en Bodega de materia prima / Bodega de envases
e insumos, como siempre.
"""
import datetime
import streamlit as st
import pandas as pd
from utils.pdf_orden_compra import generar_pdf_orden_compra

CATEGORIAS = [
    "🧹 Materiales de limpieza y desinfección",
    "🧪 Químicos para limpieza y desinfección",
    "📦 Envases",
    "🏷️ Otros (tapas, etiquetas, cartones, liners)",
]


def _opciones_otros(tapas, etiquetas, cartones, liners):
    opciones = []
    for _, r in tapas.iterrows():
        opciones.append((f"Tapa: {r['color']}", "unidad"))
    for _, r in etiquetas.iterrows():
        opciones.append((f"Etiqueta: {r['nombre']}", "unidad"))
    for _, r in cartones.iterrows():
        opciones.append((f"Cartón: {r['nombre']}", "unidad"))
    for _, r in liners.iterrows():
        opciones.append((f"Liner: {r['nombre']}", "unidad"))
    return opciones


def render(db, username, rol):
    st.title("📑 Solicitud MP e Insumos")
    tab_nueva, tab_pendientes, tab_todas = st.tabs(
        ["➕ Nueva solicitud", "🔴 Pendientes de recibir", "📋 Todas las solicitudes"]
    )

    materiales = db.get_df("materiales_limpieza")
    insumos = db.get_df("insumos")
    presentaciones = db.get_df("presentaciones")
    tapas = db.get_df("tapas")
    etiquetas = db.get_df("etiquetas")
    cartones = db.get_df("cartones")
    liners = db.get_df("liners")

    # ======================== NUEVA SOLICITUD ========================
    with tab_nueva:
        with st.container(border=True):
            st.markdown("##### 📋 Datos de la orden de compra")
            c1, c2 = st.columns(2)
            numero_oc = c1.text_input("N° de OC", "")
            fecha_solicitud = c2.date_input("Fecha de solicitud", value=datetime.date.today())
            fecha_maxima = st.date_input(
                "Fecha máxima esperada de recepción",
                value=datetime.date.today() + datetime.timedelta(days=7),
            )

        st.markdown("##### 📝 Ítems a solicitar")
        st.caption("Llena solo las categorías que necesites. Cada ítem puede tener su propio proveedor.")

        items_recolectados = []

        with st.expander(CATEGORIAS[0], expanded=False):
            if materiales.empty:
                st.info("No hay materiales de limpieza configurados — créalos en Catálogos → Materiales de limpieza.")
            else:
                opciones_mat = list(materiales["nombre"])
                df_mat = st.data_editor(
                    pd.DataFrame({"Ítem": pd.Series(dtype="object"), "Cantidad": pd.Series(dtype="float"), "Proveedor": pd.Series(dtype="object")}),
                    num_rows="dynamic", use_container_width=True, hide_index=True, key="editor_materiales",
                    column_config={"Ítem": st.column_config.SelectboxColumn("Ítem", options=opciones_mat)},
                )
                for _, fila in df_mat.iterrows():
                    if pd.notna(fila.get("Ítem")) and fila.get("Ítem") and pd.notna(fila.get("Cantidad")) and fila["Cantidad"] > 0:
                        unidad = materiales.set_index("nombre").loc[fila["Ítem"], "unidad"] if fila["Ítem"] in list(materiales["nombre"]) else ""
                        items_recolectados.append({
                            "categoria": CATEGORIAS[0], "nombre_item": fila["Ítem"],
                            "cantidad": fila["Cantidad"], "unidad": unidad,
                            "proveedor": str(fila.get("Proveedor") or ""),
                        })

        with st.expander(CATEGORIAS[1], expanded=False):
            if insumos.empty:
                st.info("No hay insumos químicos configurados — créalos en Catálogos → Insumos.")
            else:
                opciones_ins = list(insumos["nombre"])
                df_ins = st.data_editor(
                    pd.DataFrame({"Ítem": pd.Series(dtype="object"), "Cantidad": pd.Series(dtype="float"), "Proveedor": pd.Series(dtype="object")}),
                    num_rows="dynamic", use_container_width=True, hide_index=True, key="editor_quimicos",
                    column_config={"Ítem": st.column_config.SelectboxColumn("Ítem", options=opciones_ins)},
                )
                for _, fila in df_ins.iterrows():
                    if pd.notna(fila.get("Ítem")) and fila.get("Ítem") and pd.notna(fila.get("Cantidad")) and fila["Cantidad"] > 0:
                        unidad = insumos.set_index("nombre").loc[fila["Ítem"], "unidad"] if fila["Ítem"] in list(insumos["nombre"]) else ""
                        items_recolectados.append({
                            "categoria": CATEGORIAS[1], "nombre_item": fila["Ítem"],
                            "cantidad": fila["Cantidad"], "unidad": unidad,
                            "proveedor": str(fila.get("Proveedor") or ""),
                        })

        with st.expander(CATEGORIAS[2], expanded=False):
            if presentaciones.empty:
                st.info("No hay presentaciones configuradas — créalas en Catálogos → Presentaciones de envase.")
            else:
                opciones_pres = list(presentaciones["nombre"])
                df_env = st.data_editor(
                    pd.DataFrame({"Ítem": pd.Series(dtype="object"), "Cantidad": pd.Series(dtype="float"), "Proveedor": pd.Series(dtype="object")}),
                    num_rows="dynamic", use_container_width=True, hide_index=True, key="editor_envases",
                    column_config={"Ítem": st.column_config.SelectboxColumn("Ítem", options=opciones_pres)},
                )
                for _, fila in df_env.iterrows():
                    if pd.notna(fila.get("Ítem")) and fila.get("Ítem") and pd.notna(fila.get("Cantidad")) and fila["Cantidad"] > 0:
                        items_recolectados.append({
                            "categoria": CATEGORIAS[2], "nombre_item": fila["Ítem"],
                            "cantidad": fila["Cantidad"], "unidad": "unidad",
                            "proveedor": str(fila.get("Proveedor") or ""),
                        })

        with st.expander(CATEGORIAS[3], expanded=False):
            opciones_otros_lista = _opciones_otros(tapas, etiquetas, cartones, liners)
            if not opciones_otros_lista:
                st.info("No hay tapas, etiquetas, cartones ni liners configurados todavía.")
            else:
                opciones_otros_nombres = [o[0] for o in opciones_otros_lista]
                df_otros = st.data_editor(
                    pd.DataFrame({"Ítem": pd.Series(dtype="object"), "Cantidad": pd.Series(dtype="float"), "Proveedor": pd.Series(dtype="object")}),
                    num_rows="dynamic", use_container_width=True, hide_index=True, key="editor_otros",
                    column_config={"Ítem": st.column_config.SelectboxColumn("Ítem", options=opciones_otros_nombres)},
                )
                for _, fila in df_otros.iterrows():
                    if pd.notna(fila.get("Ítem")) and fila.get("Ítem") and pd.notna(fila.get("Cantidad")) and fila["Cantidad"] > 0:
                        items_recolectados.append({
                            "categoria": CATEGORIAS[3], "nombre_item": fila["Ítem"],
                            "cantidad": fila["Cantidad"], "unidad": "unidad",
                            "proveedor": str(fila.get("Proveedor") or ""),
                        })

        observaciones = st.text_area("Observaciones generales de la orden", "", key="solicitud_obs")

        if st.button("💾 Guardar solicitud", type="primary", use_container_width=True):
            if not items_recolectados:
                st.error("Agrega al menos un ítem en alguna de las categorías de arriba.")
            else:
                solicitud_id = db.siguiente_id("solicitudes_compra", "SOL", fecha_solicitud)
                db.append_row("solicitudes_compra", {
                    "solicitud_id": solicitud_id,
                    "numero_oc": numero_oc,
                    "fecha_solicitud": fecha_solicitud.isoformat(),
                    "fecha_maxima_recepcion": fecha_maxima.isoformat(),
                    "proveedor_recomendado": "",
                    "recibido": False,
                    "usuario": username,
                    "observaciones": observaciones,
                })
                for it in items_recolectados:
                    detalle_id = db.siguiente_id("solicitud_compra_items", "SOLI", fecha_solicitud)
                    db.append_row("solicitud_compra_items", {
                        "detalle_id": detalle_id,
                        "solicitud_id": solicitud_id,
                        **{k: v for k, v in it.items() if k != "proveedor"},
                        "proveedor": it.get("proveedor", ""),
                    })
                st.success(f"✅ Solicitud {solicitud_id} guardada con {len(items_recolectados)} ítem(s).")
                st.rerun()

    # ======================== helpers ========================
    def _enriquecer(df):
        if df.empty:
            return df
        df = df.copy()
        df["recibido_bool"] = df["recibido"].astype(str).str.upper().isin(["TRUE", "1", "SI", "SÍ"])
        hoy_str = datetime.date.today().isoformat()
        df["atrasado"] = (~df["recibido_bool"]) & (df["fecha_maxima_recepcion"].astype(str) < hoy_str)

        def _estado(row):
            if row["recibido_bool"]:
                return "✅ Recibido"
            if row["atrasado"]:
                return "🔴 Atrasado"
            return "🟡 Pendiente"

        df["estado"] = df.apply(_estado, axis=1)
        return df

    def _descargar_pdf(solicitud_id, fila_solicitud, contexto):
        items_df = db.get_df("solicitud_compra_items")
        items_solicitud = items_df[items_df["solicitud_id"] == solicitud_id] if not items_df.empty else pd.DataFrame()
        pdf_bytes = generar_pdf_orden_compra(fila_solicitud.to_dict(), items_solicitud.to_dict("records"))
        st.download_button(
            "📄 Descargar orden de compra (PDF)", data=pdf_bytes,
            file_name=f"orden_compra_{solicitud_id}.pdf", mime="application/pdf",
            key=f"pdf_{contexto}_{solicitud_id}",
        )

    columnas_mostrar = [
        "solicitud_id", "numero_oc",
        "fecha_solicitud", "fecha_maxima_recepcion", "observaciones",
    ]

    # ======================== PENDIENTES DE RECIBIR ========================
    with tab_pendientes:
        df = _enriquecer(db.get_df("solicitudes_compra"))
        if df.empty:
            st.info("No hay solicitudes registradas todavía.")
        else:
            pendientes = df[~df["recibido_bool"]].sort_values("fecha_maxima_recepcion")
            if pendientes.empty:
                st.success("🎉 No hay solicitudes pendientes — todo lo registrado ya llegó.")
            else:
                atrasados = pendientes[pendientes["atrasado"]]
                if not atrasados.empty:
                    st.error(f"⚠️ {len(atrasados)} solicitud(es) con fecha máxima de recepción ya vencida.")

                st.metric("Solicitudes pendientes de recibir", len(pendientes))
                st.dataframe(
                    pendientes[["estado"] + columnas_mostrar].rename(columns={"estado": "Estado"}),
                    use_container_width=True, hide_index=True,
                )

                st.write("")
                st.markdown("##### ✅ Marcar como recibido / descargar OC")
                solicitud_sel = st.selectbox(
                    "Solicitud", pendientes["solicitud_id"],
                    format_func=lambda x: (
                        f"{x} — OC {pendientes.set_index('solicitud_id').loc[x, 'numero_oc'] or 's/n'} "
                        f"({pendientes.set_index('solicitud_id').loc[x, 'fecha_maxima_recepcion']})"
                    ),
                )
                c1, c2 = st.columns(2)
                if c1.button("✅ Marcar como recibido", use_container_width=True):
                    db.update_row("solicitudes_compra", "solicitud_id", solicitud_sel, {"recibido": True})
                    st.success(f"Solicitud {solicitud_sel} marcada como recibida.")
                    st.rerun()
                with c2:
                    _descargar_pdf(solicitud_sel, pendientes.set_index("solicitud_id").loc[solicitud_sel], "pendientes")

    # ======================== TODAS LAS SOLICITUDES ========================
    with tab_todas:
        df = _enriquecer(db.get_df("solicitudes_compra"))
        if df.empty:
            st.info("No hay solicitudes registradas todavía.")
        else:
            filtro_estado = st.selectbox("Estado", ["Todos", "Pendientes", "Recibidos"], key="filtro_estado_solicitudes")
            df_mostrar = df.copy()
            if filtro_estado == "Pendientes":
                df_mostrar = df_mostrar[~df_mostrar["recibido_bool"]]
            elif filtro_estado == "Recibidos":
                df_mostrar = df_mostrar[df_mostrar["recibido_bool"]]

            atrasados_total = df_mostrar[df_mostrar["atrasado"]]
            if not atrasados_total.empty:
                st.error(f"⚠️ {len(atrasados_total)} solicitud(es) atrasada(s) sin recibir.")

            st.dataframe(
                df_mostrar.rename(columns={"estado": "Estado"})[["Estado"] + columnas_mostrar].sort_values("fecha_solicitud", ascending=False),
                use_container_width=True, hide_index=True,
            )

            st.write("")
            st.markdown("##### 📄 Descargar orden de compra de cualquier solicitud")
            solicitud_sel2 = st.selectbox(
                "Solicitud", df["solicitud_id"],
                format_func=lambda x: f"{x} — OC {df.set_index('solicitud_id').loc[x, 'numero_oc'] or 's/n'}",
                key="solicitud_pdf_todas",
            )
            _descargar_pdf(solicitud_sel2, df.set_index("solicitud_id").loc[solicitud_sel2], "todas")
