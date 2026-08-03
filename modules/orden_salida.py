"""
Orden de salida: para una fecha de entrega, muestra las lineas de pedido
comprometidas (marcadas como producidas), sugiere de que lote de cuarto
frio salen (FIFO), y genera un PDF por cliente para que la ingeniera de
calidad emita los certificados y el despachador cargue el camion.

No descuenta inventario -- ese descuento sigue haciendose desde
'Cuarto frio -> Despacho a cliente'.
"""
import io
import math
import datetime
import streamlit as st
import pandas as pd
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether,
)

ESTILOS = getSampleStyleSheet()


def _p(txt, negrita=False, pequeño=False):
    estilo = ESTILOS["Normal"].clone("celda")
    estilo.fontSize = 8 if pequeño else 9
    estilo.leading = 10 if pequeño else 11
    if negrita:
        estilo.fontName = "Helvetica-Bold"
    return Paragraph(str(txt) if txt is not None else "", estilo)


def _tipo_linea_a_semielaborado(tipo_linea):
    """Mapea 'Huevo entero pasteurizado' -> ('Huevo entero', True), etc."""
    tipo_str = str(tipo_linea or "").strip().lower()
    if "huevo" in tipo_str:
        base = "Huevo entero"
    elif "yema" in tipo_str:
        base = "Yema"
    elif "clara" in tipo_str:
        base = "Clara"
    else:
        base = tipo_linea
    pasteurizado = "sin pasteurizar" not in tipo_str
    return base, pasteurizado


def _sugerir_asignacion_fifo(linea, lotes_disponibles_df, mapa_kg_nominal):
    """Devuelve lista de asignaciones sugeridas para una linea (FIFO).
    Cada asignacion = {lote_producto_id, kg_asignado, gavetas_sugeridas}."""
    if lotes_disponibles_df.empty:
        return []
    kg_pedido = float(linea.get("cantidad_kg", 0) or 0)
    if kg_pedido <= 0:
        return []
    asignaciones = []
    for _, lote in lotes_disponibles_df.iterrows():
        if kg_pedido <= 0:
            break
        kg_disp = float(lote["kg_disponible"])
        kg_toma = min(kg_pedido, kg_disp)
        if kg_toma <= 0:
            continue
        asignaciones.append({
            "lote_producto_id": lote["lote_producto_id"],
            "kg_asignado": round(kg_toma, 2),
        })
        kg_pedido -= kg_toma
    return asignaciones


def _lotes_disponibles_para_linea(linea, cf_entradas, pasteurizacion, produccion_semi, mapa_kg_nominal):
    """Devuelve un DataFrame con los lote_producto_id disponibles en cuarto
    frio para esa linea (mismo tipo_producto + pasteurizado + presentacion_id),
    con saldo, kg_disponible y fecha_ingreso, ordenados FIFO."""
    if cf_entradas.empty or pasteurizacion.empty:
        return pd.DataFrame()
    base, pasteurizado_esperado = _tipo_linea_a_semielaborado(linea["tipo_producto"])
    pres_esperada = str(linea["presentacion_id"])
    # Traer lote_semielaborado y pasteurizado de pasteurizacion_envasado
    past_c = pasteurizacion.copy()
    past_c["pasteurizado_bool"] = past_c.get("pasteurizado", "").astype(str).str.upper().isin(["TRUE", "1", "SI", "SÍ"])
    # JOIN entradas cuarto frio con pasteurizacion
    cf = cf_entradas.copy()
    cf["saldo"] = pd.to_numeric(cf["saldo"], errors="coerce").fillna(0)
    cf = cf[cf["saldo"] > 0]
    if cf.empty:
        return pd.DataFrame()
    cf = cf.merge(
        past_c[["lote_producto_id", "lote_semielaborado_id", "pasteurizado_bool"]],
        on="lote_producto_id", how="left",
    )
    # Filtrar por presentacion
    cf = cf[cf["presentacion_id"].astype(str) == pres_esperada]
    # Filtrar por pasteurizado
    cf = cf[cf["pasteurizado_bool"] == pasteurizado_esperado]
    if cf.empty:
        return pd.DataFrame()
    # Filtrar por tipo de producto usando JOIN con produccion_semielaborados
    # (mas robusto que el prefijo del ID)
    if not produccion_semi.empty and "tipo_producto" in produccion_semi.columns:
        cf = cf.merge(
            produccion_semi[["lote_semielaborado_id", "tipo_producto"]],
            on="lote_semielaborado_id", how="left",
        )
        cf = cf[cf["tipo_producto"] == base]
    else:
        return pd.DataFrame()
    if cf.empty:
        return pd.DataFrame()
    # Calcular kg disponibles: saldo * kg_nominal
    kg_nom = mapa_kg_nominal.get(pres_esperada, 0)
    cf["kg_disponible"] = cf["saldo"] * kg_nom
    # Ordenar FIFO por fecha
    cf["fecha_dt"] = pd.to_datetime(cf["fecha"], errors="coerce")
    cf = cf.sort_values("fecha_dt", na_position="last")
    return cf[["lote_producto_id", "saldo", "kg_disponible", "fecha"]].reset_index(drop=True)


def render(db, username, rol):
    st.title("📤 Orden de salida")
    st.caption(
        "Consolidado de todo lo que se va a despachar en una fecha, agrupado por cliente. "
        "Sugiere de qué lote de cuarto frío sale cada producto (FIFO), y lo puedes "
        "editar. Sirve para que la ingeniera de calidad emita los certificados y el "
        "despachador cargue el camión."
    )

    hoy = datetime.date.today()
    fecha_sel = st.date_input("📅 Fecha de entrega", value=hoy)

    # -------- Cargar datos --------
    pedidos_cab = db.get_df("pedidos")
    lineas = db.get_df("pedidos_lineas")
    clientes = db.get_df("clientes")
    presentaciones = db.get_df("presentaciones")
    cf_entradas = db.get_df("cuarto_frio_entradas")
    pasteurizacion = db.get_df("pasteurizacion_envasado")
    produccion_semi = db.get_df("produccion_semielaborados")
    asignaciones_df = db.get_df("orden_salida_asignaciones")

    if lineas.empty:
        st.info("No hay líneas de pedido registradas todavía.")
        return

    lineas = lineas.copy()
    lineas["producido_bool"] = lineas["producido"].astype(str).str.upper().isin(["TRUE","1","SI","SÍ"])
    lineas["cantidad_kg"] = pd.to_numeric(lineas["cantidad_kg"], errors="coerce").fillna(0)
    lineas["unidades"] = pd.to_numeric(lineas["unidades"], errors="coerce").fillna(0)
    # Excluir lineas de pedidos cancelados
    if not pedidos_cab.empty and "estado" in pedidos_cab.columns:
        cancelados_ids = set(pedidos_cab[
            pedidos_cab["estado"].fillna("").astype(str).str.lower() == "cancelado"
        ]["pedido_id"].tolist())
        lineas = lineas[~lineas["pedido_id"].isin(cancelados_ids)]

    lineas_fecha = lineas[lineas["fecha_entrega"].astype(str) == fecha_sel.isoformat()]
    if lineas_fecha.empty:
        st.info(f"No hay líneas de pedido comprometidas para el {fecha_sel.strftime('%d/%m/%Y')}.")
        return

    # Alerta: líneas no producidas
    no_prod = lineas_fecha[~lineas_fecha["producido_bool"]]
    if not no_prod.empty:
        with st.container(border=True):
            st.warning(
                f"⚠️ Hay **{len(no_prod)}** línea(s) comprometida(s) para esta fecha "
                f"que aún no están marcadas como **producidas**. Márcalas en "
                f"**Recepción de pedidos → Todos los pedidos → Acciones por línea → Marcar producida** "
                f"antes de emitir la orden de salida."
            )
            mapa_cli = dict(zip(clientes["cliente_id"], clientes["nombre"])) if not clientes.empty else {}
            for _, l in no_prod.iterrows():
                cli = mapa_cli.get(l["pedido_id"], "")
                # Buscar cliente por cabecera
                if not pedidos_cab.empty:
                    fc = pedidos_cab[pedidos_cab["pedido_id"] == l["pedido_id"]]
                    if not fc.empty:
                        cli = mapa_cli.get(fc.iloc[0]["cliente_id"], fc.iloc[0]["cliente_id"])
                st.markdown(f"- `{l['pedido_id']}` — **{cli}** — {l['tipo_producto']} — {l['cantidad_kg']:.1f} kg")

    lineas_producidas = lineas_fecha[lineas_fecha["producido_bool"]]
    if lineas_producidas.empty:
        st.info("Ninguna línea comprometida para esta fecha está marcada como producida todavía.")
        return

    # -------- JOIN con cabecera y clientes --------
    cols_cab = [c for c in ["pedido_id", "cliente_id", "pedido_cliente_ref"] if c in pedidos_cab.columns]
    if cols_cab and not pedidos_cab.empty:
        lineas_producidas = lineas_producidas.merge(pedidos_cab[cols_cab], on="pedido_id", how="left")
    if not clientes.empty:
        lineas_producidas = lineas_producidas.merge(
            clientes[["cliente_id", "nombre"]].rename(columns={"nombre": "cliente_nombre"}),
            on="cliente_id", how="left",
        )
        lineas_producidas["cliente_nombre"] = lineas_producidas["cliente_nombre"].fillna(lineas_producidas["cliente_id"])
    else:
        lineas_producidas["cliente_nombre"] = lineas_producidas["cliente_id"]

    # Presentaciones: nombre + kg_nominal + unidades_por_gaveta
    mapa_pres_nombre = {}
    mapa_kg_nominal = {}
    mapa_upg = {}
    if not presentaciones.empty:
        for _, pr in presentaciones.iterrows():
            pid = str(pr["presentacion_id"])
            mapa_pres_nombre[pid] = str(pr.get("nombre", pid))
            mapa_kg_nominal[pid] = float(pd.to_numeric(pr.get("kg_nominal", 0), errors="coerce") or 0)
            upg = pr.get("unidades_por_gaveta", 0)
            try:
                mapa_upg[pid] = float(upg) if pd.notna(upg) and str(upg).strip() != "" else 0
            except (ValueError, TypeError):
                mapa_upg[pid] = 0

    # Alerta: presentaciones sin unidades_por_gaveta
    presentaciones_sin_upg = set()
    for _, l in lineas_producidas.iterrows():
        pid = str(l["presentacion_id"])
        if mapa_upg.get(pid, 0) == 0:
            presentaciones_sin_upg.add(pid)
    if presentaciones_sin_upg:
        st.warning(
            "⚠️ Estas presentaciones no tienen configurado el número de "
            "**unidades por gaveta**: "
            + ", ".join(mapa_pres_nombre.get(p, p) for p in presentaciones_sin_upg)
            + ". Configúralo en **Catálogos → Presentaciones** para que se sugieran automáticamente."
        )

    # -------- Cargar/inicializar asignaciones --------
    asig_fecha = asignaciones_df[
        asignaciones_df["fecha_entrega"].astype(str) == fecha_sel.isoformat()
    ].copy() if not asignaciones_df.empty else pd.DataFrame()

    # Si no hay asignaciones para esta fecha, sugerir FIFO para todas las lineas
    if asig_fecha.empty:
        st.info("🤖 Sugerencia FIFO inicial — puedes editarla antes de descargar el PDF.")
        for _, linea in lineas_producidas.iterrows():
            lotes_disp = _lotes_disponibles_para_linea(linea, cf_entradas, pasteurizacion, produccion_semi, mapa_kg_nominal)
            sugerencias = _sugerir_asignacion_fifo(linea, lotes_disp, mapa_kg_nominal)
            if not sugerencias:
                # Guardar un placeholder sin lote asignado
                sugerencias = [{"lote_producto_id": "", "kg_asignado": float(linea["cantidad_kg"])}]
            for s in sugerencias:
                asig_id = db.siguiente_id("orden_salida_asignaciones", "OSA", fecha_sel)
                upg = mapa_upg.get(str(linea["presentacion_id"]), 0)
                unidades_asig = (s["kg_asignado"] / mapa_kg_nominal.get(str(linea["presentacion_id"]), 1)) if mapa_kg_nominal.get(str(linea["presentacion_id"]), 0) > 0 else 0
                gavetas_sug = math.ceil(unidades_asig / upg) if upg > 0 else 0
                db.append_row("orden_salida_asignaciones", {
                    "asignacion_id": asig_id,
                    "fecha_entrega": fecha_sel.isoformat(),
                    "linea_id": linea["linea_id"],
                    "lote_producto_id": s["lote_producto_id"],
                    "kg_asignado": s["kg_asignado"],
                    "gavetas": gavetas_sug,
                    "usuario": username,
                    "observaciones": "",
                    "creado_en": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                })
        st.rerun()

    # -------- Vista editable por linea --------
    st.divider()
    st.markdown("### 📋 Asignaciones por línea")

    if st.button("🔄 Regenerar sugerencia FIFO (borra ediciones)", type="secondary"):
        for _, a in asig_fecha.iterrows():
            db.delete_row("orden_salida_asignaciones", "asignacion_id", a["asignacion_id"])
        st.rerun()

    lineas_producidas = lineas_producidas.sort_values(["cliente_nombre", "tipo_producto"])

    for _, linea in lineas_producidas.iterrows():
        pid = str(linea["presentacion_id"])
        with st.container(border=True):
            st.markdown(
                f"**{linea['cliente_nombre']}** — Pedido `{linea['pedido_id']}` — "
                f"{linea['tipo_producto']} — {linea['cantidad_kg']:.1f} kg — "
                f"{int(linea['unidades'])} un. {mapa_pres_nombre.get(pid, pid)}"
            )
            obs_linea = str(linea.get("observaciones", "") or "").strip()
            if obs_linea:
                st.caption(f"📝 {obs_linea}")

            # Filtrar asignaciones de esta linea
            asig_linea = asig_fecha[asig_fecha["linea_id"].astype(str) == str(linea["linea_id"])]
            lotes_disp = _lotes_disponibles_para_linea(linea, cf_entradas, pasteurizacion, produccion_semi, mapa_kg_nominal)

            # Mostrar lotes disponibles como ayuda (no bloquea)
            if not lotes_disp.empty:
                sugerencias_txt = ", ".join(
                    f"{r['lote_producto_id']} ({r['kg_disponible']:.0f} kg)"
                    for _, r in lotes_disp.head(5).iterrows()
                )
                st.caption(f"💡 Lotes disponibles en cuarto frío: {sugerencias_txt}")
            else:
                st.caption("💡 No hay lotes en cuarto frío todavía — puedes escribir un lote planificado.")

            # Headers de columnas
            hc1, hc2, hc3, hc4, hc5 = st.columns([3, 1, 1, 1, 1])
            hc1.markdown("**Lote (editable)**")
            hc2.markdown("**Kg**")
            hc3.markdown("**Gavetas**")
            hc4.markdown("**Obs.**")
            hc5.markdown("**Quitar**")

            kg_total_asignado = 0.0
            for _, a in asig_linea.iterrows():
                c1, c2, c3, c4, c5 = st.columns([3, 1, 1, 1, 1])
                lote_curr = str(a.get("lote_producto_id", "") or "")
                nuevo_lote = c1.text_input(
                    "Lote", value=lote_curr,
                    key=f"lote_{a['asignacion_id']}",
                    label_visibility="collapsed",
                    placeholder="Escribe el lote",
                )
                kg_curr = float(pd.to_numeric(a.get("kg_asignado", 0), errors="coerce") or 0)
                nuevo_kg = c2.number_input(
                    "Kg", min_value=0.0, step=0.5, value=kg_curr,
                    key=f"kg_{a['asignacion_id']}",
                    label_visibility="collapsed",
                )
                kg_total_asignado += nuevo_kg
                # Gavetas: sugerida (auto) o editable
                gav_curr = int(pd.to_numeric(a.get("gavetas", 0), errors="coerce") or 0)
                nuevo_gav = c3.number_input(
                    "Gavetas", min_value=0, step=1, value=gav_curr,
                    key=f"gav_{a['asignacion_id']}",
                    label_visibility="collapsed",
                )
                obs_a = c4.text_input(
                    "Obs.", value=str(a.get("observaciones", "") or ""),
                    key=f"obs_{a['asignacion_id']}",
                    label_visibility="collapsed",
                )
                borrar = c5.button("🗑️", key=f"del_{a['asignacion_id']}", help="Eliminar esta línea de asignación")
                if borrar:
                    db.delete_row("orden_salida_asignaciones", "asignacion_id", a["asignacion_id"])
                    st.rerun()
                # Auto-guardar si cambio algo
                if (nuevo_lote != lote_curr or nuevo_kg != kg_curr or nuevo_gav != gav_curr
                        or obs_a != str(a.get("observaciones", "") or "")):
                    db.update_row("orden_salida_asignaciones", "asignacion_id", a["asignacion_id"], {
                        "lote_producto_id": nuevo_lote,
                        "kg_asignado": nuevo_kg,
                        "gavetas": nuevo_gav,
                        "observaciones": obs_a,
                    })

            # Indicador de balance
            diff = kg_total_asignado - float(linea["cantidad_kg"])
            if abs(diff) < 0.01:
                st.caption(f"✅ Asignado: {kg_total_asignado:.1f} kg / pedido {linea['cantidad_kg']:.1f} kg")
            elif diff > 0:
                st.caption(f"⚠️ Sobrasignado: {kg_total_asignado:.1f} kg vs pedido {linea['cantidad_kg']:.1f} kg (+{diff:.1f})")
            else:
                st.caption(f"⚠️ Faltan {abs(diff):.1f} kg — asignado {kg_total_asignado:.1f} / pedido {linea['cantidad_kg']:.1f}")

            # Botón para agregar otra fila
            if st.button("➕ Agregar otro lote a esta línea", key=f"add_{linea['linea_id']}"):
                asig_id = db.siguiente_id("orden_salida_asignaciones", "OSA", fecha_sel)
                db.append_row("orden_salida_asignaciones", {
                    "asignacion_id": asig_id,
                    "fecha_entrega": fecha_sel.isoformat(),
                    "linea_id": linea["linea_id"],
                    "lote_producto_id": "",
                    "kg_asignado": 0,
                    "gavetas": 0,
                    "usuario": username,
                    "observaciones": "",
                    "creado_en": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                })
                st.rerun()

    # -------- LÍNEAS MANUALES (cliente extra) --------
    st.divider()
    with st.expander("➕ Agregar línea manual (cliente extra fuera del sistema)", expanded=False):
        st.caption(
            "Para clientes cuyo pedido no está registrado en el sistema pero "
            "necesitas incluirlos en esta orden de salida. Solo aparecen en el PDF."
        )
        if clientes.empty or presentaciones.empty:
            st.warning("Necesitas tener clientes y presentaciones en catálogos.")
        else:
            colm1, colm2 = st.columns(2)
            cli_extra = colm1.selectbox(
                "Cliente", clientes["cliente_id"],
                format_func=lambda x: clientes.set_index("cliente_id").loc[x, "nombre"],
                key="man_cli",
            )
            tipo_extra_m = colm2.selectbox(
                "Producto",
                ["Huevo entero pasteurizado", "Clara pasteurizada", "Clara sin pasteurizar", "Yema pasteurizada"],
                key="man_tipo",
            )
            colm3, colm4 = st.columns(2)
            pres_extra_m = colm3.selectbox(
                "Presentación", presentaciones["presentacion_id"],
                format_func=lambda x: presentaciones.set_index("presentacion_id").loc[x, "nombre"],
                key="man_pres",
            )
            unid_extra_m = colm4.number_input("Unidades", min_value=0, step=1, key="man_unid")
            kg_nom_extra = float(presentaciones.set_index("presentacion_id").loc[pres_extra_m, "kg_nominal"])
            kg_extra_m = round(unid_extra_m * kg_nom_extra, 2)
            colm5, colm6 = st.columns(2)
            lote_extra = colm5.text_input("Lote", "", key="man_lote", placeholder="Escribe el lote")
            upg_extra = mapa_upg.get(str(pres_extra_m), 0)
            gav_default = math.ceil(unid_extra_m / upg_extra) if upg_extra > 0 else 0
            gav_extra = colm6.number_input("Gavetas", min_value=0, step=1, value=int(gav_default), key="man_gav")
            obs_extra_m = st.text_input("Observaciones", "", key="man_obs")
            colm7, _ = st.columns(2)
            colm7.metric("Kg calculados", f"{kg_extra_m:.1f} kg")

            if st.button("➕ Agregar línea manual", key="btn_man_add"):
                if unid_extra_m <= 0:
                    st.error("Ingresa las unidades.")
                else:
                    asig_id = db.siguiente_id("orden_salida_asignaciones", "OSA", fecha_sel)
                    # linea_id="MANUAL:<cliente>|<tipo>|<pres>" — asi lo distinguimos y traemos sus datos al PDF
                    linea_id_manual = f"MANUAL:{cli_extra}|{tipo_extra_m}|{pres_extra_m}|{unid_extra_m}"
                    db.append_row("orden_salida_asignaciones", {
                        "asignacion_id": asig_id,
                        "fecha_entrega": fecha_sel.isoformat(),
                        "linea_id": linea_id_manual,
                        "lote_producto_id": lote_extra,
                        "kg_asignado": kg_extra_m,
                        "gavetas": gav_extra,
                        "usuario": username,
                        "observaciones": obs_extra_m,
                        "creado_en": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    })
                    st.success(f"✅ Línea manual agregada al cliente {clientes.set_index('cliente_id').loc[cli_extra, 'nombre']}.")
                    st.rerun()

    # Mostrar las líneas manuales existentes para poder borrarlas
    asig_manual = asig_fecha[asig_fecha["linea_id"].astype(str).str.startswith("MANUAL:")]
    if not asig_manual.empty:
        st.markdown("##### Líneas manuales agregadas")
        mapa_cli_m = dict(zip(clientes["cliente_id"], clientes["nombre"])) if not clientes.empty else {}
        for _, a in asig_manual.iterrows():
            partes = str(a["linea_id"]).replace("MANUAL:", "").split("|")
            cli_id_m = partes[0] if len(partes) > 0 else "?"
            tipo_m = partes[1] if len(partes) > 1 else "?"
            pres_id_m = partes[2] if len(partes) > 2 else "?"
            unid_m = partes[3] if len(partes) > 3 else "0"
            cm1, cm2 = st.columns([6, 1])
            cm1.markdown(
                f"• **{mapa_cli_m.get(cli_id_m, cli_id_m)}** — {tipo_m} — "
                f"{mapa_pres_nombre.get(pres_id_m, pres_id_m)} — "
                f"{unid_m} un., {float(a.get('kg_asignado', 0)):.1f} kg — "
                f"lote `{a.get('lote_producto_id', '') or '(sin lote)'}`"
            )
            if cm2.button("🗑️", key=f"del_man_{a['asignacion_id']}"):
                db.delete_row("orden_salida_asignaciones", "asignacion_id", a["asignacion_id"])
                st.rerun()

    # -------- TOTALES POR PRODUCTO --------
    st.divider()
    st.markdown("### 📊 Totales por producto")
    totales_prod = {}
    totales_pres = {}  # pres_id -> {"kg": float, "gavetas": int}  (para tabla por presentación)
    # Sumar de asignaciones de pedidos + manuales
    for _, a in asig_fecha.iterrows():
        # Las filas marcador de "gavetas reales" no son asignaciones de producto real
        if str(a["linea_id"]).startswith("TOTALGAV:"):
            continue
        kg_a = float(pd.to_numeric(a.get("kg_asignado", 0), errors="coerce") or 0)
        gav = int(pd.to_numeric(a.get("gavetas", 0), errors="coerce") or 0)
        # Determinar tipo de producto y presentacion
        if str(a["linea_id"]).startswith("MANUAL:"):
            partes = str(a["linea_id"]).replace("MANUAL:", "").split("|")
            tipo = partes[1] if len(partes) > 1 else "Sin tipo"
            pres_id = partes[2] if len(partes) > 2 else ""
        else:
            fila_lin = lineas_producidas[lineas_producidas["linea_id"].astype(str) == str(a["linea_id"])]
            if fila_lin.empty:
                continue
            tipo = fila_lin.iloc[0]["tipo_producto"]
            pres_id = str(fila_lin.iloc[0]["presentacion_id"])
        if tipo not in totales_prod:
            totales_prod[tipo] = {"kg": 0.0, "gavetas": 0}
        totales_prod[tipo]["kg"] += kg_a
        totales_prod[tipo]["gavetas"] += gav

        if pres_id not in totales_pres:
            totales_pres[pres_id] = {"kg": 0.0, "gavetas": 0}
        totales_pres[pres_id]["kg"] += kg_a
        totales_pres[pres_id]["gavetas"] += gav

    if totales_prod:
        df_tot = pd.DataFrame([
            {"Producto": t, "Kg totales": f"{v['kg']:.1f}", "Gavetas totales": v["gavetas"]}
            for t, v in sorted(totales_prod.items())
        ])
        total_general_kg = sum(v["kg"] for v in totales_prod.values())
        total_general_gav = sum(v["gavetas"] for v in totales_prod.values())
        df_tot.loc[len(df_tot)] = ["TOTAL GENERAL", f"{total_general_kg:.1f}", total_general_gav]
        st.dataframe(df_tot, use_container_width=True, hide_index=True)

    # -------- TOTALES POR PRESENTACIÓN (con gavetas reales editable) --------
    st.divider()
    st.markdown("### 📦 Totales por presentación")
    mapa_gavreal_final = {}  # pres_id -> gavetas reales (para reutilizar al generar el PDF)
    if totales_pres:
        st.caption(
            "La columna **Gavetas reales** es editable: úsala para anotar cuántas "
            "gavetas se armaron finalmente (puede diferir de la sugerencia). Se guarda "
            "automáticamente por fecha."
        )
        # Cargar valores de "gavetas reales" ya guardados para esta fecha
        asig_gavreal = asig_fecha[asig_fecha["linea_id"].astype(str).str.startswith("TOTALGAV:")]
        mapa_gavreal_guardado = {}
        for _, g in asig_gavreal.iterrows():
            clave = str(g["linea_id"]).replace("TOTALGAV:", "", 1)
            mapa_gavreal_guardado[clave] = {
                "asignacion_id": g["asignacion_id"],
                "gavetas": int(pd.to_numeric(g.get("gavetas", 0), errors="coerce") or 0),
            }

        hp1, hp2, hp3, hp4 = st.columns([2, 1, 1, 1])
        hp1.markdown("**Presentación**")
        hp2.markdown("**Kg totales**")
        hp3.markdown("**Gavetas sugeridas**")
        hp4.markdown("**Gavetas reales**")

        tot_kg_pres = 0.0
        tot_gav_sug_pres = 0
        tot_gav_real_pres = 0
        pres_ordenadas = sorted(totales_pres.items(), key=lambda kv: mapa_pres_nombre.get(kv[0], kv[0]))
        for pres_id, v in pres_ordenadas:
            pres_nombre = mapa_pres_nombre.get(pres_id, pres_id or "Sin presentación")
            cp1, cp2, cp3, cp4 = st.columns([2, 1, 1, 1])
            cp1.markdown(pres_nombre)
            cp2.markdown(f"{v['kg']:.1f}")
            cp3.markdown(str(v["gavetas"]))

            guardado = mapa_gavreal_guardado.get(pres_id)
            valor_default = guardado["gavetas"] if guardado else v["gavetas"]
            nuevo_gavreal = cp4.number_input(
                "Gavetas reales", min_value=0, step=1, value=int(valor_default),
                key=f"gavreal_{fecha_sel.isoformat()}_{pres_id}",
                label_visibility="collapsed",
            )
            mapa_gavreal_final[pres_id] = nuevo_gavreal

            if guardado:
                if nuevo_gavreal != guardado["gavetas"]:
                    db.update_row("orden_salida_asignaciones", "asignacion_id", guardado["asignacion_id"], {
                        "gavetas": nuevo_gavreal,
                    })
            elif nuevo_gavreal != v["gavetas"]:
                asig_id_g = db.siguiente_id("orden_salida_asignaciones", "OSA", fecha_sel)
                db.append_row("orden_salida_asignaciones", {
                    "asignacion_id": asig_id_g,
                    "fecha_entrega": fecha_sel.isoformat(),
                    "linea_id": f"TOTALGAV:{pres_id}",
                    "lote_producto_id": "",
                    "kg_asignado": 0,
                    "gavetas": nuevo_gavreal,
                    "usuario": username,
                    "observaciones": "",
                    "creado_en": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                })

            tot_kg_pres += v["kg"]
            tot_gav_sug_pres += v["gavetas"]
            tot_gav_real_pres += nuevo_gavreal

        st.markdown("---")
        cf1, cf2, cf3, cf4 = st.columns([2, 1, 1, 1])
        cf1.markdown("**TOTAL GENERAL**")
        cf2.markdown(f"**{tot_kg_pres:.1f}**")
        cf3.markdown(f"**{tot_gav_sug_pres}**")
        cf4.markdown(f"**{tot_gav_real_pres}**")

    # -------- PDF --------
    st.divider()
    st.markdown("### 📄 Descargar orden de salida (PDF)")
    if st.button("📄 Generar PDF", type="primary", use_container_width=True):
        # Recargar asignaciones actualizadas
        asignaciones_df = db.get_df("orden_salida_asignaciones")
        asig_fecha = asignaciones_df[
            asignaciones_df["fecha_entrega"].astype(str) == fecha_sel.isoformat()
        ].copy()
        mapa_cli_pdf = dict(zip(clientes["cliente_id"], clientes["nombre"])) if not clientes.empty else {}
        # Armar datos por cliente
        datos_pdf = {}
        for _, linea in lineas_producidas.iterrows():
            cli = linea["cliente_nombre"]
            asig_l = asig_fecha[asig_fecha["linea_id"].astype(str) == str(linea["linea_id"])]
            pres_nombre = mapa_pres_nombre.get(str(linea["presentacion_id"]), str(linea["presentacion_id"]))
            for _, a in asig_l.iterrows():
                kg_a = float(pd.to_numeric(a.get("kg_asignado", 0), errors="coerce") or 0)
                gav = int(pd.to_numeric(a.get("gavetas", 0), errors="coerce") or 0)
                kg_nom = mapa_kg_nominal.get(str(linea["presentacion_id"]), 0)
                unid_lote = int(round(kg_a / kg_nom)) if kg_nom > 0 else 0
                datos_pdf.setdefault(cli, []).append({
                    "producto": linea["tipo_producto"],
                    "lote": str(a.get("lote_producto_id", "") or "—") or "—",
                    "kg_total": float(linea["cantidad_kg"]),
                    "kg_lote": kg_a,
                    "presentacion": pres_nombre,
                    "unidades": unid_lote,
                    "gavetas": gav,
                    "obs_linea": str(linea.get("observaciones", "") or ""),
                    "obs_asig": str(a.get("observaciones", "") or ""),
                })
        # Agregar lineas manuales al PDF
        for _, a in asig_fecha.iterrows():
            if not str(a["linea_id"]).startswith("MANUAL:"):
                continue
            partes = str(a["linea_id"]).replace("MANUAL:", "").split("|")
            cli_id_m = partes[0] if len(partes) > 0 else ""
            tipo_m = partes[1] if len(partes) > 1 else ""
            pres_id_m = partes[2] if len(partes) > 2 else ""
            unid_m = int(partes[3]) if len(partes) > 3 and partes[3].isdigit() else 0
            cli_nombre_m = mapa_cli_pdf.get(cli_id_m, cli_id_m)
            kg_a = float(pd.to_numeric(a.get("kg_asignado", 0), errors="coerce") or 0)
            gav = int(pd.to_numeric(a.get("gavetas", 0), errors="coerce") or 0)
            datos_pdf.setdefault(cli_nombre_m, []).append({
                "producto": tipo_m + " (extra)",
                "lote": str(a.get("lote_producto_id", "") or "—") or "—",
                "kg_total": kg_a,
                "kg_lote": kg_a,
                "presentacion": mapa_pres_nombre.get(pres_id_m, pres_id_m),
                "unidades": unid_m,
                "gavetas": gav,
                "obs_linea": "",
                "obs_asig": str(a.get("observaciones", "") or ""),
            })
        totales_pres_pdf = {}
        for pres_id, v in totales_pres.items():
            nombre_p = mapa_pres_nombre.get(pres_id, pres_id or "Sin presentación")
            totales_pres_pdf[nombre_p] = {
                "kg": v["kg"],
                "gavetas_sugeridas": v["gavetas"],
                "gavetas_reales": mapa_gavreal_final.get(pres_id, v["gavetas"]),
            }
        pdf_bytes = _generar_pdf(fecha_sel, datos_pdf, totales_prod, totales_pres_pdf)
        st.download_button(
            "⬇️ Descargar PDF",
            data=pdf_bytes,
            file_name=f"orden_salida_{fecha_sel.isoformat()}.pdf",
            mime="application/pdf",
            use_container_width=True,
        )


def _generar_pdf(fecha, datos_por_cliente, totales_prod=None, totales_pres=None):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter,
                            topMargin=1.5*cm, bottomMargin=1.5*cm,
                            leftMargin=1.5*cm, rightMargin=1.5*cm)
    el = []
    el.append(Paragraph(f"Orden de salida — {fecha.strftime('%d/%m/%Y')}", ESTILOS["Title"]))
    el.append(Paragraph(
        f"Generado: {datetime.datetime.now().strftime('%d/%m/%Y %H:%M')}",
        ESTILOS["Normal"],
    ))
    el.append(Spacer(1, 0.4*cm))

    if not datos_por_cliente:
        el.append(Paragraph("No hay líneas comprometidas para esta fecha.", ESTILOS["Normal"]))
        doc.build(el)
        buffer.seek(0)
        return buffer.getvalue()

    for cliente, filas in sorted(datos_por_cliente.items()):
        bloque = []
        bloque.append(Paragraph(f"CLIENTE: {cliente}", ESTILOS["Heading2"]))
        # Tabla: Producto | Lote | Kg total | Kg/lote | Presentación | Unid. | Gavetas | Obs.
        encab = ["Producto", "Lote", "Kg total", "Kg lote", "Presentación", "Unid.", "Gavetas", "Observaciones"]
        datos = [[_p(h, negrita=True, pequeño=True) for h in encab]]
        total_kg = 0.0
        total_unid = 0
        total_gav = 0
        for f in filas:
            obs = f["obs_asig"] or f["obs_linea"]
            datos.append([
                _p(f["producto"], pequeño=True),
                _p(f["lote"], pequeño=True),
                _p(f"{f['kg_total']:.1f}", pequeño=True),
                _p(f"{f['kg_lote']:.1f}", pequeño=True),
                _p(f["presentacion"], pequeño=True),
                _p(str(f["unidades"]), pequeño=True),
                _p(str(f["gavetas"]), pequeño=True),
                _p(obs, pequeño=True),
            ])
            total_kg += f["kg_lote"]
            total_unid += f["unidades"]
            total_gav += f["gavetas"]
        # Fila TOTAL
        datos.append([
            _p("TOTAL", negrita=True, pequeño=True), _p(""), _p(""),
            _p(f"{total_kg:.1f}", negrita=True, pequeño=True),
            _p(""), _p(str(total_unid), negrita=True, pequeño=True),
            _p(str(total_gav), negrita=True, pequeño=True), _p(""),
        ])
        t = Table(datos, repeatRows=1)
        t.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1565c0")),
            ("TEXTCOLOR", (0,0), (-1,0), colors.white),
            ("GRID", (0,0), (-1,-1), 0.3, colors.HexColor("#dddddd")),
            ("ROWBACKGROUNDS", (0,1), (-1,-2), [colors.white, colors.HexColor("#f5f5f5")]),
            ("BACKGROUND", (0,-1), (-1,-1), colors.HexColor("#fff3e0")),
            ("BOTTOMPADDING", (0,0), (-1,-1), 4),
            ("TOPPADDING", (0,0), (-1,-1), 4),
        ]))
        bloque.append(t)
        el.append(KeepTogether(bloque))
        el.append(Spacer(1, 0.6*cm))

    # Tabla resumen: totales por producto (para elegir tamaño de camión)
    if totales_prod:
        el.append(Spacer(1, 0.5*cm))
        bloque_tot = []
        bloque_tot.append(Paragraph("Totales por producto (para logística)", ESTILOS["Heading2"]))
        encab_tot = ["Producto", "Kg totales", "Gavetas totales"]
        datos_tot = [[_p(h, negrita=True) for h in encab_tot]]
        for tipo, v in sorted(totales_prod.items()):
            datos_tot.append([
                _p(tipo),
                _p(f"{v['kg']:.1f}"),
                _p(str(v['gavetas'])),
            ])
        # Fila TOTAL GENERAL
        tot_kg = sum(v["kg"] for v in totales_prod.values())
        tot_gav = sum(v["gavetas"] for v in totales_prod.values())
        datos_tot.append([
            _p("TOTAL GENERAL", negrita=True),
            _p(f"{tot_kg:.1f}", negrita=True),
            _p(str(tot_gav), negrita=True),
        ])
        tt = Table(datos_tot, repeatRows=1)
        tt.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#2e7d32")),
            ("TEXTCOLOR", (0,0), (-1,0), colors.white),
            ("GRID", (0,0), (-1,-1), 0.3, colors.HexColor("#dddddd")),
            ("ROWBACKGROUNDS", (0,1), (-1,-2), [colors.white, colors.HexColor("#f5f5f5")]),
            ("BACKGROUND", (0,-1), (-1,-1), colors.HexColor("#fff3e0")),
            ("BOTTOMPADDING", (0,0), (-1,-1), 5),
            ("TOPPADDING", (0,0), (-1,-1), 5),
        ]))
        bloque_tot.append(tt)
        el.append(KeepTogether(bloque_tot))

    # Tabla resumen: totales por presentación (con gavetas reales)
    if totales_pres:
        el.append(Spacer(1, 0.5*cm))
        bloque_pres = []
        bloque_pres.append(Paragraph("Totales por presentación", ESTILOS["Heading2"]))
        encab_pres = ["Presentación", "Kg totales", "Gavetas sugeridas", "Gavetas reales"]
        datos_pres = [[_p(h, negrita=True) for h in encab_pres]]
        for nombre_p, v in sorted(totales_pres.items()):
            datos_pres.append([
                _p(nombre_p),
                _p(f"{v['kg']:.1f}"),
                _p(str(v["gavetas_sugeridas"])),
                _p(str(v["gavetas_reales"])),
            ])
        tot_kg_p = sum(v["kg"] for v in totales_pres.values())
        tot_gav_sug_p = sum(v["gavetas_sugeridas"] for v in totales_pres.values())
        tot_gav_real_p = sum(v["gavetas_reales"] for v in totales_pres.values())
        datos_pres.append([
            _p("TOTAL GENERAL", negrita=True),
            _p(f"{tot_kg_p:.1f}", negrita=True),
            _p(str(tot_gav_sug_p), negrita=True),
            _p(str(tot_gav_real_p), negrita=True),
        ])
        tp = Table(datos_pres, repeatRows=1)
        tp.setStyle(TableStyle([
            ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#6a1b9a")),
            ("TEXTCOLOR", (0,0), (-1,0), colors.white),
            ("GRID", (0,0), (-1,-1), 0.3, colors.HexColor("#dddddd")),
            ("ROWBACKGROUNDS", (0,1), (-1,-2), [colors.white, colors.HexColor("#f5f5f5")]),
            ("BACKGROUND", (0,-1), (-1,-1), colors.HexColor("#fff3e0")),
            ("BOTTOMPADDING", (0,0), (-1,-1), 5),
            ("TOPPADDING", (0,0), (-1,-1), 5),
        ]))
        bloque_pres.append(tp)
        el.append(KeepTogether(bloque_pres))

    doc.build(el)
    buffer.seek(0)
    return buffer.getvalue()
