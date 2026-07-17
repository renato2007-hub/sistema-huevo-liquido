"""
Produccion de semielaborados: quiebre de huevo (y separacion si aplica),
consumo de materia prima e insumos de limpieza, mano de obra de la jornada,
y el control teorico vs. real que sostiene el costeo de toda la cadena.
"""
import datetime
import streamlit as st
import pandas as pd
from utils.costing import sugerir_lotes_fefo, costo_ponderado, rendimiento_teorico, sugerir_codigo_lote
from utils.horas_trabajo import calcular_horas_sesion
from utils.permisos import ve_costos, es_admin


def render(db, username, rol):
    st.title("Producción de semielaborados")
    tab_nueva, tab_inventario, tab_historial, tab_perdida, tab_rendimiento, tab_corregir = st.tabs(
        ["Nueva producción", "Inventario de tanques", "📋 Historial",
         "⚠️ Registrar pérdida", "Teórico vs. real", "✏️ Corregir / eliminar"]
    )

    categorias = db.get_df("categorias_huevo")
    insumos = db.get_df("insumos")
    personal = db.get_df("personal")
    recepciones = db.get_df("recepciones_mp")
    turnos = db.get_df("turnos")

    with tab_nueva:
        # ---- alerta temprana: pedidos que requieren produccion pronto ----
        pedidos_df = db.get_df("pedidos")
        if not pedidos_df.empty:
            producido_bool = pedidos_df["producido"].astype(str).str.upper().isin(["TRUE", "1", "SI", "SÍ"])
            pendientes_prod = pedidos_df[~producido_bool].copy()
            if not pendientes_prod.empty:
                hoy_date = datetime.date.today()
                hoy_str = hoy_date.isoformat()
                limite_str = (hoy_date + datetime.timedelta(days=3)).isoformat()
                atrasados_prod = pendientes_prod[pendientes_prod["fecha_produccion"].astype(str) < hoy_str]
                proximos_prod = pendientes_prod[
                    (pendientes_prod["fecha_produccion"].astype(str) >= hoy_str)
                    & (pendientes_prod["fecha_produccion"].astype(str) <= limite_str)
                ]
                if not atrasados_prod.empty or not proximos_prod.empty:
                    with st.container(border=True):
                        st.markdown("##### 📋 Pedidos que requieren producción pronto")
                        if not atrasados_prod.empty:
                            atrasados_prod = atrasados_prod.copy()
                            atrasados_prod["cantidad_kg"] = pd.to_numeric(atrasados_prod["cantidad_kg"], errors="coerce").fillna(0)
                            resumen_atrasados = atrasados_prod.groupby("tipo_producto").agg(
                                kg=("cantidad_kg", "sum"), pedidos=("pedido_id", "count"),
                            )
                            st.error("🔴 Ya debiste haber producido:")
                            for tipo, fila in resumen_atrasados.iterrows():
                                st.markdown(f"- **{tipo}**: {fila['kg']:.1f} kg ({int(fila['pedidos'])} pedido(s))")
                        if not proximos_prod.empty:
                            proximos_prod = proximos_prod.copy()
                            proximos_prod["cantidad_kg"] = pd.to_numeric(proximos_prod["cantidad_kg"], errors="coerce").fillna(0)
                            resumen_proximos = proximos_prod.groupby("tipo_producto").agg(
                                kg=("cantidad_kg", "sum"), pedidos=("pedido_id", "count"),
                            )
                            st.warning("🟡 Producción próxima (siguientes 3 días):")
                            for tipo, fila in resumen_proximos.iterrows():
                                st.markdown(f"- **{tipo}**: {fila['kg']:.1f} kg ({int(fila['pedidos'])} pedido(s))")
                        with st.expander("Ver detalle de estos pedidos"):
                            cols_detalle = [c for c in [
                                "pedido_id", "cliente_id", "tipo_producto", "cantidad_kg",
                                "fecha_produccion", "fecha_entrega",
                            ] if c in pendientes_prod.columns]
                            todos_pendientes_vista = pd.concat([atrasados_prod, proximos_prod])
                            st.dataframe(
                                todos_pendientes_vista[cols_detalle],
                                use_container_width=True, hide_index=True,
                            )
                            st.markdown("**✅ ¿Ya produjiste para alguno de estos pedidos?** Márcalo aquí mismo:")
                            pedidos_a_marcar = st.multiselect(
                                "Pedidos ya producidos", todos_pendientes_vista["pedido_id"],
                                key="marcar_producido_alerta",
                            )
                            if st.button("Marcar seleccionados como producidos"):
                                if not pedidos_a_marcar:
                                    st.error("Selecciona al menos un pedido.")
                                else:
                                    for pid in pedidos_a_marcar:
                                        db.update_row("pedidos", "pedido_id", pid, {"producido": True})
                                    st.success(f"{len(pedidos_a_marcar)} pedido(s) marcado(s) como producido(s).")
                                    st.rerun()
                    st.write("")

        if categorias.empty:
            st.warning("Configura al menos una categoría de huevo (con rendimiento) en Catálogos.")
            return

        fecha = st.date_input("Fecha de producción", value=datetime.date.today(), key="prod_fecha")
        if turnos.empty:
            st.warning(
                "Configura al menos un turno en Catálogos → Turnos antes de registrar "
                "(esto permite saber después quién estuvo a cargo de cada lote)."
            )
            return
        c_turno, c_tanque = st.columns(2)
        turno_id = c_turno.selectbox(
            "Turno", turnos["turno_id"],
            format_func=lambda x: turnos.set_index("turno_id").loc[x, "nombre"],
        )
        tanque_id = c_tanque.selectbox("Tanque", ["T1", "T2"],
                                        format_func=lambda x: f"Tanque {'1' if x=='T1' else '2'}",
                                        key="prod_tanque")
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

        # Lotes ya registrados en esta fecha+turno (para advertir duplicado en mismo turno)
        ids_este_turno = set()
        if not producciones_existentes.empty and "turno" in producciones_existentes.columns:
            mismo_turno = producciones_existentes[
                (producciones_existentes["fecha"].astype(str) == fecha.isoformat()) &
                (producciones_existentes["turno"].astype(str) == str(turno_id))
            ]
            ids_este_turno = set(mismo_turno["lote_semielaborado_id"].astype(str))

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
                elif codigo in ids_este_turno:
                    st.warning(f"⚠️ El lote '{codigo}' ya fue registrado en este turno — si es el mismo turno, revisa si es un duplicado.")
        elif tipo_producto == "Clara":
            col_c1, col_c2 = st.columns(2)
            codigo_lote = col_c1.text_input("Código de lote — Clara (principal)", value=sugerir_codigo_lote("Clara", fecha))
            codigo_coproducto = col_c2.text_input("Código de lote — Yema (co-producto)", value=sugerir_codigo_lote("Yema", fecha))
            if codigo_lote in ids_existentes:
                st.error(f"⚠️ El código '{codigo_lote}' ya existe.")
            elif codigo_lote in ids_este_turno:
                st.warning(f"⚠️ El lote '{codigo_lote}' ya fue registrado en este turno.")
            if codigo_coproducto in ids_existentes:
                st.error(f"⚠️ El código co-producto '{codigo_coproducto}' ya existe.")
            elif codigo_coproducto in ids_este_turno:
                st.warning(f"⚠️ El co-producto '{codigo_coproducto}' ya fue registrado en este turno.")
            st.caption("La yema que salga también quedará como lote propio en el inventario. Si no hubo yema, deja el campo de yema real en 0 y no se creará ese lote.")
        elif tipo_producto == "Yema":
            col_c1, col_c2 = st.columns(2)
            codigo_lote = col_c1.text_input("Código de lote — Yema (principal)", value=sugerir_codigo_lote("Yema", fecha))
            codigo_coproducto = col_c2.text_input("Código de lote — Clara (co-producto)", value=sugerir_codigo_lote("Clara", fecha))
            if codigo_lote in ids_existentes:
                st.error(f"⚠️ El código '{codigo_lote}' ya existe.")
            elif codigo_lote in ids_este_turno:
                st.warning(f"⚠️ El lote '{codigo_lote}' ya fue registrado en este turno.")
            if codigo_coproducto in ids_existentes:
                st.error(f"⚠️ El código co-producto '{codigo_coproducto}' ya existe.")
            elif codigo_coproducto in ids_este_turno:
                st.warning(f"⚠️ El co-producto '{codigo_coproducto}' ya fue registrado en este turno.")
            st.caption("La clara que salga también quedará como lote propio en el inventario. Si no hubo clara, deja el campo en 0 y no se creará ese lote.")
        else:
            codigo_lote = st.text_input(
                "Código de lote", value=sugerir_codigo_lote(tipo_producto, fecha),
            )
            codigo_coproducto = ""
            if codigo_lote in ids_existentes:
                st.error(f"⚠️ El código '{codigo_lote}' ya existe — usa otro o ve a '✏️ Corregir / eliminar' si fue un error.")
            elif codigo_lote in ids_este_turno:
                st.warning(f"⚠️ El lote '{codigo_lote}' ya fue registrado en este turno — si quieres continuar en otro turno, cambia el turno arriba.")

        recepciones_con_saldo = recepciones[
            pd.to_numeric(recepciones["cubetas_saldo"], errors="coerce").fillna(0) > 0
        ] if not recepciones.empty else pd.DataFrame()

        if recepciones_con_saldo.empty:
            st.warning("No hay huevo disponible en bodega de materia prima para producir.")
            return

        if not categorias.empty:
            mapa_cat_nombre = dict(zip(categorias["categoria_id"], categorias["nombre"]))
        else:
            mapa_cat_nombre = {}

        # Resumen de disponibilidad en bodega (informativo, sin selector de categoría)
        recepciones_con_saldo["cubetas_saldo_num"] = pd.to_numeric(recepciones_con_saldo["cubetas_saldo"], errors="coerce").fillna(0)
        total_cubetas_bodega = int(recepciones_con_saldo["cubetas_saldo_num"].sum())
        detalle_lotes = " · ".join(
            f"{r['recepcion_id']} ({mapa_cat_nombre.get(r['categoria_id'], r['categoria_id'])}, {int(r['cubetas_saldo_num'])} cub.)"
            for _, r in recepciones_con_saldo.sort_values("fecha_vencimiento").iterrows()
        )
        st.info(f"📦 **{total_cubetas_bodega} cubetas disponibles** en bodega: {detalle_lotes}")

        cubetas_necesarias = st.number_input("Cubetas a procesar (total)", min_value=1, step=1)

        # Tabla con selector de lote por fila — el usuario elige qué lotes mezclar
        opciones_lote = [
            f"{r['recepcion_id']} — {mapa_cat_nombre.get(r['categoria_id'], r['categoria_id'])} — saldo: {int(r['cubetas_saldo_num'])} cub."
            for _, r in recepciones_con_saldo.sort_values("fecha_vencimiento").iterrows()
        ]
        mapa_opcion_a_recepcion = {
            f"{r['recepcion_id']} — {mapa_cat_nombre.get(r['categoria_id'], r['categoria_id'])} — saldo: {int(r['cubetas_saldo_num'])} cub.": r["recepcion_id"]
            for _, r in recepciones_con_saldo.iterrows()
        }
        mapa_recepcion_a_costo = dict(zip(recepciones_con_saldo["recepcion_id"], pd.to_numeric(recepciones_con_saldo["costo_cubeta"], errors="coerce").fillna(0)))
        mapa_recepcion_a_categoria = dict(zip(recepciones_con_saldo["recepcion_id"], recepciones_con_saldo["categoria_id"]))

        # Pre-poblar: si hay plan de MP para esta fecha, usarlo; sino FEFO normal
        plan_mp_df = db.get_df("plan_mp_asignado")
        plan_fecha = pd.DataFrame()
        if not plan_mp_df.empty:
            plan_fecha = plan_mp_df[plan_mp_df["fecha"].astype(str) == fecha.isoformat()].copy()
            if not plan_fecha.empty:
                plan_fecha["cubetas_asignadas"] = pd.to_numeric(plan_fecha["cubetas_asignadas"], errors="coerce").fillna(0)
                plan_fecha = plan_fecha[plan_fecha["cubetas_asignadas"] > 0]

        if not plan_fecha.empty:
            st.info(f"📅 Plan de producción del día: cargando lotes asignados por el jefe de planta.")
            filas_sugeridas = []
            for _, prow in plan_fecha.iterrows():
                rec_id = prow["recepcion_id"]
                cub    = float(prow["cubetas_asignadas"])
                rec_match = recepciones_con_saldo[recepciones_con_saldo["recepcion_id"] == rec_id]
                if rec_match.empty:
                    continue
                saldo = float(rec_match.iloc[0]["cubetas_saldo_num"])
                cat   = rec_match.iloc[0]["categoria_id"]
                opcion = f"{rec_id} — {mapa_cat_nombre.get(cat, cat)} — saldo: {int(saldo)} cub."
                filas_sugeridas.append({"lote": opcion, "cubetas_a_tomar": min(cub, saldo)})
        else:
            # FEFO normal por vencimiento
            recepciones_sorted = recepciones_con_saldo.sort_values("fecha_vencimiento")
            filas_sugeridas = []
            restante = cubetas_necesarias
            for _, lote in recepciones_sorted.iterrows():
                if restante <= 0:
                    break
                saldo = float(lote["cubetas_saldo_num"])
                tomar = min(saldo, restante)
                opcion = f"{lote['recepcion_id']} — {mapa_cat_nombre.get(lote['categoria_id'], lote['categoria_id'])} — saldo: {int(saldo)} cub."
                filas_sugeridas.append({"lote": opcion, "cubetas_a_tomar": tomar})
                restante -= tomar

        st.markdown("**Lotes a usar — elige el lote y la cantidad de cubetas de cada uno**")
        st.caption("Puedes mezclar lotes de distintas categorías. Agrega o quita filas según necesites.")
        df_lotes_input = st.data_editor(
            pd.DataFrame(filas_sugeridas) if filas_sugeridas else pd.DataFrame({"lote": pd.Series(dtype="object"), "cubetas_a_tomar": pd.Series(dtype="float")}),
            num_rows="dynamic", use_container_width=True,
            column_config={
                "lote": st.column_config.SelectboxColumn("Lote de MP", options=opciones_lote, width="large"),
                "cubetas_a_tomar": st.column_config.NumberColumn("Cubetas a tomar", min_value=0, step=1),
            },
            key=f"editor_lotes_libre_{int(cubetas_necesarias)}",
        )

        # Construir la tabla interna (recepcion_id + cantidad + costo) a partir de lo que eligió el usuario
        filas_validas = []
        for _, fila in df_lotes_input.iterrows():
            opcion_sel = fila.get("lote", "")
            cant = float(fila.get("cubetas_a_tomar") or 0)
            if not opcion_sel or cant <= 0:
                continue
            rec_id = mapa_opcion_a_recepcion.get(opcion_sel, "")
            if not rec_id:
                continue
            filas_validas.append({
                "recepcion_id": rec_id,
                "cantidad_a_tomar": cant,
                "costo_cubeta": mapa_recepcion_a_costo.get(rec_id, 0),
            })
        df_lotes_editado = pd.DataFrame(filas_validas) if filas_validas else pd.DataFrame(columns=["recepcion_id", "cantidad_a_tomar", "costo_cubeta"])

        # categoria_id: usar la del primer lote seleccionado (para rendimientos teóricos)
        categoria_id = ""
        if filas_validas:
            categoria_id = mapa_recepcion_a_categoria.get(filas_validas[0]["recepcion_id"], "")
        if not categoria_id and not recepciones_con_saldo.empty:
            recepciones_sorted_tmp = recepciones_con_saldo.sort_values("fecha_vencimiento")
            categoria_id = recepciones_sorted_tmp.iloc[0]["categoria_id"] if not recepciones_sorted_tmp.empty else ""

        total_tomado = float(pd.to_numeric(df_lotes_editado["cantidad_a_tomar"], errors="coerce").fillna(0).sum()) if not df_lotes_editado.empty else 0.0
        st.info(f"📦 Vas a consumir **{total_tomado:.0f} cubetas** de bodega de materia prima — revisa que sea correcto antes de guardar.")
        if total_tomado < cubetas_necesarias:
            st.warning(
                f"Las cantidades de los lotes suman {total_tomado:.0f} cubetas, "
                f"pero se necesitan {cubetas_necesarias}. Ajusta antes de guardar."
            )

        st.caption("ℹ️ El registro de personal y horas se hace en **👥 Personal y turnos** — se enlaza por fecha y turno.")
        filas_personal_horas = []
        costo_mano_obra_total = 0.0
        agua_litros = 0.0

        if not categorias.empty and categoria_id in categorias["categoria_id"].values:
            categoria_row = categorias.set_index("categoria_id").loc[categoria_id]
            teorico = rendimiento_teorico(total_tomado, categoria_row)
        else:
            teorico = {"kg_teorico_bruto": total_tomado * 1.724, "kg_liquido_teorico": total_tomado * 1.43,
                       "clara_teorica_kg": 0, "yema_teorica_kg": 0, "cascara_teorica_kg": 0}
            st.caption("ℹ️ Esta categoría no tiene rendimientos configurados — los valores teóricos son estimados.")
        st.markdown("**Valores teóricos calculados** (según cubetas y categoría seleccionadas)")
        col1, col2, col3, col4, col5 = st.columns(5)
        col1.metric("Bruto teórico (kg)", f"{teorico['kg_teorico_bruto']:.2f}", help="Peso del huevo entero, con cáscara")
        col2.metric("Líquido teórico (kg)", f"{teorico['kg_liquido_teorico']:.2f}", help="Sin cáscara — compara esto contra lo real")
        col3.metric("Clara teórica (kg)", f"{teorico['clara_teorica_kg']:.2f}")
        col4.metric("Yema teórica (kg)", f"{teorico['yema_teorica_kg']:.2f}")
        col5.metric("Cáscara teórica (kg)", f"{teorico['cascara_teorica_kg']:.2f}")

        st.markdown("**Valores reales obtenidos** (pesar al final del proceso — compara contra el *líquido* teórico de arriba)")
        st.caption("Llena siempre los 4 valores — se usan para calcular eficiencia de separación y balance de masa.")
        kg_real_input = st.number_input("Kg reales obtenidos (huevo entero o suma líquida)", min_value=0.0, step=0.1)
        clara_real_kg = st.number_input("Clara real (kg)", min_value=0.0, step=0.1)
        yema_real_kg = st.number_input("Yema real (kg)", min_value=0.0, step=0.1)
        cascara_real_kg = st.number_input("Cáscara real (kg)", min_value=0.0, step=0.1)

        # kg_real para el SALDO DEL TANQUE depende del tipo de producto que se va a almacenar:
        # - Huevo entero → lo que se pesó como líquido total
        # - Clara → solo los kg de clara (la yema puede venderse/desecharse aparte)
        # - Yema → solo los kg de yema
        # - Clara y yema → suma de ambos (cada uno va a su propio lote)
        if tipo_producto == "Clara":
            kg_real = clara_real_kg
        elif tipo_producto == "Yema":
            kg_real = yema_real_kg
        elif tipo_producto == "Clara y yema":
            kg_real = clara_real_kg + yema_real_kg
        else:
            # Huevo entero: si llenaron el campo suma úsalo, si no suma clara+yema
            kg_real = kg_real_input if kg_real_input > 0 else (clara_real_kg + yema_real_kg)

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
            st.info(
                "ℹ️ El balance de masa superó el 100% — esto es solo informativo, no impide guardar. "
                "Puede pasar por mezclar huevo de distintas categorías, una báscula no calibrada, u "
                "otro factor real de planta. Si te parece raro, vale la pena revisar cubetas/categoría/kg, "
                "pero no es obligatorio corregir antes de guardar."
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
                if codigo_clara in ids_este_turno or codigo_yema in ids_este_turno:
                    st.error(f"⚠️ Uno de esos lotes ya fue registrado en este turno ({turno_id}). No se puede duplicar en el mismo turno.")
                    return
            else:
                if not codigo_lote:
                    st.error("Ingresa el código de lote.")
                    return
                if codigo_lote in ids_existentes:
                    st.error("Ese código ya existe. Corrígelo antes de guardar.")
                    return
                if codigo_lote in ids_este_turno:
                    st.error(f"⚠️ El lote '{codigo_lote}' ya fue registrado en este turno ({turno_id}). No se puede duplicar en el mismo turno — si es otra producción, usa otro código o cambia el turno.")
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

            costo_mano_obra_total = 0.0
            detalle_personal = []
            detalle_personal = []

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
                "turno": turno_id,
                "tanque_id": tanque_id,
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
                st.rerun()
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

                # Co-producto: si se produjo Clara también crea lote de Yema (y viceversa)
                kg_coproducto = yema_real_kg if tipo_producto == "Clara" else (clara_real_kg if tipo_producto == "Yema" else 0)
                tipo_coproducto = "Yema" if tipo_producto == "Clara" else ("Clara" if tipo_producto == "Yema" else "")
                if tipo_coproducto and kg_coproducto > 0 and codigo_coproducto:
                    db.append_row("produccion_semielaborados", {
                        **base_row,
                        "lote_semielaborado_id": codigo_coproducto,
                        "tipo_producto": tipo_coproducto,
                        "kg_liquido_teorico": 0,
                        "kg_real": kg_coproducto,
                        "clara_teorica_kg": 0,
                        "clara_real_kg": clara_real_kg if tipo_coproducto == "Clara" else 0,
                        "yema_teorica_kg": 0,
                        "yema_real_kg": yema_real_kg if tipo_coproducto == "Yema" else 0,
                        "cascara_teorica_kg": 0,
                        "cascara_real_kg": 0,
                        "costo_huevo": 0,
                        "costo_insumos": 0,
                        "costo_mano_obra": 0,
                        "costo_total": 0,
                        "costo_unitario_kg": 0,
                        "kg_saldo": kg_coproducto,
                        "balance_masa_pct": 0,
                        "observaciones": f"Co-producto de lote {codigo_lote}",
                    })

                msg_lotes = codigo_lote
                if tipo_coproducto and kg_coproducto > 0 and codigo_coproducto:
                    msg_lotes += f" + co-producto {codigo_coproducto} ({tipo_coproducto}, {kg_coproducto:.1f} kg)"

                if ve_costos(rol):
                    st.success(f"Lote {msg_lotes} guardado — costo total {costo_total:,.2f}, costo/kg {costo_unitario_kg:,.2f}")
                else:
                    st.success(f"Lote {msg_lotes} guardado.")
                st.rerun()

    with tab_inventario:
        try:
            df_inv = db.get_df("produccion_semielaborados")
            if df_inv.empty:
                st.info("Todavía no hay lotes de semielaborado.")
            else:
                df_inv["kg_saldo"] = pd.to_numeric(df_inv["kg_saldo"], errors="coerce").fillna(0)
                disponibles = df_inv[df_inv["kg_saldo"] >= 0.1].copy()
                if disponibles.empty:
                    st.info("No hay kg disponibles en los tanques actualmente.")
                else:
                    cols_show = [c for c in ["lote_semielaborado_id","fecha","tipo_producto","tanque_id","kg_saldo","costo_unitario_kg"] if c in disponibles.columns]
                    tabla_disp = disponibles[cols_show].copy()
                    tabla_disp["kg_saldo"] = tabla_disp["kg_saldo"].round(1)
                    if "tanque_id" in tabla_disp.columns:
                        tabla_disp["tanque_id"] = tabla_disp["tanque_id"].fillna("Sin asignar")
                    st.dataframe(tabla_disp, use_container_width=True, hide_index=True)
        except Exception as e:
            st.error(f"Error al cargar inventario: {e}")

    with tab_historial:
        df_hist = db.get_df("produccion_semielaborados")
        if df_hist.empty:
            st.info("Todavía no hay lotes registrados.")
        else:
            df_hist["kg_saldo"] = pd.to_numeric(df_hist["kg_saldo"], errors="coerce").fillna(0)
            df_hist["kg_real"] = pd.to_numeric(df_hist["kg_real"], errors="coerce").fillna(0)

            # Filtros
            c1, c2, c3 = st.columns(3)
            tipos = ["Todos"] + sorted(df_hist["tipo_producto"].dropna().unique().tolist())
            filtro_tipo = c1.selectbox("Tipo de producto", tipos, key="hist_semi_tipo")
            desde = c2.date_input("Desde", value=datetime.date.today() - datetime.timedelta(days=30), key="hist_semi_desde")
            hasta = c3.date_input("Hasta", value=datetime.date.today(), key="hist_semi_hasta")

            df_mostrar = df_hist.copy()
            if filtro_tipo != "Todos":
                df_mostrar = df_mostrar[df_mostrar["tipo_producto"] == filtro_tipo]
            df_mostrar = df_mostrar[
                (df_mostrar["fecha"].astype(str) >= desde.isoformat()) &
                (df_mostrar["fecha"].astype(str) <= hasta.isoformat())
            ]

            df_mostrar["estado"] = df_mostrar["kg_saldo"].apply(
                lambda s: "✅ Despachado/usado" if s == 0 else f"🟡 {s:.1f} kg en tanque"
            )

            columnas_hist = ["lote_semielaborado_id", "fecha", "tipo_producto", "kg_real", "estado"]
            if ve_costos(rol):
                columnas_hist += ["costo_unitario_kg", "costo_total"]

            st.dataframe(
                df_mostrar[[c for c in columnas_hist if c in df_mostrar.columns]].sort_values("fecha", ascending=False),
                use_container_width=True, hide_index=True,
            )
            st.caption(
                f"Total: {len(df_mostrar)} lote(s) | "
                f"En tanque: {(df_mostrar['kg_saldo'] > 0).sum()} | "
                f"Despachados/usados: {(df_mostrar['kg_saldo'] == 0).sum()} | "
                f"Kg producidos: {df_mostrar['kg_real'].sum():,.1f} kg"
            )

    with tab_perdida:
        st.caption(
            "Para clara o yema sobrante sin cliente (u otro semielaborado dañado/vencido) "
            "que se va a desechar — reduce el saldo del tanque y queda registrado en mermas."
        )
        df_disp = db.get_df("produccion_semielaborados")
        if df_disp.empty:
            st.info("Todavía no hay lotes de semielaborado.")
        else:
            df_disp["kg_saldo"] = pd.to_numeric(df_disp["kg_saldo"], errors="coerce").fillna(0)
            disponibles_perdida = df_disp[df_disp["kg_saldo"] > 0]
            if disponibles_perdida.empty:
                st.info("No hay lotes con saldo disponible para registrar pérdida.")
            else:
                lote_perdida_id = st.selectbox(
                    "Lote semielaborado", disponibles_perdida["lote_semielaborado_id"],
                    format_func=lambda x: (
                        f"{x} — {disponibles_perdida.set_index('lote_semielaborado_id').loc[x, 'tipo_producto']} "
                        f"(saldo {disponibles_perdida.set_index('lote_semielaborado_id').loc[x, 'kg_saldo']:.1f} kg)"
                    ),
                )
                fila_lote = disponibles_perdida.set_index("lote_semielaborado_id").loc[lote_perdida_id]
                saldo_disponible = float(fila_lote["kg_saldo"])
                costo_unit_lote = float(pd.to_numeric(fila_lote.get("costo_unitario_kg", 0), errors="coerce") or 0)

                kg_desechar = st.number_input(
                    "Kg a desechar", min_value=0.0, max_value=saldo_disponible, step=0.5,
                )
                causa = st.selectbox(
                    "Causa", ["Sobrante sin cliente", "Dañado", "Vencido", "Otro"],
                )
                observaciones_perdida = st.text_area("Observaciones", "", key="perdida_semi_obs")

                if ve_costos(rol) and kg_desechar > 0:
                    st.caption(f"Costo estimado de la pérdida: {kg_desechar * costo_unit_lote:,.2f}")

                if st.button("🗑️ Registrar pérdida"):
                    if kg_desechar <= 0:
                        st.error("Ingresa una cantidad mayor a 0.")
                    else:
                        nuevo_saldo = saldo_disponible - kg_desechar
                        db.update_row(
                            "produccion_semielaborados", "lote_semielaborado_id", lote_perdida_id,
                            {"kg_saldo": nuevo_saldo},
                        )
                        merma_id = db.siguiente_id("mermas_semielaborado", "MS", datetime.date.today())
                        db.append_row("mermas_semielaborado", {
                            "merma_id": merma_id,
                            "fecha": datetime.date.today().isoformat(),
                            "lote_semielaborado_id": lote_perdida_id,
                            "kg_desechado": kg_desechar,
                            "causa": causa,
                            "costo_estimado": kg_desechar * costo_unit_lote,
                            "usuario": username,
                            "observaciones": observaciones_perdida,
                        })
                        st.success(f"Pérdida {merma_id} registrada — saldo del lote actualizado a {nuevo_saldo:.1f} kg.")
                        st.rerun()

        st.divider()
        st.markdown("##### 📋 Historial de pérdidas registradas")
        df_mermas = db.get_df("mermas_semielaborado")
        if df_mermas.empty:
            st.info("No hay pérdidas registradas todavía.")
        else:
            df_mermas_vista = df_mermas.copy()
            # Agregar tipo de producto desde la tabla de producción
            prod_tipos = db.get_df("produccion_semielaborados")
            if not prod_tipos.empty:
                df_mermas_vista = df_mermas_vista.merge(
                    prod_tipos[["lote_semielaborado_id", "tipo_producto"]],
                    on="lote_semielaborado_id", how="left",
                )
            columnas_mermas = [c for c in [
                "fecha", "lote_semielaborado_id", "tipo_producto",
                "kg_desechado", "causa", "observaciones",
            ] if c in df_mermas_vista.columns]
            if ve_costos(rol):
                columnas_mermas.append("costo_estimado")
            st.dataframe(
                df_mermas_vista[columnas_mermas].sort_values("fecha", ascending=False),
                use_container_width=True, hide_index=True,
            )
            kg_total_desechado = pd.to_numeric(df_mermas_vista["kg_desechado"], errors="coerce").fillna(0).sum()
            st.caption(f"Total desechado histórico: **{kg_total_desechado:,.1f} kg** en {len(df_mermas_vista)} registro(s)")
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
                st.info(
                    f"ℹ️ {len(anomalos)} lote(s) muestran rendimiento mayor a 100% — puede deberse a "
                    f"mezclar huevo de varias categorías u otros factores reales de planta, no "
                    f"necesariamente un error. Si quieres, revisa el kg real digitado o el "
                    f"kg_promedio_cubeta de la categoría usada."
                )
            if not balance_anomalo.empty:
                st.info(
                    f"ℹ️ {len(balance_anomalo)} lote(s) muestran un balance de masa mayor a 100% — "
                    f"solo informativo, no es necesariamente un error (mezcla de categorías, "
                    f"variación normal de báscula, etc.)."
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

    with tab_rendimiento:
        st.markdown("##### ⚖️ Teórico vs. real")
        df_rend = db.get_df("produccion_semielaborados")
        if df_rend.empty:
            st.info("No hay lotes registrados todavía.")
        else:
            for col in ["kg_liquido_teorico","kg_real","clara_teorica_kg","clara_real_kg",
                        "yema_teorica_kg","yema_real_kg","cascara_teorica_kg","cascara_real_kg","balance_masa_pct"]:
                if col in df_rend.columns:
                    df_rend[col] = pd.to_numeric(df_rend[col], errors="coerce").fillna(0)
            cols_rend = [c for c in ["lote_semielaborado_id","fecha","tipo_producto",
                                      "kg_liquido_teorico","kg_real","clara_teorica_kg","clara_real_kg",
                                      "yema_teorica_kg","yema_real_kg","cascara_teorica_kg","cascara_real_kg",
                                      "balance_masa_pct"] if c in df_rend.columns]
            st.dataframe(df_rend[cols_rend].sort_values("fecha", ascending=False),
                         use_container_width=True, hide_index=True)

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
