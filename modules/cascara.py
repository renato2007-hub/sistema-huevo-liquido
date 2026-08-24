"""
Cascara (subproducto): inventario actual, salidas a la planta de
biomateriales circulares e historial. La cascara se genera en Produccion
de semielaborados (una sola vez por quiebre, asociada al lote donde se
ingresan las cubetas fisicas).
"""
import datetime
import streamlit as st
import pandas as pd


def render(db, username, rol):
    st.title("♻️ Cáscara — subproducto para biomateriales")

    tab_inv, tab_salida, tab_hist = st.tabs(
        ["📦 Inventario actual", "🚚 Registrar salida a biomateriales", "📋 Historial de salidas"]
    )

    prod_cascara = db.get_df("produccion_cascara")
    salidas = db.get_df("salidas_cascara")
    produccion = db.get_df("produccion_semielaborados")

    # ============================ INVENTARIO ============================
    with tab_inv:
        if prod_cascara.empty:
            st.info("Todavía no hay cáscara producida.")
        else:
            df = prod_cascara.copy()
            df["kg_saldo"] = pd.to_numeric(df["kg_saldo"], errors="coerce").fillna(0)
            df["kg"] = pd.to_numeric(df["kg"], errors="coerce").fillna(0)
            inv = df[df["kg_saldo"] >= 0.1].copy()

            total_producido = float(df["kg"].sum())
            total_saldo = float(inv["kg_saldo"].sum())
            total_enviado = total_producido - total_saldo

            c1, c2, c3 = st.columns(3)
            c1.metric("Producida (histórico)", f"{total_producido:,.1f} kg")
            c2.metric("Enviada a biomateriales", f"{total_enviado:,.1f} kg")
            c3.metric("En bodega (saldo)", f"{total_saldo:,.1f} kg")

            st.write("")
            if inv.empty:
                st.info("No hay saldo en bodega — toda la cáscara producida ya fue enviada.")
            else:
                # Traer el tipo de producto para contexto
                if not produccion.empty:
                    inv = inv.merge(
                        produccion[["lote_semielaborado_id", "tipo_producto"]].rename(
                            columns={"lote_semielaborado_id": "lote_semielaborado_origen"}
                        ),
                        on="lote_semielaborado_origen", how="left",
                    )
                cols = [c for c in [
                    "cascara_id", "fecha", "lote_semielaborado_origen",
                    "tipo_producto", "kg", "kg_saldo", "estado", "observaciones",
                ] if c in inv.columns]
                inv = inv.sort_values("fecha")
                st.dataframe(inv[cols], use_container_width=True, hide_index=True)

    # ======================== REGISTRAR SALIDA ========================
    with tab_salida:
        if prod_cascara.empty:
            st.info("No hay cáscara producida todavía.")
        else:
            df = prod_cascara.copy()
            df["kg_saldo"] = pd.to_numeric(df["kg_saldo"], errors="coerce").fillna(0)
            disponibles = df[df["kg_saldo"] >= 0.1].copy()

            if disponibles.empty:
                st.info("No hay saldo de cáscara disponible para enviar.")
            else:
                st.caption(
                    "Selecciona el lote de origen, la cantidad en kg a enviar, "
                    "el destino y la fecha. La salida descuenta del saldo del lote."
                )
                fecha = st.date_input("Fecha de salida", value=datetime.date.today(), key="cas_fecha")

                disponibles = disponibles.sort_values("fecha")
                cascara_id = st.selectbox(
                    "Lote de cáscara",
                    disponibles["cascara_id"],
                    format_func=lambda x: (
                        f"{x} — origen {disponibles.set_index('cascara_id').loc[x, 'lote_semielaborado_origen']} "
                        f"— saldo {float(disponibles.set_index('cascara_id').loc[x, 'kg_saldo']):.1f} kg"
                    ),
                    key="cas_lote",
                )
                fila = disponibles.set_index("cascara_id").loc[cascara_id]
                saldo = float(fila["kg_saldo"])

                kg_enviar = st.number_input(
                    "Kg a enviar", min_value=0.0, max_value=saldo, step=0.1,
                    value=saldo, key="cas_kg",
                )
                destino = st.text_input(
                    "Destino", value="Planta biomateriales circulares", key="cas_dest",
                )
                observaciones = st.text_area("Observaciones", "", key="cas_obs")

                if st.button("🚚 Registrar salida", type="primary"):
                    if kg_enviar <= 0:
                        st.error("Ingresa una cantidad mayor a cero.")
                    elif kg_enviar > saldo + 0.001:
                        st.error(f"No puedes enviar más de {saldo:.1f} kg (saldo del lote).")
                    else:
                        salida_id = db.siguiente_id("salidas_cascara", "SCAS", fecha)
                        db.append_row("salidas_cascara", {
                            "salida_id": salida_id,
                            "fecha": fecha.isoformat(),
                            "cascara_id_origen": cascara_id,
                            "kg": kg_enviar,
                            "destino": destino,
                            "usuario": username,
                            "observaciones": observaciones,
                        })
                        nuevo_saldo = saldo - kg_enviar
                        cambios = {"kg_saldo": nuevo_saldo}
                        if nuevo_saldo < 0.01:
                            cambios["estado"] = "enviado"
                        db.update_row("produccion_cascara", "cascara_id", cascara_id, cambios)
                        st.success(
                            f"✅ Salida {salida_id} registrada — {kg_enviar:.1f} kg "
                            f"del lote {cascara_id} enviados a {destino}."
                        )
                        st.rerun()

    # ============================ HISTORIAL ============================
    with tab_hist:
        if salidas.empty:
            st.info("No hay salidas registradas todavía.")
        else:
            df = salidas.copy()
            df["kg"] = pd.to_numeric(df["kg"], errors="coerce").fillna(0)
            df = df.sort_values("fecha", ascending=False)
            st.dataframe(
                df[[c for c in ["salida_id", "fecha", "cascara_id_origen", "kg",
                                "destino", "usuario", "observaciones"] if c in df.columns]],
                use_container_width=True, hide_index=True,
            )
            st.metric("Total enviado (histórico)", f"{df['kg'].sum():,.1f} kg")
