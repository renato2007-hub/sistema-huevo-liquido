"""
Bodega de materia prima: recepcion de huevo (galpones propios y proveedores
calificados) e inventario por lote. El consumo hacia produccion se registra
desde el modulo 'Produccion de semielaborados', no aqui -- asi el saldo de
cada lote siempre queda ligado a la produccion que lo origino (trazabilidad).
"""
import datetime
import streamlit as st
import pandas as pd


CAUSAS_MERMA_HUEVO = ["Caída", "Rotura en bodega", "Daño de transporte/proveedor", "Otro"]


def render(db, username):
    st.title("Bodega de materia prima — huevo")
    tab_recepcion, tab_inventario, tab_historial, tab_perdida = st.tabs(
        ["Registrar recepción", "Inventario actual", "Historial", "⚠️ Registrar pérdida/daño"]
    )

    galpones = db.get_df("galpones")
    proveedores = db.get_df("proveedores")
    categorias = db.get_df("categorias_huevo")

    with tab_recepcion:
        if categorias.empty:
            st.warning(
                "No hay categorías de huevo configuradas todavía. Ve a "
                "'Catálogos y configuración' y crea al menos una (con su "
                "rendimiento teórico) antes de registrar una recepción."
            )
        else:
            origen_tipo = st.radio("Origen", ["Galpón propio", "Proveedor"], horizontal=True)
            if origen_tipo == "Galpón propio":
                opciones_origen = galpones[galpones.get("activo", "TRUE").astype(str).str.upper() != "FALSE"]
                col_id, col_nombre = "galpon_id", "nombre"
            else:
                opciones_origen = proveedores[proveedores.get("activo", "TRUE").astype(str).str.upper() != "FALSE"]
                col_id, col_nombre = "proveedor_id", "nombre"

            if opciones_origen.empty:
                st.warning(f"No hay {origen_tipo.lower()}s configurados. Créalos en Catálogos.")
            else:
                with st.form("form_recepcion"):
                    fecha = st.date_input("Fecha de recepción", value=datetime.date.today())
                    origen_id = st.selectbox(
                        "Origen",
                        opciones_origen[col_id],
                        format_func=lambda x: opciones_origen.set_index(col_id).loc[x, col_nombre],
                    )
                    categoria_id = st.selectbox(
                        "Categoría / tamaño de huevo",
                        categorias["categoria_id"],
                        format_func=lambda x: categorias.set_index("categoria_id").loc[x, "nombre"],
                    )
                    cubetas = st.number_input("Cubetas recibidas (30 huevos c/u)", min_value=1, step=1)
                    costo_cubeta = st.number_input("Costo por cubeta", min_value=0.0, step=0.01, format="%.2f")
                    fecha_vencimiento = st.date_input(
                        "Fecha de vencimiento estimada",
                        value=datetime.date.today() + datetime.timedelta(days=21),
                    )
                    observaciones = st.text_area("Observaciones", "")
                    guardar = st.form_submit_button("Registrar recepción")

                if guardar:
                    recepcion_id = db.siguiente_id("recepciones_mp", "MP", fecha)
                    costo_total = cubetas * costo_cubeta
                    db.append_row("recepciones_mp", {
                        "recepcion_id": recepcion_id,
                        "fecha": fecha.isoformat(),
                        "origen_tipo": origen_tipo,
                        "origen_id": origen_id,
                        "categoria_id": categoria_id,
                        "cubetas": cubetas,
                        "costo_cubeta": costo_cubeta,
                        "costo_total": costo_total,
                        "cubetas_saldo": cubetas,
                        "fecha_vencimiento": fecha_vencimiento.isoformat(),
                        "usuario": username,
                        "observaciones": observaciones,
                    })
                    st.success(f"Recepción {recepcion_id} registrada — costo total {costo_total:,.2f}")

    with tab_inventario:
        df = db.get_df("recepciones_mp")
        if df.empty:
            st.info("Todavía no hay recepciones registradas.")
        else:
            df["cubetas_saldo"] = pd.to_numeric(df["cubetas_saldo"], errors="coerce").fillna(0)
            inventario = df[df["cubetas_saldo"] > 0].copy()
            if inventario.empty:
                st.info("No hay saldo disponible en bodega de materia prima.")
            else:
                inventario["fecha_vencimiento"] = pd.to_datetime(inventario["fecha_vencimiento"], errors="coerce")
                inventario["costo_cubeta"] = pd.to_numeric(inventario["costo_cubeta"], errors="coerce")
                inventario["valor_saldo"] = inventario["cubetas_saldo"] * inventario["costo_cubeta"]
                inventario = inventario.sort_values("fecha_vencimiento")
                hoy = pd.Timestamp(datetime.date.today())
                inventario["dias_para_vencer"] = (inventario["fecha_vencimiento"] - hoy).dt.days

                proximos = inventario[inventario["dias_para_vencer"] <= 3]
                if not proximos.empty:
                    st.warning(f"{len(proximos)} lote(s) vencen en 3 días o menos — revisa el orden de consumo.")

                st.dataframe(
                    inventario[[
                        "recepcion_id", "fecha", "origen_tipo", "categoria_id",
                        "cubetas_saldo", "costo_cubeta", "valor_saldo",
                        "fecha_vencimiento", "dias_para_vencer",
                    ]],
                    use_container_width=True,
                )
                st.metric("Valor total en bodega de materia prima", f"{inventario['valor_saldo'].sum():,.2f}")

    with tab_historial:
        df = db.get_df("recepciones_mp")
        st.dataframe(df, use_container_width=True)

    with tab_perdida:
        st.caption(
            "Para huevo que se daña en bodega ANTES de entrar a producción "
            "(se cayó, se rompió, llegó dañado, etc.) — esto descuenta del "
            "saldo del lote igual que un consumo, pero queda registrado como "
            "pérdida, no como producción."
        )
        df = db.get_df("recepciones_mp")
        if df.empty:
            st.info("No hay recepciones registradas todavía.")
        else:
            df["cubetas_saldo"] = pd.to_numeric(df["cubetas_saldo"], errors="coerce").fillna(0)
            disponibles = df[df["cubetas_saldo"] > 0]
            if disponibles.empty:
                st.info("No hay saldo disponible en ningún lote.")
            else:
                fecha = st.date_input("Fecha", value=datetime.date.today(), key="perdida_fecha")
                recepcion_id = st.selectbox(
                    "Lote afectado",
                    disponibles["recepcion_id"],
                    format_func=lambda x: (
                        f"{x} — saldo {disponibles.set_index('recepcion_id').loc[x, 'cubetas_saldo']:.0f} cubetas"
                    ),
                )
                fila_lote = disponibles.set_index("recepcion_id").loc[recepcion_id]
                cubetas_saldo_lote = float(fila_lote["cubetas_saldo"])
                costo_cubeta_lote = float(fila_lote["costo_cubeta"])

                huevos_danados = st.number_input(
                    "Cantidad de huevos dañados/perdidos", min_value=0, step=1,
                )
                cubetas_equivalentes = huevos_danados / 30
                if cubetas_equivalentes > cubetas_saldo_lote:
                    st.error(
                        f"Eso equivale a {cubetas_equivalentes:.2f} cubetas, pero el lote solo "
                        f"tiene {cubetas_saldo_lote:.2f} cubetas de saldo. Revisa la cantidad."
                    )
                costo_estimado = cubetas_equivalentes * costo_cubeta_lote
                st.caption(
                    f"≈ {cubetas_equivalentes:.2f} cubetas — costo estimado de la pérdida: {costo_estimado:,.2f}"
                )

                causa = st.selectbox("Causa", CAUSAS_MERMA_HUEVO)
                observaciones = st.text_area("Observaciones", "", key="perdida_obs")

                if st.button("Registrar pérdida"):
                    if huevos_danados <= 0:
                        st.error("Ingresa una cantidad mayor a cero.")
                    elif cubetas_equivalentes > cubetas_saldo_lote:
                        st.error("La cantidad supera el saldo disponible del lote. Corrige antes de guardar.")
                    else:
                        merma_id = db.siguiente_id("mermas_mp", "MERMP", fecha)
                        db.append_row("mermas_mp", {
                            "merma_id": merma_id,
                            "fecha": fecha.isoformat(),
                            "recepcion_id": recepcion_id,
                            "causa": causa,
                            "huevos_danados": huevos_danados,
                            "cubetas_equivalentes": cubetas_equivalentes,
                            "costo_estimado": costo_estimado,
                            "usuario": username,
                            "observaciones": observaciones,
                        })
                        db.update_row("recepciones_mp", "recepcion_id", recepcion_id, {
                            "cubetas_saldo": cubetas_saldo_lote - cubetas_equivalentes,
                        })
                        st.success(
                            f"Pérdida {merma_id} registrada — {huevos_danados} huevos "
                            f"({cubetas_equivalentes:.2f} cubetas, costo {costo_estimado:,.2f})"
                        )
                        st.rerun()

        st.divider()
        st.markdown("**Histórico de pérdidas**")
        mermas = db.get_df("mermas_mp")
        if mermas.empty:
            st.info("No hay pérdidas registradas todavía.")
        else:
            st.dataframe(
                mermas[["fecha", "recepcion_id", "causa", "huevos_danados", "costo_estimado", "observaciones"]]
                .sort_values("fecha", ascending=False),
                use_container_width=True,
            )
            costo_total_mermas = pd.to_numeric(mermas["costo_estimado"], errors="coerce").fillna(0).sum()
            st.metric("Costo total acumulado en pérdidas", f"{costo_total_mermas:,.2f}")
