"""
Produccion de semielaborados: quiebre de huevo (y separacion si aplica),
consumo de materia prima e insumos de limpieza, mano de obra de la jornada,
y el control teorico vs. real que sostiene el costeo de toda la cadena.
"""
import datetime
import streamlit as st
import pandas as pd
from utils.costing import costo_ponderado, sugerir_codigo_lote
from utils.horas_trabajo import calcular_horas_sesion
from utils.permisos import ve_costos, es_admin, es_admin_o_jefe_planta
from utils.bitacora import log_cambio

OPCIONES_TANQUE = ["T1", "T2", ""]
NOMBRES_TANQUE = {"T1": "Tanque 1", "T2": "Tanque 2", "": "Sin tanque (va directo a granel)"}


def _selector_tanque(label, key, contenedor=None):
    contenedor = contenedor if contenedor is not None else st
    return contenedor.selectbox(label, OPCIONES_TANQUE, format_func=lambda x: NOMBRES_TANQUE[x], key=key)


def _tanque_ocupado_por_otro(producciones_df, tanque_id, tipo_producto_nuevo):
    """Si tanque_id ya tiene saldo de un tipo de producto DISTINTO al que se
    quiere guardar, devuelve (tipo_ocupante, kg_ocupante); si no hay conflicto
    (tanque vacio, sin tanque asignado, o mismo tipo de producto), (None, None)."""
    if not tanque_id or producciones_df.empty or "tanque_id" not in producciones_df.columns:
        return None, None
    df = producciones_df.copy()
    df["kg_saldo"] = pd.to_numeric(df["kg_saldo"], errors="coerce").fillna(0)
    ocupantes = df[
        (df["tanque_id"].astype(str) == str(tanque_id))
        & (df["kg_saldo"] > 0)
        & (df["tipo_producto"] != tipo_producto_nuevo)
    ]
    if ocupantes.empty:
        return None, None
    return str(ocupantes.iloc[0]["tipo_producto"]), float(ocupantes["kg_saldo"].sum())


def render(db, username, rol):
    st.title("Producción de semielaborados")
    tab_nueva, tab_inventario, tab_historial, tab_perdida, tab_ajuste, tab_rendimiento, tab_corregir = st.tabs(
        ["Nueva producción", "Inventario de tanques", "📋 Historial",
         "⚠️ Registrar pérdida", "➕ Ajustar saldo", "Teórico vs. real", "✏️ Corregir / eliminar"]
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
        turno_id = st.selectbox(
            "Turno", turnos["turno_id"],
            format_func=lambda x: turnos.set_index("turno_id").loc[x, "nombre"],
        )
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
            # Lineas de pedido vinculadas a esta orden
            lineas_aviso = db.get_df("pedidos_lineas")
            pedidos_cab_aviso = db.get_df("pedidos")
            if not lineas_aviso.empty and "orden_produccion_vinculada" in lineas_aviso.columns:
                vinc = lineas_aviso[
                    (lineas_aviso["orden_produccion_vinculada"].astype(str) == str(orden_produccion))
                    & (~lineas_aviso["producido"].astype(str).str.upper().isin(["TRUE", "1", "SI", "SÍ"]))
                ].copy()
                # Excluir lineas de pedidos cancelados y traer cliente + urgente
                if not vinc.empty and not pedidos_cab_aviso.empty:
                    vinc = vinc.merge(
                        pedidos_cab_aviso[[c for c in ["pedido_id", "cliente_id", "estado", "urgente"] if c in pedidos_cab_aviso.columns]],
                        on="pedido_id", how="left",
                    )
                    if "estado" in vinc.columns:
                        vinc = vinc[vinc["estado"].fillna("pendiente").astype(str).str.lower() != "cancelado"]
                if not vinc.empty:
                    clientes_df_aviso = db.get_df("clientes")
                    mapa_cli = dict(zip(clientes_df_aviso["cliente_id"], clientes_df_aviso["nombre"])) if not clientes_df_aviso.empty else {}
                    total_kg_vinc = float(pd.to_numeric(vinc["cantidad_kg"], errors="coerce").fillna(0).sum())
                    with st.container(border=True):
                        st.markdown(f"##### 📋 Esta orden tiene **{len(vinc)}** línea(s) de pedido vinculada(s):")
                        for _, pv in vinc.iterrows():
                            es_urg = str(pv.get("urgente", "")).upper() in ("TRUE", "1", "SI", "SÍ")
                            kg_v = float(pd.to_numeric(pv.get("cantidad_kg", 0), errors="coerce") or 0)
                            marca_urg = "⚡ **URGENTE** " if es_urg else ""
                            st.markdown(
                                f"- {marca_urg}`{pv['pedido_id']}` — "
                                f"{mapa_cli.get(pv['cliente_id'], pv['cliente_id'])} — "
                                f"**{kg_v:.1f} kg** {pv.get('tipo_producto', '')}"
                            )
                        st.info(
                            f"💡 Total kg de líneas vinculadas: **{total_kg_vinc:.1f} kg**. "
                            f"Ajusta las cubetas más abajo si necesitas incluir esa producción extra."
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
            col_t1, col_t2 = st.columns(2)
            tanque_clara = _selector_tanque("Tanque para la clara", key="prod_tanque_clara", contenedor=col_t1)
            tanque_yema = _selector_tanque("Tanque para la yema", key="prod_tanque_yema", contenedor=col_t2)
            for tq, tp in ((tanque_clara, "Clara"), (tanque_yema, "Yema")):
                ocupante, kg_ocupante = _tanque_ocupado_por_otro(producciones_existentes, tq, tp)
                if ocupante:
                    st.error(
                        f"⚠️ {NOMBRES_TANQUE[tq]} ya tiene {kg_ocupante:.1f} kg de '{ocupante}' — "
                        f"no se puede mezclar con '{tp}'. Usa el otro tanque, pasteuriza y vacía este "
                        f"primero, o elige 'Sin tanque' para mandarlo directo a granel."
                    )
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
            col_t1, col_t2 = st.columns(2)
            tanque_lote = _selector_tanque("Tanque para la clara (principal)", key="prod_tanque_principal", contenedor=col_t1)
            tanque_coproducto = _selector_tanque("Tanque para la yema (co-producto, solo si hay)", key="prod_tanque_coprod", contenedor=col_t2)
            # El aviso del tanque principal se muestra ya (siempre se va a usar). El del
            # co-producto se valida solo al guardar, porque hasta no ingresar los kg reales
            # más abajo no se sabe si en verdad va a haber yema co-producto que guardar.
            ocupante, kg_ocupante = _tanque_ocupado_por_otro(producciones_existentes, tanque_lote, "Clara")
            if ocupante:
                st.error(
                    f"⚠️ {NOMBRES_TANQUE[tanque_lote]} ya tiene {kg_ocupante:.1f} kg de '{ocupante}' — "
                    f"no se puede mezclar con 'Clara'. Usa el otro tanque, pasteuriza y vacía este "
                    f"primero, o elige 'Sin tanque' para mandarlo directo a granel."
                )
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
            col_t1, col_t2 = st.columns(2)
            tanque_lote = _selector_tanque("Tanque para la yema (principal)", key="prod_tanque_principal", contenedor=col_t1)
            tanque_coproducto = _selector_tanque("Tanque para la clara (co-producto, solo si hay)", key="prod_tanque_coprod", contenedor=col_t2)
            # Igual que en "Clara": el co-producto solo se valida al guardar, no aquí,
            # porque todavía no se sabe si va a haber clara co-producto real que guardar.
            ocupante, kg_ocupante = _tanque_ocupado_por_otro(producciones_existentes, tanque_lote, "Yema")
            if ocupante:
                st.error(
                    f"⚠️ {NOMBRES_TANQUE[tanque_lote]} ya tiene {kg_ocupante:.1f} kg de '{ocupante}' — "
                    f"no se puede mezclar con 'Yema'. Usa el otro tanque, pasteuriza y vacía este "
                    f"primero, o elige 'Sin tanque' para mandarlo directo a granel."
                )
        else:
            codigo_lote = st.text_input(
                "Código de lote", value=sugerir_codigo_lote(tipo_producto, fecha),
            )
            codigo_coproducto = ""
            if codigo_lote in ids_existentes:
                st.error(f"⚠️ El código '{codigo_lote}' ya existe — usa otro o ve a '✏️ Corregir / eliminar' si fue un error.")
            elif codigo_lote in ids_este_turno:
                st.warning(f"⚠️ El lote '{codigo_lote}' ya fue registrado en este turno — si quieres continuar en otro turno, cambia el turno arriba.")
            tanque_lote = _selector_tanque("Tanque", key="prod_tanque_principal")
            tanque_coproducto = ""
            ocupante, kg_ocupante = _tanque_ocupado_por_otro(producciones_existentes, tanque_lote, tipo_producto)
            if ocupante:
                st.error(
                    f"⚠️ {NOMBRES_TANQUE[tanque_lote]} ya tiene {kg_ocupante:.1f} kg de '{ocupante}' — "
                    f"no se puede mezclar con '{tipo_producto}'. Usa el otro tanque, pasteuriza y vacía "
                    f"este primero, o elige 'Sin tanque' para mandarlo directo a granel."
                )

        # Solo recepciones activas (nueva columna 'estado' en la parte 1)
        if "estado" in recepciones.columns:
            recepciones_activas = recepciones[
                recepciones["estado"].fillna("activo").astype(str).str.strip().str.lower().isin(["", "activo"])
            ]
        else:
            recepciones_activas = recepciones

        recepciones_con_saldo = recepciones_activas[
            pd.to_numeric(recepciones_activas["cubetas_saldo"], errors="coerce").fillna(0) > 0
        ] if not recepciones_activas.empty else pd.DataFrame()

        if recepciones_con_saldo.empty:
            st.warning("No hay huevo disponible en bodega de materia prima para producir.")
            return

        # Total disponible en bodega — el operador NO elige de qué lotes,
        # el sistema descuenta FIFO estricto por fecha de recepcion al guardar.
        recepciones_con_saldo = recepciones_con_saldo.copy()
        recepciones_con_saldo["cubetas_saldo_num"] = pd.to_numeric(
            recepciones_con_saldo["cubetas_saldo"], errors="coerce"
        ).fillna(0)
        total_cubetas_bodega = int(recepciones_con_saldo["cubetas_saldo_num"].sum())
        st.info(
            f"📦 **{total_cubetas_bodega} cubetas disponibles** en bodega "
            f"(la bodega funciona como un pool — se descontarán FIFO por fecha "
            f"de recepción cuando guardes)."
        )

        cubetas_necesarias = st.number_input(
            "Cubetas a procesar (total)", min_value=1, step=1, key="prod_cubetas",
        )
        if cubetas_necesarias > total_cubetas_bodega:
            st.error(
                f"⚠️ Pediste {cubetas_necesarias} cubetas pero en bodega solo hay "
                f"{total_cubetas_bodega}. Ajusta la cantidad."
            )

        # ================== FACTORES DE RENDIMIENTO EDITABLES ==================
        # Defaults por producto (kg de liquido por cubeta y gramos de cascara
        # por huevo). El operador los ajusta segun el tamano/lote del huevo.
        DEFAULTS_RENDIMIENTO = {
            "Huevo entero": 1.5,
            "Clara":        1.05,
            "Yema":         0.54,
        }
        DEFAULT_GR_CASCARA = 7.0
        HUEVOS_POR_CUBETA = 30  # fijo

        st.markdown("**Factores de rendimiento** — ajústalos según el tamaño del huevo recibido.")
        st.caption(
            "El *peso del huevo estimado* de abajo te sirve de referencia — "
            "si te sale muy distinto al peso real que sabes, revisa el rendimiento "
            "o los gramos de cáscara."
        )

        if tipo_producto == "Huevo entero":
            col_f1, col_f2 = st.columns(2)
            factor_liquido = col_f1.number_input(
                "Rendimiento líquido (kg/cubeta)", min_value=0.0, step=0.01,
                value=DEFAULTS_RENDIMIENTO["Huevo entero"], key="fac_liq_he",
            )
            gr_cascara = col_f2.number_input(
                "Peso de cáscara (g/huevo)", min_value=0.0, step=0.1,
                value=DEFAULT_GR_CASCARA, key="fac_cas_he",
            )
            peso_huevo_g = (factor_liquido * 1000 / HUEVOS_POR_CUBETA) + gr_cascara
            # huevo entero no separa: los teoricos de clara y yema son 0
            factor_clara = 0.0
            factor_yema = 0.0
            factor_liquido_total = factor_liquido
        elif tipo_producto == "Clara":
            col_f1, col_f2, col_f3 = st.columns(3)
            factor_liquido = col_f1.number_input(
                "Rendimiento clara (kg/cubeta)", min_value=0.0, step=0.01,
                value=DEFAULTS_RENDIMIENTO["Clara"], key="fac_liq_cl",
            )
            factor_yema_ref = col_f2.number_input(
                "Rendimiento yema co-producto (kg/cubeta)",
                min_value=0.0, step=0.01,
                value=DEFAULTS_RENDIMIENTO["Yema"], key="fac_yema_ref_cl",
                help="Se usa para calcular el peso del huevo estimado y el teorico de yema.",
            )
            gr_cascara = col_f3.number_input(
                "Peso de cáscara (g/huevo)", min_value=0.0, step=0.1,
                value=DEFAULT_GR_CASCARA, key="fac_cas_cl",
            )
            peso_huevo_g = ((factor_liquido + factor_yema_ref) * 1000 / HUEVOS_POR_CUBETA) + gr_cascara
            # el huevo se separa completo: teoricos de ambos componentes visibles
            factor_clara = factor_liquido
            factor_yema = factor_yema_ref
            # el "liquido total" del huevo = clara + yema (para el balance de masa)
            factor_liquido_total = factor_clara + factor_yema
        elif tipo_producto == "Yema":
            col_f1, col_f2, col_f3 = st.columns(3)
            factor_liquido = col_f1.number_input(
                "Rendimiento yema (kg/cubeta)", min_value=0.0, step=0.01,
                value=DEFAULTS_RENDIMIENTO["Yema"], key="fac_liq_ye",
            )
            factor_clara_ref = col_f2.number_input(
                "Rendimiento clara co-producto (kg/cubeta)",
                min_value=0.0, step=0.01,
                value=DEFAULTS_RENDIMIENTO["Clara"], key="fac_clara_ref_ye",
                help="Se usa para calcular el peso del huevo estimado y el teorico de clara.",
            )
            gr_cascara = col_f3.number_input(
                "Peso de cáscara (g/huevo)", min_value=0.0, step=0.1,
                value=DEFAULT_GR_CASCARA, key="fac_cas_ye",
            )
            peso_huevo_g = ((factor_liquido + factor_clara_ref) * 1000 / HUEVOS_POR_CUBETA) + gr_cascara
            factor_clara = factor_clara_ref
            factor_yema = factor_liquido
            factor_liquido_total = factor_clara + factor_yema
        else:  # Clara y yema
            col_f1, col_f2, col_f3 = st.columns(3)
            factor_clara = col_f1.number_input(
                "Rendimiento clara (kg/cubeta)", min_value=0.0, step=0.01,
                value=DEFAULTS_RENDIMIENTO["Clara"], key="fac_cl_cy",
            )
            factor_yema = col_f2.number_input(
                "Rendimiento yema (kg/cubeta)", min_value=0.0, step=0.01,
                value=DEFAULTS_RENDIMIENTO["Yema"], key="fac_ye_cy",
            )
            gr_cascara = col_f3.number_input(
                "Peso de cáscara (g/huevo)", min_value=0.0, step=0.1,
                value=DEFAULT_GR_CASCARA, key="fac_cas_cy",
            )
            peso_huevo_g = ((factor_clara + factor_yema) * 1000 / HUEVOS_POR_CUBETA) + gr_cascara
            factor_liquido_total = factor_clara + factor_yema
            factor_liquido = factor_liquido_total

        # Indicador visual del peso del huevo estimado (sanity check)
        col_p1, col_p2, col_p3 = st.columns(3)
        col_p1.metric("🥚 Peso del huevo estimado", f"{peso_huevo_g:.1f} g")
        col_p2.metric("Kg líquido estimado", f"{cubetas_necesarias * factor_liquido_total:.1f} kg")
        col_p3.metric(
            "Kg cáscara estimada",
            f"{cubetas_necesarias * HUEVOS_POR_CUBETA * gr_cascara / 1000:.1f} kg",
        )

        # Aviso si el peso del huevo estimado se sale de un rango razonable (45-75 g)
        if peso_huevo_g > 0 and (peso_huevo_g < 45 or peso_huevo_g > 75):
            st.warning(
                f"⚠️ El peso del huevo estimado ({peso_huevo_g:.1f} g) está fuera del "
                f"rango típico (45-75 g). Revisa los factores — algo puede estar mal."
            )

        st.caption(
            "ℹ️ El registro de personal y horas se hace en **👥 Personal y turnos** — "
            "se enlaza por fecha y turno."
        )
        filas_personal_horas = []
        costo_mano_obra_total = 0.0
        agua_litros = 0.0

        # ================== VALORES TEORICOS (basados en factores) ==================
        # Reemplaza el calculo por categoria: ahora se basa en los factores editables.
        # Se mantiene el diccionario 'teorico' para compatibilidad con el resto del codigo.
        cubetas_para_teorico = float(cubetas_necesarias)
        kg_liquido_teorico = cubetas_para_teorico * factor_liquido_total
        kg_cascara_teorico = cubetas_para_teorico * HUEVOS_POR_CUBETA * gr_cascara / 1000
        teorico = {
            "kg_teorico_bruto":    kg_liquido_teorico + kg_cascara_teorico,
            "kg_liquido_teorico":  kg_liquido_teorico,
            "clara_teorica_kg":    cubetas_para_teorico * factor_clara,
            "yema_teorica_kg":     cubetas_para_teorico * factor_yema,
            "cascara_teorica_kg":  kg_cascara_teorico,
        }
        # categoria_id se deja vacio para producciones nuevas — la trazabilidad
        # ahora va por lote y proveedor, no por categoria del huevo.
        categoria_id = ""

        st.markdown("**Valores teóricos calculados** (según cubetas y factores)")
        col1, col2, col3, col4, col5 = st.columns(5)
        col1.metric("Bruto teórico (kg)", f"{teorico['kg_teorico_bruto']:.2f}", help="Líquido + cáscara")
        col2.metric("Líquido teórico (kg)", f"{teorico['kg_liquido_teorico']:.2f}", help="Sin cáscara — compara contra lo real")
        col3.metric("Clara teórica (kg)", f"{teorico['clara_teorica_kg']:.2f}")
        col4.metric("Yema teórica (kg)", f"{teorico['yema_teorica_kg']:.2f}")
        col5.metric("Cáscara teórica (kg)", f"{teorico['cascara_teorica_kg']:.2f}")

        st.markdown("**Valores reales obtenidos** (pesar al final del proceso — compara contra el *líquido* teórico de arriba)")
        st.caption(
            "Los campos vienen **pre-llenados con el valor teórico** según los "
            "factores de arriba. Confírmalos o ajústalos con lo que marque la balanza."
        )
        # Pre-llenados según el tipo de producto y los factores editables.
        # Para Clara/Yema, el co-producto tambien viene pre-llenado con su
        # teorico (usando el rendimiento de referencia), para que el operador
        # solo confirme o ajuste con la balanza.
        if tipo_producto == "Huevo entero":
            default_liquido = teorico["kg_liquido_teorico"]
            default_clara = 0.0
            default_yema = 0.0
        elif tipo_producto == "Clara":
            default_liquido = teorico["clara_teorica_kg"]
            default_clara = teorico["clara_teorica_kg"]
            default_yema = teorico["yema_teorica_kg"]  # co-producto pre-llenado
        elif tipo_producto == "Yema":
            default_liquido = teorico["yema_teorica_kg"]
            default_clara = teorico["clara_teorica_kg"]  # co-producto pre-llenado
            default_yema = teorico["yema_teorica_kg"]
        else:  # Clara y yema
            default_liquido = 0.0
            default_clara = teorico["clara_teorica_kg"]
            default_yema = teorico["yema_teorica_kg"]
        default_cascara = teorico["cascara_teorica_kg"]

        kg_real_input = st.number_input(
            "Kg reales obtenidos (huevo entero o suma líquida)",
            min_value=0.0, step=0.1, value=float(round(default_liquido, 2)),
            key="prod_kg_real_input",
        )
        clara_real_kg = st.number_input(
            "Clara real (kg)", min_value=0.0, step=0.1,
            value=float(round(default_clara, 2)), key="prod_clara_real",
        )
        yema_real_kg = st.number_input(
            "Yema real (kg)", min_value=0.0, step=0.1,
            value=float(round(default_yema, 2)), key="prod_yema_real",
        )
        cascara_real_kg = st.number_input(
            "Cáscara real (kg)", min_value=0.0, step=0.1,
            value=float(round(default_cascara, 2)), key="prod_cascara_real",
        )

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
            # En el flujo nuevo, cubetas_necesarias es lo que se va a consumir
            total_tomado = float(cubetas_necesarias)
            if total_tomado <= 0:
                st.error("Ingresa una cantidad de cubetas mayor a cero.")
                return
            if total_tomado > total_cubetas_bodega:
                st.error(
                    f"No hay suficientes cubetas en bodega ({total_cubetas_bodega} disponibles, "
                    f"{total_tomado:.0f} pedidas). Ajusta antes de guardar."
                )
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

            # No mezclar productos distintos en un mismo tanque físico.
            if tipo_producto == "Clara y yema":
                pares_tanque = [(tanque_clara, "Clara"), (tanque_yema, "Yema")]
            elif tipo_producto in ("Clara", "Yema"):
                pares_tanque = [(tanque_lote, tipo_producto)]
                kg_coprod_check = yema_real_kg if tipo_producto == "Clara" else clara_real_kg
                tipo_coprod_check = "Yema" if tipo_producto == "Clara" else "Clara"
                if kg_coprod_check > 0 and codigo_coproducto:
                    pares_tanque.append((tanque_coproducto, tipo_coprod_check))
            else:
                pares_tanque = [(tanque_lote, tipo_producto)]

            for tq, tp in pares_tanque:
                ocupante, kg_ocupante = _tanque_ocupado_por_otro(producciones_existentes, tq, tp)
                if ocupante:
                    st.error(
                        f"⚠️ {NOMBRES_TANQUE[tq]} ya tiene {kg_ocupante:.1f} kg de '{ocupante}' — "
                        f"no se puede mezclar con '{tp}'. Usa el otro tanque, pasteuriza y vacía este "
                        f"primero, o elige 'Sin tanque' para mandarlo directo a granel."
                    )
                    return

            # ================== FIFO ESTRICTO POR FECHA ==================
            # Descuenta las cubetas de las recepciones activas, la mas antigua
            # primero. Genera un registro en consumo_mp_produccion por cada
            # tramo consumido, preservando la trazabilidad por proveedor.
            recepciones_fifo = recepciones_con_saldo.copy()
            recepciones_fifo["fecha_dt"] = pd.to_datetime(recepciones_fifo["fecha"], errors="coerce")
            recepciones_fifo = recepciones_fifo.sort_values("fecha_dt", na_position="last")

            detalle_lotes = []
            restante = total_tomado
            for _, lote in recepciones_fifo.iterrows():
                if restante <= 0:
                    break
                saldo = float(lote["cubetas_saldo_num"])
                if saldo <= 0:
                    continue
                tomar = min(saldo, restante)
                detalle_lotes.append({
                    "recepcion_id": lote["recepcion_id"],
                    "cantidad_a_tomar": tomar,
                    "costo_cubeta": float(pd.to_numeric(lote.get("costo_cubeta", 0), errors="coerce") or 0),
                })
                restante -= tomar

            if restante > 0.01:
                st.error(
                    f"No hay suficientes cubetas en bodega para completar el consumo. "
                    f"Faltaron {restante:.0f} cubetas. Ajusta antes de guardar."
                )
                return

            costo_huevo_total = sum(
                d["cantidad_a_tomar"] * d["costo_cubeta"] for d in detalle_lotes
            )
            costo_unit_huevo = costo_huevo_total / total_tomado if total_tomado > 0 else 0

            costo_insumos_total = 0.0
            detalle_insumos = []
            costo_mano_obra_total = 0.0
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
                    "tanque_id": tanque_clara,
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
                    "tanque_id": tanque_yema,
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

                # Registro de cascara (subproducto): se crea UNA sola vez, asociada
                # al lote de clara (que es el lote_referencia en el caso clara+yema).
                if cascara_real_kg > 0:
                    cascara_id = db.siguiente_id("produccion_cascara", "CASC", fecha)
                    db.append_row("produccion_cascara", {
                        "cascara_id": cascara_id,
                        "fecha": fecha.isoformat(),
                        "lote_semielaborado_origen": codigo_clara,
                        "kg": cascara_real_kg,
                        "kg_saldo": cascara_real_kg,
                        "estado": "en bodega",
                        "usuario": username,
                        "observaciones": f"Del quiebre {codigo_clara} + {codigo_yema}",
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
                    "tanque_id": tanque_lote,
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
                        "tanque_id": tanque_coproducto,
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

                # Registro de cascara (subproducto): se crea UNA sola vez, asociada
                # al lote principal (NO al co-producto), para evitar doble conteo.
                if cascara_real_kg > 0:
                    cascara_id = db.siguiente_id("produccion_cascara", "CASC", fecha)
                    obs_cascara = f"Del lote {codigo_lote}"
                    if tipo_coproducto and codigo_coproducto:
                        obs_cascara += f" (co-producto {codigo_coproducto})"
                    db.append_row("produccion_cascara", {
                        "cascara_id": cascara_id,
                        "fecha": fecha.isoformat(),
                        "lote_semielaborado_origen": codigo_lote,
                        "kg": cascara_real_kg,
                        "kg_saldo": cascara_real_kg,
                        "estado": "en bodega",
                        "usuario": username,
                        "observaciones": obs_cascara,
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
        import plotly.graph_objects as go
        df_inv = db.get_df("produccion_semielaborados")
        if df_inv.empty:
            st.info("Todavía no hay lotes de semielaborado.")
        else:
            df_inv["kg_saldo"] = pd.to_numeric(df_inv["kg_saldo"], errors="coerce").fillna(0)
            disp = df_inv[df_inv["kg_saldo"] >= 0.1].copy()
            if disp.empty:
                st.info("No hay kg disponibles en los tanques actualmente.")
            else:
                # Tabla
                cols_t = [c for c in ["lote_semielaborado_id","fecha","tipo_producto","tanque_id","kg_saldo"] if c in disp.columns]
                t = disp[cols_t].copy()
                t["kg_saldo"] = t["kg_saldo"].round(1)
                if "tanque_id" in t.columns:
                    t["tanque_id"] = t["tanque_id"].fillna("").replace("", "Sin tanque (en granel)")
                st.dataframe(t, use_container_width=True, hide_index=True)

                st.write("")
                st.markdown("##### 🛢️ Nivel de los tanques")
                CAPACIDAD = 1000
                COLORES = {"Huevo entero": "#C68B54", "Clara": "#90EE90", "Yema": "#FFA500"}
                c1, c2 = st.columns(2)
                for col, tid, tnom in [(c1, "T1", "Tanque 1"), (c2, "T2", "Tanque 2")]:
                    with col:
                        if "tanque_id" in disp.columns:
                            lt = disp[disp["tanque_id"].astype(str) == tid].copy()
                        else:
                            lt = pd.DataFrame()
                        total = float(lt["kg_saldo"].sum()) if not lt.empty else 0.0
                        pct = min(total / CAPACIDAD * 100, 100)
                        fig = go.Figure()
                        if lt.empty:
                            fig.add_trace(go.Bar(x=[tnom], y=[CAPACIDAD],
                                marker_color="#e0e0e0", text=["Vacío"],
                                textposition="inside", name="Vacío"))
                        else:
                            for _, row in lt.iterrows():
                                tipo = str(row.get("tipo_producto", ""))
                                kg = float(row["kg_saldo"])
                                lid = str(row["lote_semielaborado_id"])
                                cc = COLORES.get(tipo)
                                if not cc:
                                    cc = "#C68B54" if lid.startswith("SR") else ("#FFA500" if lid.startswith("TK") else "#90EE90")
                                fig.add_trace(go.Bar(
                                    x=[tnom], y=[kg],
                                    marker_color=cc,
                                    text=[f"{lid}: {kg:.0f}kg"],
                                    textposition="inside", name=lid
                                ))
                            libre = max(CAPACIDAD - total, 0)
                            if libre > 0:
                                fig.add_trace(go.Bar(
                                    x=[tnom], y=[libre],
                                    marker_color="#f5f5f5",
                                    text=[f"{libre:.0f}kg libre"],
                                    textposition="inside",
                                    textfont={"color": "#aaa"},
                                    name="Libre", hoverinfo="skip"
                                ))
                        fig.update_layout(
                            barmode="stack", height=400,
                            title={"text": f"{tnom}: {total:.0f}/{CAPACIDAD}kg ({pct:.0f}%)", "x": 0.5},
                            showlegend=False,
                            margin={"l": 5, "r": 5, "t": 60, "b": 5},
                            paper_bgcolor="rgba(0,0,0,0)",
                            plot_bgcolor="rgba(0,0,0,0)",
                            yaxis={"range": [0, CAPACIDAD], "showgrid": True, "gridcolor": "#eee", "title": "kg"},
                            xaxis={"showticklabels": False},
                        )
                        st.plotly_chart(fig, use_container_width=True)

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
                        f"(saldo {disponibles_perdida.set_index('lote_semielaborado_id').loc[x, 'kg_saldo']:.2f} kg)"
                    ),
                )
                fila_lote = disponibles_perdida.set_index("lote_semielaborado_id").loc[lote_perdida_id]
                saldo_disponible = round(float(fila_lote["kg_saldo"]), 2)
                costo_unit_lote = float(pd.to_numeric(fila_lote.get("costo_unitario_kg", 0), errors="coerce") or 0)

                kg_desechar = st.number_input(
                    "Kg a desechar", min_value=0.0, max_value=saldo_disponible,
                    value=saldo_disponible, step=0.01,
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
                        nuevo_saldo = round(saldo_disponible - kg_desechar, 2)
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

            if es_admin_o_jefe_planta(rol):
                st.markdown("###### ↩️ Revertir una pérdida registrada por error")
                merma_sel = st.selectbox(
                    "Selecciona la pérdida a revertir",
                    df_mermas_vista["merma_id"],
                    format_func=lambda x: (
                        f"{x} — {df_mermas_vista.set_index('merma_id').loc[x, 'lote_semielaborado_id']} "
                        f"({float(pd.to_numeric(df_mermas_vista.set_index('merma_id').loc[x, 'kg_desechado'], errors='coerce') or 0):.2f} kg, "
                        f"{df_mermas_vista.set_index('merma_id').loc[x, 'causa']})"
                    ),
                    key="revertir_merma_sel",
                )
                fila_merma = df_mermas_vista.set_index("merma_id").loc[merma_sel]
                lote_merma = str(fila_merma["lote_semielaborado_id"])
                kg_merma = float(pd.to_numeric(fila_merma["kg_desechado"], errors="coerce") or 0)

                lote_actual_df = db.get_df("produccion_semielaborados")
                lote_actual = lote_actual_df[lote_actual_df["lote_semielaborado_id"] == lote_merma]
                if lote_actual.empty:
                    st.warning(
                        f"⚠️ El lote {lote_merma} ya no existe (fue eliminado por separado). "
                        "Se puede borrar el registro de pérdida, pero no hay saldo al cual devolver los kg."
                    )
                else:
                    saldo_lote_actual = round(float(lote_actual.iloc[0]["kg_saldo"]), 2)
                    st.caption(
                        f"Al revertir, se devolverán **{kg_merma:.2f} kg** al saldo del lote {lote_merma} "
                        f"(saldo actual {saldo_lote_actual:.2f} kg → nuevo saldo {round(saldo_lote_actual + kg_merma, 2):.2f} kg) "
                        "y se eliminará este registro de pérdida."
                    )

                confirmar_revertir = st.checkbox(
                    f"Confirmo que quiero revertir la pérdida {merma_sel}",
                    key="confirmar_revertir_merma",
                )
                if st.button("↩️ Revertir esta pérdida"):
                    if not confirmar_revertir:
                        st.error("Marca la casilla de confirmación antes de revertir.")
                    else:
                        if not lote_actual.empty:
                            db.update_row(
                                "produccion_semielaborados", "lote_semielaborado_id", lote_merma,
                                {"kg_saldo": round(saldo_lote_actual + kg_merma, 2)},
                            )
                        db.delete_row("mermas_semielaborado", "merma_id", merma_sel)
                        log_cambio(
                            db, username,
                            modulo="Produccion de semielaborados",
                            tabla="mermas_semielaborado",
                            id_registro=merma_sel, accion="eliminacion",
                            motivo=(
                                f"Perdida revertida — {kg_merma:.2f} kg devueltos al saldo del lote {lote_merma}"
                                if not lote_actual.empty
                                else "Perdida revertida — lote origen ya no existe, no se devolvio saldo"
                            ),
                        )
                        st.success(f"Pérdida {merma_sel} revertida.")
                        st.rerun()

    with tab_ajuste:
        st.caption(
            "La producción de semielaborados se estima por volumen/peso del tanque, y a veces "
            "esa estimación queda corta — por ejemplo, al envasar salen 108 kg cuando el tanque "
            "parecía tener solo 100 kg. Usa esto para sumar directamente los kg que faltan al "
            "saldo del lote, sin repetir todo el proceso de producción."
        )
        df_ajuste = db.get_df("produccion_semielaborados")
        if df_ajuste.empty:
            st.info("Todavía no hay lotes de semielaborado.")
        else:
            df_ajuste["kg_saldo"] = pd.to_numeric(df_ajuste["kg_saldo"], errors="coerce").fillna(0)
            df_ajuste["kg_real"] = pd.to_numeric(df_ajuste["kg_real"], errors="coerce").fillna(0)
            lote_ajuste_id = st.selectbox(
                "Lote semielaborado a corregir", df_ajuste["lote_semielaborado_id"],
                format_func=lambda x: (
                    f"{x} — {df_ajuste.set_index('lote_semielaborado_id').loc[x, 'tipo_producto']} "
                    f"(saldo actual {df_ajuste.set_index('lote_semielaborado_id').loc[x, 'kg_saldo']:.2f} kg)"
                ),
                key="ajuste_lote_sel",
            )
            fila_ajuste = df_ajuste.set_index("lote_semielaborado_id").loc[lote_ajuste_id]
            kg_real_actual = float(fila_ajuste["kg_real"])
            kg_saldo_actual = float(fila_ajuste["kg_saldo"])

            kg_agregar = st.number_input(
                "Kg a agregar al saldo", min_value=0.0, step=0.01, key="ajuste_kg_agregar",
            )
            motivo_ajuste = st.text_area(
                "Motivo del ajuste (obligatorio)", "",
                placeholder="Ej: al envasar salieron 8 kg más de lo que el tanque parecía tener.",
                key="ajuste_motivo",
            )

            if kg_agregar > 0:
                st.caption(
                    f"Saldo actual {kg_saldo_actual:.2f} kg → nuevo saldo "
                    f"{round(kg_saldo_actual + kg_agregar, 2):.2f} kg."
                )

            if st.button("➕ Registrar ajuste"):
                if kg_agregar <= 0:
                    st.error("Ingresa una cantidad mayor a 0.")
                elif not motivo_ajuste.strip():
                    st.error("Ingresa el motivo del ajuste.")
                else:
                    nuevo_kg_real = round(kg_real_actual + kg_agregar, 2)
                    nuevo_kg_saldo = round(kg_saldo_actual + kg_agregar, 2)
                    db.update_row(
                        "produccion_semielaborados", "lote_semielaborado_id", lote_ajuste_id,
                        {"kg_real": nuevo_kg_real, "kg_saldo": nuevo_kg_saldo},
                    )
                    ajuste_id = db.siguiente_id("ajustes_semielaborado", "AJ", datetime.date.today())
                    db.append_row("ajustes_semielaborado", {
                        "ajuste_id": ajuste_id,
                        "fecha": datetime.date.today().isoformat(),
                        "lote_semielaborado_id": lote_ajuste_id,
                        "kg_agregado": kg_agregar,
                        "motivo": motivo_ajuste.strip(),
                        "usuario": username,
                        "observaciones": "",
                    })
                    log_cambio(
                        db, username,
                        modulo="Produccion de semielaborados",
                        tabla="produccion_semielaborados",
                        id_registro=lote_ajuste_id, accion="ajuste",
                        motivo=f"Ajuste {ajuste_id}: +{kg_agregar:.2f} kg — {motivo_ajuste.strip()}",
                    )
                    st.success(
                        f"Ajuste {ajuste_id} registrado — saldo del lote {lote_ajuste_id} "
                        f"actualizado a {nuevo_kg_saldo:.2f} kg."
                    )
                    st.rerun()

        st.divider()
        st.markdown("##### 📋 Historial de ajustes registrados")
        df_ajustes_hist = db.get_df("ajustes_semielaborado")
        if df_ajustes_hist.empty:
            st.info("No hay ajustes registrados todavía.")
        else:
            df_ajustes_vista = df_ajustes_hist.copy()
            prod_tipos_aj = db.get_df("produccion_semielaborados")
            if not prod_tipos_aj.empty:
                df_ajustes_vista = df_ajustes_vista.merge(
                    prod_tipos_aj[["lote_semielaborado_id", "tipo_producto"]],
                    on="lote_semielaborado_id", how="left",
                )
            columnas_ajustes = [c for c in [
                "fecha", "lote_semielaborado_id", "tipo_producto",
                "kg_agregado", "motivo", "usuario",
            ] if c in df_ajustes_vista.columns]
            st.dataframe(
                df_ajustes_vista[columnas_ajustes].sort_values("fecha", ascending=False),
                use_container_width=True, hide_index=True,
            )
            kg_total_agregado = pd.to_numeric(df_ajustes_vista["kg_agregado"], errors="coerce").fillna(0).sum()
            st.caption(f"Total agregado histórico: **{kg_total_agregado:,.1f} kg** en {len(df_ajustes_vista)} registro(s)")

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

            envasado_df = db.get_df("pasteurizacion_envasado")
            envasado_rel = (
                envasado_df[envasado_df["lote_semielaborado_id"] == lote_sel]
                if not envasado_df.empty else envasado_df
            )
            consumo_semi_df = db.get_df("consumo_semi_pasteurizacion")
            consumo_semi_rel = (
                consumo_semi_df[consumo_semi_df["lote_semielaborado_id"] == lote_sel]
                if not consumo_semi_df.empty else consumo_semi_df
            )

            if not envasado_rel.empty or not consumo_semi_rel.empty:
                st.error(
                    "❌ Este lote ya tiene kg consumidos en Pasteurización y envasado, "
                    "así que no se puede eliminar sin dejar inconsistencias en el producto "
                    "ya envasado. Si de verdad necesitas corregirlo, dímelo para revisarlo "
                    "con cuidado en vez de borrarlo a ciegas."
                )
            else:
                consumo_rel = db.get_df("consumo_mp_produccion")
                consumo_rel = consumo_rel[consumo_rel["lote_semielaborado_id"] == lote_sel]
                insumos_rel = db.get_df("produccion_insumos")
                insumos_rel = insumos_rel[insumos_rel["lote_semielaborado_id"] == lote_sel]
                personal_rel = db.get_df("produccion_personal")
                personal_rel = personal_rel[personal_rel["lote_semielaborado_id"] == lote_sel]
                mermas_rel = db.get_df("mermas_semielaborado")
                mermas_rel = mermas_rel[mermas_rel["lote_semielaborado_id"] == lote_sel]
                granel_df = db.get_df("stock_a_granel")
                granel_rel = granel_df[granel_df["lote_origen"] == lote_sel] if not granel_df.empty else granel_df

                st.markdown("**Esto es lo que se va a revertir si lo eliminas:**")
                if not consumo_rel.empty:
                    st.write("↩️ Cubetas que vuelven a bodega de materia prima:")
                    st.dataframe(consumo_rel[["recepcion_id", "cubetas_usadas"]], use_container_width=True)
                if not insumos_rel.empty:
                    st.write("↩️ Insumos que vuelven a bodega:")
                    st.dataframe(insumos_rel[["insumo_id", "cantidad"]], use_container_width=True)
                if not personal_rel.empty:
                    st.write(f"↩️ Se eliminarán {len(personal_rel)} registro(s) de horas de personal.")
                if not mermas_rel.empty:
                    st.write("↩️ Registros de pérdida asociados a este lote que también se eliminarán:")
                    st.dataframe(mermas_rel[["merma_id", "kg_desechado", "causa"]], use_container_width=True)
                if not granel_rel.empty:
                    st.info(
                        f"ℹ️ Este lote tiene {len(granel_rel)} traslado(s) a stock a granel "
                        f"({pd.to_numeric(granel_rel['kg_inicial'], errors='coerce').sum():.1f} kg) que ya están "
                        "en recipientes de cuarto frío — no se eliminan, quedan como stock independiente."
                    )

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
                        # revertir cascara (subproducto) generada por este lote
                        db.delete_rows_where("produccion_cascara", "lote_semielaborado_origen", lote_sel)
                        db.delete_rows_where("mermas_semielaborado", "lote_semielaborado_id", lote_sel)
                        db.delete_row("produccion_semielaborados", "lote_semielaborado_id", lote_sel)

                        log_cambio(
                            db, username,
                            modulo="Produccion de semielaborados",
                            tabla="produccion_semielaborados",
                            id_registro=lote_sel, accion="eliminacion",
                            motivo="Eliminado con reversion completa (cubetas, insumos, personal, cascara, mermas)",
                        )

                        st.success(
                            f"Producción {lote_sel} eliminada y todo revertido — ya puedes "
                            f"volver a registrarla correctamente en 'Nueva producción'."
                        )
                        st.rerun()

