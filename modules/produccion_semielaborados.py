"""
Produccion de semielaborados: quiebre de huevo (y separacion si aplica),
consumo de materia prima e insumos de limpieza, mano de obra de la jornada,
y el control teorico vs. real que sostiene el costeo de toda la cadena.
"""
import datetime
import streamlit as st
import pandas as pd
from utils.costing import sugerir_lotes_fefo, costo_ponderado, rendimiento_teorico, sugerir_codigo_lote
from utils.permisos import ve_costos, es_admin


def render(db, username, rol):
    st.title("Producción de semielaborados")
    tab_nueva, tab_inventario, tab_rendimiento, tab_corregir = st.tabs(
        ["Nueva producción", "Inventario de tanques", "Teórico vs. real", "✏️ Corregir / eliminar"]
    )

    categorias = db.get_df("categorias_huevo")
    insumos = db.get_df("insumos")
    personal = db.get_df("personal")
    recepciones = db.get_df("recepciones_mp")

    with tab_nueva:
        if categorias.empty:
            st.warning("Configura al menos una categoría de huevo (con rendimiento) en Catálogos.")
            return

        fecha = st.date_input("Fecha de producción", value=datetime.date.today(), key="prod_fecha")
        orden_produccion = st.text_input("Orden de producción", "")
        if orden_produccion:
            producciones_existentes = db.get_df("produccion_semielaborados")
            if not producciones_existentes.empty and orden_produccion in producciones_existentes["orden_produccion"].astype(str).values:
                st.warning(
                    f"⚠️ Ya existe al menos una producción registrada con la orden "
                    f"'{orden_produccion}'. Si es un lote adicional de la misma orden está "
                    f"bien seguir, pero si fue un error de digitación, revisa antes de guardar "
                    f"(pestaña '✏️ Corregir / eliminar' para borrar duplicados)."
                )
        tipo_producto = st.selectbox("Producto a obtener", ["Huevo entero", "Clara", "Yema", "Clara y yema"])

        producciones_existentes = db.get_df("produccion_semielaborados")
        ids_existentes = set(producciones_existentes["lote_semielaborado_id"].astype(str)) if not producciones_existentes.empty else set()

        st.caption("Convención de planta: SR = huevo entero, R = clara, TK = yema, + fecha DDMMAA. Puedes editar el código libremente.")
        if tipo_producto == "Clara y yema":
            col_codigo1, col_codigo2 = st.columns(2)
            codigo_clara = col_codigo1.text_input(
                "Código de lote — Clara", value=sugerir_codigo_lote("Clara", fecha),
            )
            codigo_yema = col_codigo2.text_input(
                "Código de lote — Yema", value=sugerir_codigo_lote("Yema", fecha),
            )
            for codigo in (codigo_clara, codigo_yema):
                if codigo in ids_existentes:
                    st.error(f"⚠️ El código '{codigo}' ya existe — usa otro o ve a '✏️ Corregir / eliminar' si fue un error.")
        else:
            codigo_lote = st.text_input(
                "Código de lote", value=sugerir_codigo_lote(tipo_producto, fecha),
            )
            if codigo_lote in ids_existentes:
                st.error(f"⚠️ El código '{codigo_lote}' ya existe — usa otro o ve a '✏️ Corregir / eliminar' si fue un error.")

        categoria_id = st.selectbox(
            "Categoría / tamaño de huevo a usar",
            categorias["categoria_id"],
            format_func=lambda x: categorias.set_index("categoria_id").loc[x, "nombre"],
        )
        cubetas_necesarias = st.number_input("Cubetas a procesar", min_value=1, step=1)

        sugerencia = sugerir_lotes_fefo(recepciones, categoria_id, cubetas_necesarias)
        st.markdown("**Lotes sugeridos (FEFO) — puedes editar las cantidades**")
        if not sugerencia:
            st.error("No hay inventario disponible de esa categoría en bodega de materia prima.")
            df_lotes = pd.DataFrame(columns=["recepcion_id", "cantidad_a_tomar", "costo_cubeta"])
        else:
            df_lotes = pd.DataFrame(sugerencia)[["recepcion_id", "cantidad_a_tomar", "costo_cubeta"]]

        df_lotes_editado = st.data_editor(
            df_lotes, num_rows="dynamic", use_container_width=True,
            key=f"editor_lotes_{categoria_id}_{int(cubetas_necesarias)}",
        )

        total_tomado = float(pd.to_numeric(df_lotes_editado["cantidad_a_tomar"], errors="coerce").fillna(0).sum())
        st.info(f"📦 Vas a consumir **{total_tomado:.0f} cubetas** de bodega de materia prima — revisa que sea correcto antes de guardar.")
        if total_tomado < cubetas_necesarias:
            st.warning(
                f"Las cantidades de los lotes suman {total_tomado:.0f} cubetas, "
                f"pero se necesitan {cubetas_necesarias}. Ajusta antes de guardar."
            )

        st.markdown("**Insumos de limpieza usados**")
        opciones_insumo = list(insumos["insumo_id"]) if not insumos.empty else []
        df_insumos_input = st.data_editor(
            pd.DataFrame({"insumo_id": pd.Series(dtype="object"), "cantidad": pd.Series(dtype="float")}),
            num_rows="dynamic", use_container_width=True, key="editor_insumos",
            column_config={"insumo_id": st.column_config.SelectboxColumn("Insumo", options=opciones_insumo)},
        )

        st.markdown("**Personal que trabajó la jornada**")
        opciones_personal = list(personal["personal_id"]) if not personal.empty else []
        df_personal_input = st.data_editor(
            pd.DataFrame({"personal_id": pd.Series(dtype="object"), "horas": pd.Series(dtype="float")}),
            num_rows="dynamic", use_container_width=True, key="editor_personal",
            column_config={"personal_id": st.column_config.SelectboxColumn("Persona", options=opciones_personal)},
        )

        agua_litros = st.number_input("Agua usada (litros)", min_value=0.0, step=1.0)

        categoria_row = categorias.set_index("categoria_id").loc[categoria_id]
        teorico = rendimiento_teorico(total_tomado, categoria_row)
        st.markdown("**Valores teóricos calculados** (según cubetas y categoría seleccionadas)")
        col1, col2, col3, col4, col5 = st.columns(5)
        col1.metric("Bruto teórico (kg)", f"{teorico['kg_teorico_bruto']:.2f}", help="Peso del huevo entero, con cáscara")
        col2.metric("Líquido teórico (kg)", f"{teorico['kg_liquido_teorico']:.2f}", help="Sin cáscara — compara esto contra lo real")
        col3.metric("Clara teórica (kg)", f"{teorico['clara_teorica_kg']:.2f}")
        col4.metric("Yema teórica (kg)", f"{teorico['yema_teorica_kg']:.2f}")
        col5.metric("Cáscara teórica (kg)", f"{teorico['cascara_teorica_kg']:.2f}")

        st.markdown("**Valores reales obtenidos** (pesar al final del proceso — compara contra el *líquido* teórico de arriba)")
        kg_real = st.number_input("Kg reales obtenidos (huevo entero o suma)", min_value=0.0, step=0.1)
        clara_real_kg = st.number_input("Clara real (kg)", min_value=0.0, step=0.1)
        yema_real_kg = st.number_input("Yema real (kg)", min_value=0.0, step=0.1)
        cascara_real_kg = st.number_input("Cáscara real (kg)", min_value=0.0, step=0.1)

        masa_real_total = max(kg_real, clara_real_kg + yema_real_kg) + cascara_real_kg
        balance_masa_pct = (
            masa_real_total / teorico["kg_teorico_bruto"] * 100 if teorico["kg_teorico_bruto"] > 0 else 0
        )
        st.markdown("**Balance de masa** (líquido + cáscara reales, contra el peso bruto teórico del huevo que entró)")
        col_b1, col_b2 = st.columns(2)
        col_b1.metric("Masa real contabilizada", f"{masa_real_total:.2f} kg")
        col_b2.metric(
            "Balance de masa", f"{balance_masa_pct:.1f}%",
            help="100% = toda la masa que entró quedó contabilizada entre líquido y cáscara. Nunca debería superar 100%, eso violaría la conservación de masa.",
        )
        if balance_masa_pct > 100.5:
            st.error(
                "⚠️ El balance de masa supera el 100% — físicamente no es posible. "
                "Revisa la cantidad de cubetas, la categoría seleccionada, o los kg reales digitados antes de guardar."
            )
        elif masa_real_total > 0 and balance_masa_pct < 85:
            st.warning(
                f"⚠️ Solo se contabilizó el {balance_masa_pct:.0f}% de la masa esperada — hay una "
                f"pérdida sin explicar de aproximadamente {teorico['kg_teorico_bruto'] - masa_real_total:.1f} kg. "
                f"Revisa si falta registrar cáscara, hubo derrames, o un error de digitación."
            )

        observaciones = st.text_area("Observaciones", "", key="prod_obs")

        if st.button("Guardar producción"):
            if total_tomado <= 0:
                st.error("Define al menos un lote de huevo con cantidad mayor a cero.")
                return

            if tipo_producto == "Clara y yema":
                if not codigo_clara or not codigo_yema:
                    st.error("Ingresa el código de lote para clara y para yema.")
                    return
                if codigo_clara in ids_existentes or codigo_yema in ids_existentes:
                    st.error("Uno de los códigos ya existe. Corrígelo antes de guardar.")
                    return
            else:
                if not codigo_lote:
                    st.error("Ingresa el código de lote.")
                    return
                if codigo_lote in ids_existentes:
                    st.error("Ese código ya existe. Corrígelo antes de guardar.")
                    return

            detalle_lotes = [
                d for d in df_lotes_editado.to_dict("records")
                if pd.notna(d.get("cantidad_a_tomar")) and float(d.get("cantidad_a_tomar", 0)) > 0
            ]
            costo_unit_huevo = costo_ponderado([
                {"cantidad_a_tomar": float(d["cantidad_a_tomar"]), "costo_cubeta": float(d["costo_cubeta"])}
                for d in detalle_lotes
            ])
            costo_huevo_total = costo_unit_huevo * total_tomado

            costo_insumos_total = 0.0
            detalle_insumos = []
            for _, fila in df_insumos_input.iterrows():
                if pd.isna(fila.get("insumo_id")) or not fila.get("insumo_id"):
                    continue
                costo_unit = float(insumos.set_index("insumo_id").loc[fila["insumo_id"], "costo_unitario"])
                cant = float(fila["cantidad"]) if pd.notna(fila.get("cantidad")) else 0.0
                costo_insumos_total += costo_unit * cant
                detalle_insumos.append({
                    "insumo_id": fila["insumo_id"], "cantidad": cant, "costo_calculado": costo_unit * cant,
                })

            costo_mano_obra_total = 0.0
            detalle_personal = []
            for _, fila in df_personal_input.iterrows():
                if pd.isna(fila.get("personal_id")) or not fila.get("personal_id"):
                    continue
                costo_hora = float(personal.set_index("personal_id").loc[fila["personal_id"], "costo_hora"])
                horas = float(fila["horas"]) if pd.notna(fila.get("horas")) else 0.0
                costo_mano_obra_total += costo_hora * horas
                detalle_personal.append({
                    "personal_id": fila["personal_id"], "horas": horas, "costo_calculado": costo_hora * horas,
                })

            costo_total = costo_huevo_total + costo_insumos_total + costo_mano_obra_total

            # el "lote de referencia" es a quien se le atribuyen el consumo de
            # materia prima, insumos y personal -- en el caso de Clara y yema
            # se usa el codigo de clara como referencia, y el de yema queda
            # enlazado via observaciones (ambos vienen del mismo quiebre).
            lote_referencia = codigo_clara if tipo_producto == "Clara y yema" else codigo_lote

            for d in detalle_lotes:
                cantidad = float(d["cantidad_a_tomar"])
                costo_unit = float(d["costo_cubeta"])
                consumo_id = db.siguiente_id("consumo_mp_produccion", "CONS", fecha)
                db.append_row("consumo_mp_produccion", {
                    "consumo_id": consumo_id,
                    "fecha": fecha.isoformat(),
                    "recepcion_id": d["recepcion_id"],
                    "lote_semielaborado_id": lote_referencia,
                    "cubetas_usadas": cantidad,
                    "costo_unitario_aplicado": costo_unit,
                    "costo_total_aplicado": cantidad * costo_unit,
                    "usuario": username,
                })
                fila_recepcion = recepciones[recepciones["recepcion_id"] == d["recepcion_id"]]
                if not fila_recepcion.empty:
                    saldo_actual = float(fila_recepcion.iloc[0]["cubetas_saldo"])
                    db.update_row("recepciones_mp", "recepcion_id", d["recepcion_id"], {
                        "cubetas_saldo": saldo_actual - cantidad,
                    })

            for di in detalle_insumos:
                detalle_id = db.siguiente_id("produccion_insumos", "PI", fecha)
                db.append_row("produccion_insumos", {
                    "detalle_id": detalle_id,
                    "lote_semielaborado_id": lote_referencia,
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
                    "modulo_destino": "Producción de semielaborados",
                    "usuario": username,
                    "observaciones": lote_referencia,
                })

            for dp in detalle_personal:
                detalle_id = db.siguiente_id("produccion_personal", "PP", fecha)
                db.append_row("produccion_personal", {
                    "detalle_id": detalle_id,
                    "lote_semielaborado_id": lote_referencia,
                    **dp,
                })

            base_row = {
                "fecha": fecha.isoformat(),
                "orden_produccion": orden_produccion,
                "categoria_id": categoria_id,
                "cubetas_totales": total_tomado,
                "kg_teorico_bruto": teorico["kg_teorico_bruto"],
                "agua_litros": agua_litros,
                "usuario": username,
            }

            if tipo_producto == "Clara y yema":
                suma_real = clara_real_kg + yema_real_kg
                prop_clara = (clara_real_kg / suma_real) if suma_real > 0 else 0.5
                prop_yema = 1 - prop_clara

                costo_clara = costo_total * prop_clara
                costo_yema = costo_total * prop_yema
                costo_unit_clara = costo_clara / clara_real_kg if clara_real_kg > 0 else 0
                costo_unit_yema = costo_yema / yema_real_kg if yema_real_kg > 0 else 0

                db.append_row("produccion_semielaborados", {
                    **base_row,
                    "lote_semielaborado_id": codigo_clara,
                    "tipo_producto": "Clara",
                    "kg_liquido_teorico": teorico["clara_teorica_kg"],
                    "kg_real": clara_real_kg,
                    "clara_teorica_kg": teorico["clara_teorica_kg"],
                    "clara_real_kg": clara_real_kg,
                    "yema_teorica_kg": 0,
                    "yema_real_kg": 0,
                    "cascara_teorica_kg": teorico["cascara_teorica_kg"],
                    "cascara_real_kg": cascara_real_kg,
                    "costo_huevo": costo_huevo_total * prop_clara,
                    "costo_insumos": costo_insumos_total * prop_clara,
                    "costo_mano_obra": costo_mano_obra_total * prop_clara,
                    "costo_total": costo_clara,
                    "costo_unitario_kg": costo_unit_clara,
                    "kg_saldo": clara_real_kg,
                    "balance_masa_pct": balance_masa_pct,
                    "observaciones": f"{observaciones} (co-producto junto con lote yema {codigo_yema})".strip(),
                })
                db.append_row("produccion_semielaborados", {
                    **base_row,
                    "lote_semielaborado_id": codigo_yema,
                    "tipo_producto": "Yema",
                    "kg_liquido_teorico": teorico["yema_teorica_kg"],
                    "kg_real": yema_real_kg,
                    "clara_teorica_kg": 0,
                    "clara_real_kg": 0,
                    "yema_teorica_kg": teorico["yema_teorica_kg"],
                    "yema_real_kg": yema_real_kg,
                    "cascara_teorica_kg": teorico["cascara_teorica_kg"],
                    "cascara_real_kg": cascara_real_kg,
                    "costo_huevo": costo_huevo_total * prop_yema,
                    "costo_insumos": costo_insumos_total * prop_yema,
                    "costo_mano_obra": costo_mano_obra_total * prop_yema,
                    "costo_total": costo_yema,
                    "costo_unitario_kg": costo_unit_yema,
                    "kg_saldo": yema_real_kg,
                    "balance_masa_pct": balance_masa_pct,
                    "observaciones": f"{observaciones} (co-producto junto con lote clara {codigo_clara})".strip(),
                })

                if ve_costos(rol):
                    st.success(
                        f"Lotes {codigo_clara} (clara) y {codigo_yema} (yema) guardados — "
                        f"costo/kg clara {costo_unit_clara:,.2f}, costo/kg yema {costo_unit_yema:,.2f}"
                    )
                else:
                    st.success(f"Lotes {codigo_clara} (clara) y {codigo_yema} (yema) guardados.")
            else:
                costo_unitario_kg = costo_total / kg_real if kg_real > 0 else 0
                db.append_row("produccion_semielaborados", {
                    **base_row,
                    "lote_semielaborado_id": codigo_lote,
                    "tipo_producto": tipo_producto,
                    "kg_liquido_teorico": teorico["kg_liquido_teorico"],
                    "kg_real": kg_real,
                    "clara_teorica_kg": teorico["clara_teorica_kg"],
                    "clara_real_kg": clara_real_kg,
                    "yema_teorica_kg": teorico["yema_teorica_kg"],
                    "yema_real_kg": yema_real_kg,
                    "cascara_teorica_kg": teorico["cascara_teorica_kg"],
                    "cascara_real_kg": cascara_real_kg,
                    "costo_huevo": costo_huevo_total,
                    "costo_insumos": costo_insumos_total,
                    "costo_mano_obra": costo_mano_obra_total,
                    "costo_total": costo_total,
                    "costo_unitario_kg": costo_unitario_kg,
                    "kg_saldo": kg_real,
                    "balance_masa_pct": balance_masa_pct,
                    "observaciones": observaciones,
                })

                if ve_costos(rol):
                    st.success(
                        f"Lote {codigo_lote} guardado — "
                        f"costo total {costo_total:,.2f}, costo/kg {costo_unitario_kg:,.2f}"
                    )
                else:
                    st.success(f"Lote {codigo_lote} guardado.")

    with tab_inventario:
        df = db.get_df("produccion_semielaborados")
        if df.empty:
            st.info("Todavía no hay lotes de semielaborado.")
        else:
            df["kg_saldo"] = pd.to_numeric(df["kg_saldo"], errors="coerce").fillna(0)
            disponibles = df[df["kg_saldo"] > 0]
            columnas_disp = ["lote_semielaborado_id", "fecha", "tipo_producto", "kg_saldo"]
            if ve_costos(rol):
                columnas_disp.append("costo_unitario_kg")
            st.dataframe(disponibles[columnas_disp], use_container_width=True)

    with tab_rendimiento:
        df = db.get_df("produccion_semielaborados")
        if df.empty:
            st.info("No hay datos todavía.")
        else:
            df["kg_liquido_teorico"] = pd.to_numeric(df["kg_liquido_teorico"], errors="coerce")
            df["kg_real"] = pd.to_numeric(df["kg_real"], errors="coerce")
            df["rendimiento_pct"] = (df["kg_real"] / df["kg_liquido_teorico"] * 100).round(1)
            df["balance_masa_pct"] = pd.to_numeric(df.get("balance_masa_pct"), errors="coerce").round(1)

            anomalos = df[df["rendimiento_pct"] > 100]
            balance_anomalo = df[df["balance_masa_pct"] > 100.5]
            if not anomalos.empty:
                st.warning(
                    f"⚠️ {len(anomalos)} lote(s) muestran rendimiento mayor a 100%, lo cual no "
                    f"debería ser físicamente posible — revisa si el kg real se digitó bien, o "
                    f"si el kg_promedio_cubeta de la categoría usada está desactualizado."
                )
            if not balance_anomalo.empty:
                st.error(
                    f"❌ {len(balance_anomalo)} lote(s) muestran un balance de masa mayor a 100% — "
                    f"esto viola la conservación de masa, revisa esos registros con prioridad."
                )

            st.caption("Rendimiento = kg real ÷ kg líquido teórico (sin cáscara). Balance de masa = (líquido + cáscara real) ÷ peso bruto teórico del huevo.")
            st.dataframe(
                df[["lote_semielaborado_id", "fecha", "kg_liquido_teorico", "kg_real", "rendimiento_pct", "balance_masa_pct"]],
                use_container_width=True,
            )
            if df["fecha"].notna().any():
                col_g1, col_g2 = st.columns(2)
                col_g1.markdown("**Rendimiento (%) en el tiempo**")
                col_g1.line_chart(df.set_index("fecha")["rendimiento_pct"])
                col_g2.markdown("**Balance de masa (%) en el tiempo**")
                col_g2.line_chart(df.set_index("fecha")["balance_masa_pct"])

    with tab_corregir:
        if not es_admin(rol):
            st.error("🔒 Esta función está disponible solo para el administrador.")
            return
        st.caption(
            "Si te equivocaste al registrar una producción, NO la vuelvas a registrar encima — "
            "elimínala aquí primero (esto devuelve automáticamente las cubetas y los insumos a "
            "bodega), y luego regístrala de nuevo bien en 'Nueva producción'."
        )
        df_prod = db.get_df("produccion_semielaborados")
        if df_prod.empty:
            st.info("No hay producciones registradas todavía.")
        else:
            lote_sel = st.selectbox(
                "Selecciona el lote a corregir", df_prod["lote_semielaborado_id"], key="corregir_select",
            )
            fila_sel = df_prod[df_prod["lote_semielaborado_id"] == lote_sel].iloc[0]
            kg_real_sel = float(fila_sel["kg_real"]) if str(fila_sel["kg_real"]) != "" else 0.0
            kg_saldo_sel = float(fila_sel["kg_saldo"]) if str(fila_sel["kg_saldo"]) != "" else 0.0

            st.write(f"**Orden de producción:** {fila_sel['orden_produccion']} — **Fecha:** {fila_sel['fecha']}")
            st.write(f"**Kg real registrado:** {kg_real_sel:.2f} — **Kg saldo actual:** {kg_saldo_sel:.2f}")

            if "co-producto junto con lote" in str(fila_sel.get("observaciones", "")):
                st.info(
                    f"ℹ️ Este lote viene del mismo quiebre de huevo que otro lote hermano "
                    f"(ver observaciones: \"{fila_sel['observaciones']}\"). Si el error fue en "
                    f"el quiebre completo (no solo en este lote), probablemente quieras "
                    f"eliminar también el lote hermano por separado."
                )

            if kg_saldo_sel < kg_real_sel - 0.001:
                st.error(
                    "❌ Este lote ya tiene kg consumidos en Pasteurización y envasado "
                    "(el saldo es menor al kg real), así que no se puede eliminar sin dejar "
                    "inconsistencias en el producto ya envasado. Si de verdad necesitas "
                    "corregirlo, dímelo para revisarlo con cuidado en vez de borrarlo a ciegas."
                )
            else:
                consumo_rel = db.get_df("consumo_mp_produccion")
                consumo_rel = consumo_rel[consumo_rel["lote_semielaborado_id"] == lote_sel]
                insumos_rel = db.get_df("produccion_insumos")
                insumos_rel = insumos_rel[insumos_rel["lote_semielaborado_id"] == lote_sel]
                personal_rel = db.get_df("produccion_personal")
                personal_rel = personal_rel[personal_rel["lote_semielaborado_id"] == lote_sel]

                st.markdown("**Esto es lo que se va a revertir si lo eliminas:**")
                if not consumo_rel.empty:
                    st.write("↩️ Cubetas que vuelven a bodega de materia prima:")
                    st.dataframe(consumo_rel[["recepcion_id", "cubetas_usadas"]], use_container_width=True)
                if not insumos_rel.empty:
                    st.write("↩️ Insumos que vuelven a bodega:")
                    st.dataframe(insumos_rel[["insumo_id", "cantidad"]], use_container_width=True)
                if not personal_rel.empty:
                    st.write(f"↩️ Se eliminarán {len(personal_rel)} registro(s) de horas de personal.")

                confirmar = st.checkbox(
                    f"Confirmo que quiero eliminar la producción {lote_sel} y revertir todo lo anterior",
                    key="confirmar_eliminar_produccion",
                )
                if st.button("🗑️ Eliminar esta producción y revertir"):
                    if not confirmar:
                        st.error("Marca la casilla de confirmación antes de eliminar.")
                    else:
                        recepciones_actual = db.get_df("recepciones_mp")
                        for _, c in consumo_rel.iterrows():
                            fila_r = recepciones_actual[recepciones_actual["recepcion_id"] == c["recepcion_id"]]
                            if not fila_r.empty:
                                saldo_actual = float(fila_r.iloc[0]["cubetas_saldo"])
                                db.update_row("recepciones_mp", "recepcion_id", c["recepcion_id"], {
                                    "cubetas_saldo": saldo_actual + float(c["cubetas_usadas"]),
                                })
                        db.delete_rows_where("consumo_mp_produccion", "lote_semielaborado_id", lote_sel)

                        movimientos = db.get_df("movimientos_envases_insumos")
                        movimientos_a_revertir = movimientos[
                            (movimientos["observaciones"].astype(str) == str(lote_sel))
                            & (movimientos["modulo_destino"] == "Producción de semielaborados")
                        ]
                        for mid in movimientos_a_revertir["movimiento_id"]:
                            db.delete_row("movimientos_envases_insumos", "movimiento_id", mid)
                        db.delete_rows_where("produccion_insumos", "lote_semielaborado_id", lote_sel)
                        db.delete_rows_where("produccion_personal", "lote_semielaborado_id", lote_sel)
                        db.delete_row("produccion_semielaborados", "lote_semielaborado_id", lote_sel)

                        st.success(
                            f"Producción {lote_sel} eliminada y todo revertido — ya puedes "
                            f"volver a registrarla correctamente en 'Nueva producción'."
                        )
                        st.rerun()
