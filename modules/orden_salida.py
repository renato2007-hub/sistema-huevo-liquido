"""
Orden de salida: para una fecha de entrega puede haber mas de una orden de
salida (distintos camiones/rutas/clientes). Cada orden agrupa un subconjunto
de las lineas de pedido comprometidas y producidas para esa fecha -- las
lineas no marcadas como incluidas en una orden no aparecen en ella. Para
cada linea incluida, sugiere de que lote de cuarto frio sale (FIFO), editable,
y genera un PDF por cliente para que la ingeniera de calidad emita los
certificados y el despachador cargue el camion.

No descuenta inventario -- ese descuento sigue haciendose desde
'Cuarto frio -> Despacho a cliente'.
"""
import io
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
    Cada asignacion = {lote_producto_id, kg_asignado}."""
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


def _crear_fifo_para_linea(db, username, fecha_sel, orden_sel, linea,
                            cf_entradas, pasteurizacion, produccion_semi, mapa_kg_nominal):
    """Crea las filas de asignacion (sugerencia FIFO, o placeholder sin lote
    si no hay stock) para una linea dentro de una orden puntual."""
    lotes_disp = _lotes_disponibles_para_linea(linea, cf_entradas, pasteurizacion, produccion_semi, mapa_kg_nominal)
    sugerencias = _sugerir_asignacion_fifo(linea, lotes_disp, mapa_kg_nominal)
    if not sugerencias:
        sugerencias = [{"lote_producto_id": "", "kg_asignado": float(linea["cantidad_kg"])}]
    for s in sugerencias:
        asig_id = db.siguiente_id("orden_salida_asignaciones", "OSA", fecha_sel)
        db.append_row("orden_salida_asignaciones", {
            "asignacion_id": asig_id,
            "fecha_entrega": fecha_sel.isoformat(),
            "linea_id": linea["linea_id"],
            "lote_producto_id": s["lote_producto_id"],
            "kg_asignado": s["kg_asignado"],
            "gavetas": 0,
            "usuario": username,
            "observaciones": "",
            "creado_en": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "orden_id": orden_sel,
        })


def _incluir_linea_en_orden(db, username, fecha_sel, orden_sel, linea, asig_fecha_todas,
                             cf_entradas, pasteurizacion, produccion_semi, mapa_kg_nominal):
    """Marca una linea como incluida en la orden actual. Si ya existian
    asignaciones 'huerfanas' para esa linea (creadas antes de que existiera
    el concepto de orden, sin orden_id), las adopta en vez de duplicarlas."""
    lid = str(linea["linea_id"])
    if "orden_id" in asig_fecha_todas.columns:
        huerfanas = asig_fecha_todas[
            (asig_fecha_todas["linea_id"].astype(str) == lid)
            & (asig_fecha_todas["orden_id"].astype(str).str.strip() == "")
        ]
    else:
        huerfanas = asig_fecha_todas[asig_fecha_todas["linea_id"].astype(str) == lid]
    if not huerfanas.empty:
        for _, h in huerfanas.iterrows():
            db.update_row("orden_salida_asignaciones", "asignacion_id", h["asignacion_id"], {
                "orden_id": orden_sel,
            })
        return
    _crear_fifo_para_linea(db, username, fecha_sel, orden_sel, linea,
                            cf_entradas, pasteurizacion, produccion_semi, mapa_kg_nominal)


def _excluir_linea_de_orden(db, linea_id, asig_fecha_orden):
    """Quita una linea de la orden actual (borra sus filas de asignacion)."""
    filas = asig_fecha_orden[asig_fecha_orden["linea_id"].astype(str) == str(linea_id)]
    for _, f in filas.iterrows():
        db.delete_row("orden_salida_asignaciones", "asignacion_id", f["asignacion_id"])


def render(db, username, rol):
    st.title("📤 Orden de salida")
    st.caption(
        "Consolidado de todo lo que se va a despachar en una fecha, agrupado por cliente. "
        "En un mismo día puede haber más de una orden de salida (distintos camiones o "
        "rutas): elige o crea una orden y marca qué pedidos van en ella. Sugiere de qué "
        "lote de cuarto frío sale cada producto (FIFO), y lo puedes editar. Sirve para que "
        "la ingeniera de calidad emita los certificados y el despachador cargue el camión."
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
    ordenes_df = db.get_df("ordenes_salida")
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
    lineas_producidas = lineas_producidas.sort_values(["cliente_nombre", "tipo_producto"])

    # Presentaciones: nombre + kg_nominal
    mapa_pres_nombre = {}
    mapa_kg_nominal = {}
    if not presentaciones.empty:
        for _, pr in presentaciones.iterrows():
            pid = str(pr["presentacion_id"])
            mapa_pres_nombre[pid] = str(pr.get("nombre", pid))
            mapa_kg_nominal[pid] = float(pd.to_numeric(pr.get("kg_nominal", 0), errors="coerce") or 0)

    # ==================== SELECCIÓN / CREACIÓN DE ORDEN ====================
    st.divider()
    st.markdown("### 🗂️ Orden de salida de esta fecha")
    st.caption(
        "Puede haber más de una orden de salida el mismo día (ej. distintos "
        "camiones o rutas). Elige una orden existente o crea una nueva; luego, "
        "más abajo, marca qué pedidos van en ella."
    )

    ordenes_fecha = (
        ordenes_df[ordenes_df["fecha_entrega"].astype(str) == fecha_sel.isoformat()].copy()
        if not ordenes_df.empty else pd.DataFrame()
    )
    if not ordenes_fecha.empty:
        ordenes_fecha = ordenes_fecha.sort_values("creado_en")
    mapa_orden_nombre = dict(zip(ordenes_fecha["orden_id"], ordenes_fecha["nombre"])) if not ordenes_fecha.empty else {}

    session_key = f"orden_sel_{fecha_sel.isoformat()}"
    CREAR_NUEVA = "➕ Crear nueva orden…"
    orden_sel = None
    nombre_orden_actual = None

    if ordenes_fecha.empty:
        st.info("Todavía no hay ninguna orden de salida creada para esta fecha.")
        mostrar_form_nueva = True
    else:
        ids_disponibles = list(mapa_orden_nombre.keys())
        if st.session_state.get(session_key) not in ids_disponibles:
            st.session_state[session_key] = ids_disponibles[0]
        etiquetas = [f"{mapa_orden_nombre[i]}" for i in ids_disponibles] + [CREAR_NUEVA]
        idx_default = ids_disponibles.index(st.session_state[session_key])
        elegido = st.selectbox(
            "Selecciona la orden de salida", etiquetas, index=idx_default,
            key=f"sb_orden_{fecha_sel.isoformat()}",
        )
        if elegido == CREAR_NUEVA:
            mostrar_form_nueva = True
        else:
            mostrar_form_nueva = False
            orden_sel = ids_disponibles[etiquetas.index(elegido)]
            nombre_orden_actual = mapa_orden_nombre[orden_sel]
            st.session_state[session_key] = orden_sel

    if mostrar_form_nueva:
        nombre_nueva = st.text_input(
            "Nombre de la nueva orden",
            placeholder="Ej. Camión mañana, Ruta Norte, Cliente urgente...",
            key=f"nombre_nueva_orden_{fecha_sel.isoformat()}",
        )
        if st.button("➕ Crear orden", type="primary", key=f"btn_crear_orden_{fecha_sel.isoformat()}"):
            if not nombre_nueva.strip():
                st.error("Ponle un nombre a la orden.")
                st.stop()
            nuevo_orden_id = db.siguiente_id("ordenes_salida", "OS", fecha_sel)
            era_primera = ordenes_fecha.empty
            db.append_row("ordenes_salida", {
                "orden_id": nuevo_orden_id,
                "fecha_entrega": fecha_sel.isoformat(),
                "nombre": nombre_nueva.strip(),
                "usuario": username,
                "creado_en": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            })
            if era_primera:
                # Adoptar asignaciones creadas antes de que existiera el concepto
                # de orden (sin orden_id) como parte de esta primera orden del dia,
                # para no perder ediciones previas.
                asig_todas_fecha = asignaciones_df[
                    asignaciones_df["fecha_entrega"].astype(str) == fecha_sel.isoformat()
                ] if not asignaciones_df.empty else pd.DataFrame()
                if "orden_id" in asig_todas_fecha.columns:
                    huerfanas = asig_todas_fecha[asig_todas_fecha["orden_id"].astype(str).str.strip() == ""]
                else:
                    huerfanas = asig_todas_fecha
                for _, h in huerfanas.iterrows():
                    db.update_row("orden_salida_asignaciones", "asignacion_id", h["asignacion_id"], {
                        "orden_id": nuevo_orden_id,
                    })
            st.session_state[session_key] = nuevo_orden_id
            st.rerun()
        st.stop()

    # ==================== ASIGNACIONES DE LA ORDEN ACTUAL ====================
    asig_fecha_todas = asignaciones_df[
        asignaciones_df["fecha_entrega"].astype(str) == fecha_sel.isoformat()
    ].copy() if not asignaciones_df.empty else pd.DataFrame()

    if "orden_id" in asig_fecha_todas.columns:
        asig_fecha = asig_fecha_todas[asig_fecha_todas["orden_id"].astype(str) == str(orden_sel)].copy()
    else:
        asig_fecha = pd.DataFrame(columns=asig_fecha_todas.columns)

    # A que orden pertenece cada linea real (no MANUAL/TOTALGAV), para saber
    # cuales ya estan tomadas por OTRA orden de esta misma fecha.
    mapa_linea_a_orden = {}
    if not asig_fecha_todas.empty and "orden_id" in asig_fecha_todas.columns:
        reales_todas = asig_fecha_todas[
            ~asig_fecha_todas["linea_id"].astype(str).str.startswith("TOTALGAV:")
            & ~asig_fecha_todas["linea_id"].astype(str).str.startswith("MANUAL:")
        ]
        for _, r in reales_todas.iterrows():
            oid = str(r.get("orden_id", "") or "").strip()
            if oid:
                mapa_linea_a_orden[str(r["linea_id"])] = oid

    # -------- Selección de pedidos incluidos en esta orden --------
    st.divider()
    st.markdown(f"### ✅ Pedidos incluidos en «{nombre_orden_actual}»")
    st.caption(
        "Marca qué líneas de pedido salen en esta orden. Las que no marques "
        "no aparecerán en el PDF ni en los totales de esta orden — quedan "
        "disponibles para otra orden de este mismo día."
    )

    if st.button("🔄 Regenerar sugerencia FIFO de esta orden (borra ediciones)", type="secondary"):
        reales_orden = asig_fecha[
            ~asig_fecha["linea_id"].astype(str).str.startswith("TOTALGAV:")
            & ~asig_fecha["linea_id"].astype(str).str.startswith("MANUAL:")
        ]
        lineas_a_regenerar = reales_orden["linea_id"].astype(str).unique().tolist()
        for _, a in reales_orden.iterrows():
            db.delete_row("orden_salida_asignaciones", "asignacion_id", a["asignacion_id"])
        for lid in lineas_a_regenerar:
            fila_lin = lineas_producidas[lineas_producidas["linea_id"].astype(str) == lid]
            if fila_lin.empty:
                continue
            _crear_fifo_para_linea(db, username, fecha_sel, orden_sel, fila_lin.iloc[0],
                                    cf_entradas, pasteurizacion, produccion_semi, mapa_kg_nominal)
        st.rerun()

    lineas_incluidas_ids = []
    for _, linea in lineas_producidas.iterrows():
        lid = str(linea["linea_id"])
        pid = str(linea["presentacion_id"])
        orden_de_linea = mapa_linea_a_orden.get(lid)
        ya_en_esta = orden_de_linea == orden_sel
        ya_en_otra = orden_de_linea is not None and orden_de_linea != orden_sel

        etiqueta = (
            f"**{linea['cliente_nombre']}** — Pedido `{linea['pedido_id']}` — "
            f"{linea['tipo_producto']} — {linea['cantidad_kg']:.1f} kg — "
            f"{int(linea['unidades'])} un. {mapa_pres_nombre.get(pid, pid)}"
        )

        with st.container(border=True):
            colchk, coltxt = st.columns([1, 9])
            if ya_en_otra:
                colchk.checkbox("Incluir", value=False, disabled=True,
                                 key=f"chk_{orden_sel}_{lid}", label_visibility="collapsed")
                nombre_otra = mapa_orden_nombre.get(orden_de_linea, orden_de_linea)
                coltxt.markdown(f"~~{etiqueta}~~")
                coltxt.caption(f"🔒 Ya incluida en la orden «{nombre_otra}»")
                continue

            marcado = colchk.checkbox("Incluir", value=ya_en_esta,
                                       key=f"chk_{orden_sel}_{lid}", label_visibility="collapsed")
            coltxt.markdown(etiqueta)
            obs_linea = str(linea.get("observaciones", "") or "").strip()
            if obs_linea:
                coltxt.caption(f"📝 {obs_linea}")

            if marcado and not ya_en_esta:
                _incluir_linea_en_orden(db, username, fecha_sel, orden_sel, linea, asig_fecha_todas,
                                         cf_entradas, pasteurizacion, produccion_semi, mapa_kg_nominal)
                st.rerun()
            elif not marcado and ya_en_esta:
                _excluir_linea_de_orden(db, lid, asig_fecha)
                st.rerun()

            if not marcado:
                continue
            lineas_incluidas_ids.append(lid)

            # -------- Sub-tabla editable de lotes para esta linea --------
            asig_linea = asig_fecha[asig_fecha["linea_id"].astype(str) == lid]
            lotes_disp = _lotes_disponibles_para_linea(linea, cf_entradas, pasteurizacion, produccion_semi, mapa_kg_nominal)

            if not lotes_disp.empty:
                sugerencias_txt = ", ".join(
                    f"{r['lote_producto_id']} ({r['kg_disponible']:.0f} kg)"
                    for _, r in lotes_disp.head(5).iterrows()
                )
                st.caption(f"💡 Lotes disponibles en cuarto frío: {sugerencias_txt}")
            else:
                st.caption("💡 No hay lotes en cuarto frío todavía — puedes escribir un lote planificado.")

            hc1, hc2, hc3, hc4 = st.columns([3, 1, 1, 1])
            hc1.markdown("**Lote (editable)**")
            hc2.markdown("**Kg**")
            hc3.markdown("**Obs.**")
            hc4.markdown("**Quitar**")

            kg_total_asignado = 0.0
            for _, a in asig_linea.iterrows():
                c1, c2, c3, c4 = st.columns([3, 1, 1, 1])
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
                obs_a = c3.text_input(
                    "Obs.", value=str(a.get("observaciones", "") or ""),
                    key=f"obs_{a['asignacion_id']}",
                    label_visibility="collapsed",
                )
                borrar = c4.button("🗑️", key=f"del_{a['asignacion_id']}", help="Eliminar esta línea de asignación")
                if borrar:
                    db.delete_row("orden_salida_asignaciones", "asignacion_id", a["asignacion_id"])
                    st.rerun()
                # Auto-guardar si cambio algo
                if (nuevo_lote != lote_curr or nuevo_kg != kg_curr
                        or obs_a != str(a.get("observaciones", "") or "")):
                    db.update_row("orden_salida_asignaciones", "asignacion_id", a["asignacion_id"], {
                        "lote_producto_id": nuevo_lote,
                        "kg_asignado": nuevo_kg,
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
                    "orden_id": orden_sel,
                })
                st.rerun()

    lineas_incluidas = lineas_producidas[lineas_producidas["linea_id"].astype(str).isin(lineas_incluidas_ids)]

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
            lote_extra = st.text_input("Lote", "", key="man_lote", placeholder="Escribe el lote")
            obs_extra_m = st.text_input("Observaciones", "", key="man_obs")
            st.metric("Kg calculados", f"{kg_extra_m:.1f} kg")

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
                        "gavetas": 0,
                        "usuario": username,
                        "observaciones": obs_extra_m,
                        "creado_en": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "orden_id": orden_sel,
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
    totales_pres = {}  # (tipo, pres_id) -> {"kg": float}  (para tabla por producto+presentación)
    # Sumar de asignaciones de pedidos + manuales (de esta orden)
    for _, a in asig_fecha.iterrows():
        # Las filas marcador de "gavetas reales" no son asignaciones de producto real
        if str(a["linea_id"]).startswith("TOTALGAV:"):
            continue
        kg_a = float(pd.to_numeric(a.get("kg_asignado", 0), errors="coerce") or 0)
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
            totales_prod[tipo] = {"kg": 0.0}
        totales_prod[tipo]["kg"] += kg_a

        clave_pp = (tipo, pres_id)
        if clave_pp not in totales_pres:
            totales_pres[clave_pp] = {"kg": 0.0}
        totales_pres[clave_pp]["kg"] += kg_a

    if totales_prod:
        df_tot = pd.DataFrame([
            {"Producto": t, "Kg totales": f"{v['kg']:.1f}"}
            for t, v in sorted(totales_prod.items())
        ])
        total_general_kg = sum(v["kg"] for v in totales_prod.values())
        df_tot.loc[len(df_tot)] = ["TOTAL GENERAL", f"{total_general_kg:.1f}"]
        st.dataframe(df_tot, use_container_width=True, hide_index=True)
    else:
        st.caption("Todavía no hay pedidos incluidos en esta orden.")

    # -------- TOTALES POR PRODUCTO Y PRESENTACIÓN (gavetas reales editable) --------
    st.divider()
    st.markdown("### 📦 Totales por producto y presentación")
    mapa_gavreal_final = {}  # (tipo, pres_id) -> gavetas reales (para reutilizar al generar el PDF)
    if totales_pres:
        st.caption(
            "La columna **Gavetas reales** es editable: úsala para anotar cuántas "
            "gavetas se arman de cada producto y presentación. Se guarda "
            "automáticamente por orden."
        )
        # Cargar valores de "gavetas reales" ya guardados para esta orden
        asig_gavreal = asig_fecha[asig_fecha["linea_id"].astype(str).str.startswith("TOTALGAV:")]
        mapa_gavreal_guardado = {}
        for _, g in asig_gavreal.iterrows():
            clave = str(g["linea_id"]).replace("TOTALGAV:", "", 1)
            partes_g = clave.split("|", 1)
            tipo_g = partes_g[0] if len(partes_g) > 0 else ""
            pres_id_g = partes_g[1] if len(partes_g) > 1 else ""
            mapa_gavreal_guardado[(tipo_g, pres_id_g)] = {
                "asignacion_id": g["asignacion_id"],
                "gavetas": float(pd.to_numeric(g.get("gavetas", 0), errors="coerce") or 0),
            }

        hp1, hp2, hp3, hp4 = st.columns([2, 2, 1, 1])
        hp1.markdown("**Producto**")
        hp2.markdown("**Presentación**")
        hp3.markdown("**Kg totales**")
        hp4.markdown("**Gavetas reales**")

        tot_kg_pres = 0.0
        tot_gav_real_pres = 0.0
        pres_ordenadas = sorted(
            totales_pres.items(),
            key=lambda kv: (kv[0][0], mapa_pres_nombre.get(kv[0][1], kv[0][1])),
        )
        for (tipo, pres_id), v in pres_ordenadas:
            pres_nombre = mapa_pres_nombre.get(pres_id, pres_id or "Sin presentación")
            cp1, cp2, cp3, cp4 = st.columns([2, 2, 1, 1])
            cp1.markdown(tipo)
            cp2.markdown(pres_nombre)
            cp3.markdown(f"{v['kg']:.1f}")

            clave_g = (tipo, pres_id)
            guardado = mapa_gavreal_guardado.get(clave_g)
            valor_default = guardado["gavetas"] if guardado else 0.0
            nuevo_gavreal = cp4.number_input(
                "Gavetas reales", min_value=0.0, step=0.1, value=float(valor_default),
                format="%.1f",
                key=f"gavreal_{orden_sel}_{tipo}_{pres_id}",
                label_visibility="collapsed",
            )
            mapa_gavreal_final[clave_g] = nuevo_gavreal

            if guardado:
                if nuevo_gavreal != guardado["gavetas"]:
                    db.update_row("orden_salida_asignaciones", "asignacion_id", guardado["asignacion_id"], {
                        "gavetas": nuevo_gavreal,
                    })
            elif nuevo_gavreal != 0:
                asig_id_g = db.siguiente_id("orden_salida_asignaciones", "OSA", fecha_sel)
                db.append_row("orden_salida_asignaciones", {
                    "asignacion_id": asig_id_g,
                    "fecha_entrega": fecha_sel.isoformat(),
                    "linea_id": f"TOTALGAV:{tipo}|{pres_id}",
                    "lote_producto_id": "",
                    "kg_asignado": 0,
                    "gavetas": nuevo_gavreal,
                    "usuario": username,
                    "observaciones": "",
                    "creado_en": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                    "orden_id": orden_sel,
                })

            tot_kg_pres += v["kg"]
            tot_gav_real_pres += nuevo_gavreal

        st.markdown("---")
        cf1, cf2, cf3, cf4 = st.columns([2, 2, 1, 1])
        cf1.markdown("**TOTAL GENERAL**")
        cf3.markdown(f"**{tot_kg_pres:.1f}**")
        cf4.markdown(f"**{tot_gav_real_pres:.1f}**")

    # -------- PDF --------
    st.divider()
    st.markdown("### 📄 Descargar orden de salida (PDF)")
    if st.button("📄 Generar PDF", type="primary", use_container_width=True):
        # Recargar asignaciones actualizadas, filtradas a esta fecha y esta orden
        asignaciones_df = db.get_df("orden_salida_asignaciones")
        asig_fecha_recarga = asignaciones_df[
            asignaciones_df["fecha_entrega"].astype(str) == fecha_sel.isoformat()
        ].copy()
        if "orden_id" in asig_fecha_recarga.columns:
            asig_fecha = asig_fecha_recarga[asig_fecha_recarga["orden_id"].astype(str) == str(orden_sel)].copy()
        else:
            asig_fecha = asig_fecha_recarga.iloc[0:0].copy()
        mapa_cli_pdf = dict(zip(clientes["cliente_id"], clientes["nombre"])) if not clientes.empty else {}
        # Armar datos por cliente (solo lineas incluidas en esta orden)
        datos_pdf = {}
        for _, linea in lineas_incluidas.iterrows():
            cli = linea["cliente_nombre"]
            asig_l = asig_fecha[asig_fecha["linea_id"].astype(str) == str(linea["linea_id"])]
            pres_nombre = mapa_pres_nombre.get(str(linea["presentacion_id"]), str(linea["presentacion_id"]))
            for _, a in asig_l.iterrows():
                kg_a = float(pd.to_numeric(a.get("kg_asignado", 0), errors="coerce") or 0)
                kg_nom = mapa_kg_nominal.get(str(linea["presentacion_id"]), 0)
                unid_lote = int(round(kg_a / kg_nom)) if kg_nom > 0 else 0
                datos_pdf.setdefault(cli, []).append({
                    "producto": linea["tipo_producto"],
                    "lote": str(a.get("lote_producto_id", "") or "—") or "—",
                    "kg_total": float(linea["cantidad_kg"]),
                    "kg_lote": kg_a,
                    "presentacion": pres_nombre,
                    "unidades": unid_lote,
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
            datos_pdf.setdefault(cli_nombre_m, []).append({
                "producto": tipo_m + " (extra)",
                "lote": str(a.get("lote_producto_id", "") or "—") or "—",
                "kg_total": kg_a,
                "kg_lote": kg_a,
                "presentacion": mapa_pres_nombre.get(pres_id_m, pres_id_m),
                "unidades": unid_m,
                "obs_linea": "",
                "obs_asig": str(a.get("observaciones", "") or ""),
            })
        totales_pres_pdf = {}
        for (tipo, pres_id), v in totales_pres.items():
            nombre_p = mapa_pres_nombre.get(pres_id, pres_id or "Sin presentación")
            kg_nom_p = mapa_kg_nominal.get(pres_id, 0)
            unid_p = int(round(v["kg"] / kg_nom_p)) if kg_nom_p > 0 else 0
            totales_pres_pdf[(tipo, nombre_p)] = {
                "kg": v["kg"],
                "unidades": unid_p,
                "gavetas_reales": mapa_gavreal_final.get((tipo, pres_id), 0),
            }
        pdf_bytes = _generar_pdf(fecha_sel, datos_pdf, totales_prod, totales_pres_pdf, nombre_orden_actual)
        nombre_archivo_orden = "".join(
            c if c.isalnum() else "_" for c in nombre_orden_actual
        ).strip("_").lower() or orden_sel
        st.download_button(
            "⬇️ Descargar PDF",
            data=pdf_bytes,
            file_name=f"orden_salida_{fecha_sel.isoformat()}_{nombre_archivo_orden}.pdf",
            mime="application/pdf",
            use_container_width=True,
        )


def _generar_pdf(fecha, datos_por_cliente, totales_prod=None, totales_pres=None, nombre_orden=None):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter,
                            topMargin=1.5*cm, bottomMargin=1.5*cm,
                            leftMargin=1.5*cm, rightMargin=1.5*cm)
    el = []
    titulo = f"Orden de salida — {fecha.strftime('%d/%m/%Y')}"
    if nombre_orden:
        titulo += f" — {nombre_orden}"
    el.append(Paragraph(titulo, ESTILOS["Title"]))
    el.append(Paragraph(
        f"Generado: {datetime.datetime.now().strftime('%d/%m/%Y %H:%M')}",
        ESTILOS["Normal"],
    ))
    el.append(Spacer(1, 0.4*cm))

    if not datos_por_cliente:
        el.append(Paragraph("No hay líneas incluidas en esta orden.", ESTILOS["Normal"]))
        doc.build(el)
        buffer.seek(0)
        return buffer.getvalue()

    for cliente, filas in sorted(datos_por_cliente.items()):
        bloque = []
        bloque.append(Paragraph(f"CLIENTE: {cliente}", ESTILOS["Heading2"]))
        # Tabla: Producto | Lote | Kg total | Kg/lote | Presentación | Unid. | Obs.
        encab = ["Producto", "Lote", "Kg total", "Kg lote", "Presentación", "Unid.", "Observaciones"]
        datos = [[_p(h, negrita=True, pequeño=True) for h in encab]]
        total_kg = 0.0
        total_unid = 0
        for f in filas:
            obs = f["obs_asig"] or f["obs_linea"]
            datos.append([
                _p(f["producto"], pequeño=True),
                _p(f["lote"], pequeño=True),
                _p(f"{f['kg_total']:.1f}", pequeño=True),
                _p(f"{f['kg_lote']:.1f}", pequeño=True),
                _p(f["presentacion"], pequeño=True),
                _p(str(f["unidades"]), pequeño=True),
                _p(obs, pequeño=True),
            ])
            total_kg += f["kg_lote"]
            total_unid += f["unidades"]
        # Fila TOTAL
        datos.append([
            _p("TOTAL", negrita=True, pequeño=True), _p(""), _p(""),
            _p(f"{total_kg:.1f}", negrita=True, pequeño=True),
            _p(""), _p(str(total_unid), negrita=True, pequeño=True), _p(""),
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
        encab_tot = ["Producto", "Kg totales"]
        datos_tot = [[_p(h, negrita=True) for h in encab_tot]]
        for tipo, v in sorted(totales_prod.items()):
            datos_tot.append([
                _p(tipo),
                _p(f"{v['kg']:.1f}"),
            ])
        # Fila TOTAL GENERAL
        tot_kg = sum(v["kg"] for v in totales_prod.values())
        datos_tot.append([
            _p("TOTAL GENERAL", negrita=True),
            _p(f"{tot_kg:.1f}", negrita=True),
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

    # Tabla resumen: totales por producto y presentación (con gavetas reales)
    if totales_pres:
        el.append(Spacer(1, 0.5*cm))
        bloque_pres = []
        bloque_pres.append(Paragraph("Totales por producto y presentación", ESTILOS["Heading2"]))
        encab_pres = ["Producto", "Presentación", "Kg totales", "Unidades", "Gavetas reales"]
        datos_pres = [[_p(h, negrita=True) for h in encab_pres]]
        for (tipo, nombre_p), v in sorted(totales_pres.items()):
            datos_pres.append([
                _p(tipo),
                _p(nombre_p),
                _p(f"{v['kg']:.1f}"),
                _p(str(v["unidades"])),
                _p(f"{v['gavetas_reales']:.1f}"),
            ])
        tot_kg_p = sum(v["kg"] for v in totales_pres.values())
        tot_unid_p = sum(v["unidades"] for v in totales_pres.values())
        tot_gav_real_p = sum(v["gavetas_reales"] for v in totales_pres.values())
        datos_pres.append([
            _p("TOTAL GENERAL", negrita=True),
            _p(""),
            _p(f"{tot_kg_p:.1f}", negrita=True),
            _p(str(tot_unid_p), negrita=True),
            _p(f"{tot_gav_real_p:.1f}", negrita=True),
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
