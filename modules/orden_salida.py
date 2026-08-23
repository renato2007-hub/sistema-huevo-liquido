"""
Orden de salida: para una fecha de entrega puede haber mas de una orden de
salida (distintos camiones, rutas o lugares de entrega). Cada orden agrupa
un subconjunto de las lineas de pedido comprometidas y producidas para esa
fecha -- las lineas no marcadas como incluidas en una orden no aparecen en
ella. Para cada linea incluida, sugiere de que lote de cuarto frio sale
(FIFO), editable, y genera un PDF por cliente para que la ingeniera de
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


def _fmt_unidades_por_pres(unidades_por_pres: dict) -> str:
    """'40 Envase 3.8kg, 20 Envase 2kg' -- para no mostrar un total de
    unidades mezclando presentaciones distintas sin decir de cuál es cada una."""
    if not unidades_por_pres:
        return "—"
    return ", ".join(f"{u} {p}" for p, u in sorted(unidades_por_pres.items()))


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
                            cf_entradas, pasteurizacion, produccion_semi,
                            mapa_kg_nominal, mapa_upg):
    """Crea las filas de asignacion (sugerencia FIFO, o placeholder sin lote
    si no hay stock) para una linea dentro de una orden puntual. Incluye la
    sugerencia automatica de gavetas segun unidades_por_gaveta."""
    lotes_disp = _lotes_disponibles_para_linea(linea, cf_entradas, pasteurizacion, produccion_semi, mapa_kg_nominal)
    sugerencias = _sugerir_asignacion_fifo(linea, lotes_disp, mapa_kg_nominal)
    if not sugerencias:
        sugerencias = [{"lote_producto_id": "", "kg_asignado": float(linea["cantidad_kg"])}]
    pid = str(linea["presentacion_id"])
    upg = mapa_upg.get(pid, 0)
    kg_nom = mapa_kg_nominal.get(pid, 0)
    for s in sugerencias:
        asig_id = db.siguiente_id("orden_salida_asignaciones", "OSA", fecha_sel)
        unidades_asig = (s["kg_asignado"] / kg_nom) if kg_nom > 0 else 0
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
            "orden_id": orden_sel,
        })


def _incluir_linea_en_orden(db, username, fecha_sel, orden_sel, linea, asig_fecha_todas,
                             cf_entradas, pasteurizacion, produccion_semi, mapa_kg_nominal, mapa_upg):
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
                            cf_entradas, pasteurizacion, produccion_semi, mapa_kg_nominal, mapa_upg)


def _excluir_linea_de_orden(db, linea_id, asig_fecha_orden):
    """Quita una linea de la orden actual (borra sus filas de asignacion)."""
    filas = asig_fecha_orden[asig_fecha_orden["linea_id"].astype(str) == str(linea_id)]
    for _, f in filas.iterrows():
        db.delete_row("orden_salida_asignaciones", "asignacion_id", f["asignacion_id"])


def render(db, username, rol):
    st.title("📤 Orden de salida")
    st.caption(
        "Consolidado de todo lo que se va a despachar en una fecha, agrupado por cliente. "
        "En un mismo día puede haber más de una orden de salida (distintos camiones, "
        "rutas o lugares de entrega): elige o crea una orden y marca qué pedidos van en "
        "ella. Sugiere de qué lote de cuarto frío sale cada producto (FIFO), y lo puedes "
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

    # ==================== SELECCIÓN / CREACIÓN DE ORDEN ====================
    st.divider()
    st.markdown("### 🗂️ Orden de salida de esta fecha")
    st.caption(
        "Puede haber más de una orden de salida el mismo día (ej. distintos "
        "camiones, rutas o lugares de entrega). Elige una orden existente o "
        "crea una nueva; luego, más abajo, marca qué pedidos van en ella."
    )

    ordenes_fecha = (
        ordenes_df[ordenes_df["fecha_entrega"].astype(str) == fecha_sel.isoformat()].copy()
        if not ordenes_df.empty else pd.DataFrame(columns=ordenes_df.columns)
    )
    if not ordenes_fecha.empty:
        ordenes_fecha = ordenes_fecha.sort_values("creado_en")
    mapa_orden_nombre = dict(zip(ordenes_fecha["orden_id"], ordenes_fecha["nombre"])) if not ordenes_fecha.empty else {}

    session_key = f"orden_sel_{fecha_sel.isoformat()}"
    sb_key = f"sb_orden_{fecha_sel.isoformat()}"
    pending_sb_key = f"_pending_{sb_key}"
    # El selectbox de abajo tiene su propio estado por su `key`, que
    # persiste entre reruns e ignora el `index` por defecto una vez que el
    # usuario lo tocó (ej. al elegir "Crear nueva orden..."). Streamlit no
    # deja escribir el session_state de un widget en el mismo run en que ya
    # se instanció, asi que para forzar su valor (ej. saltar a la orden
    # recien creada) se deja una bandera pendiente que se aplica aqui,
    # ANTES de instanciar el widget, en el siguiente rerun.
    if pending_sb_key in st.session_state:
        st.session_state[sb_key] = st.session_state.pop(pending_sb_key)

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
            key=sb_key,
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
                ] if not asignaciones_df.empty else pd.DataFrame(columns=asignaciones_df.columns)
                if not asig_todas_fecha.empty:
                    asig_todas_fecha = asig_todas_fecha.drop_duplicates(subset=["asignacion_id"], keep="last")
                if "orden_id" in asig_todas_fecha.columns:
                    huerfanas = asig_todas_fecha[asig_todas_fecha["orden_id"].astype(str).str.strip() == ""]
                else:
                    huerfanas = asig_todas_fecha
                for _, h in huerfanas.iterrows():
                    db.update_row("orden_salida_asignaciones", "asignacion_id", h["asignacion_id"], {
                        "orden_id": nuevo_orden_id,
                    })
            st.session_state[session_key] = nuevo_orden_id
            st.session_state[pending_sb_key] = nombre_nueva.strip()
            st.rerun()
        st.stop()

    # -------- FECHA DE ENTREGA QUE APARECE EN EL PDF/CERTIFICADO --------
    # Por defecto es la fecha de esta orden (fecha_sel), pero es editable:
    # la orden se arma segun la fecha de compromiso de los pedidos, y a veces
    # el certificado se genera un dia para una entrega en otra fecha.
    fila_orden_actual = ordenes_fecha[ordenes_fecha["orden_id"].astype(str) == str(orden_sel)]
    fecha_pdf_guardada = None
    if not fila_orden_actual.empty and "fecha_entrega_pdf" in fila_orden_actual.columns:
        valor_guardado = str(fila_orden_actual.iloc[0].get("fecha_entrega_pdf", "") or "").strip()
        if valor_guardado:
            try:
                fecha_pdf_guardada = datetime.date.fromisoformat(valor_guardado)
            except ValueError:
                fecha_pdf_guardada = None
    fecha_pdf_default = fecha_pdf_guardada or fecha_sel
    fecha_pdf_sel = st.date_input(
        "🚚 Fecha de entrega en el PDF/certificado",
        value=fecha_pdf_default,
        key=f"fecha_pdf_{orden_sel}",
        help="Por defecto es la misma fecha de esta orden. Cámbiala si el "
             "certificado se genera hoy pero la entrega es en otro día.",
    )
    if fecha_pdf_sel != fecha_pdf_default:
        db.update_row("ordenes_salida", "orden_id", orden_sel, {
            "fecha_entrega_pdf": fecha_pdf_sel.isoformat(),
        })

    # ==================== ASIGNACIONES DE LA ORDEN ACTUAL ====================
    asig_fecha_todas = asignaciones_df[
        asignaciones_df["fecha_entrega"].astype(str) == fecha_sel.isoformat()
    ].copy() if not asignaciones_df.empty else pd.DataFrame(columns=asignaciones_df.columns)
    # Deduplicar por asignacion_id (en caso que el sheet tenga duplicados)
    if not asig_fecha_todas.empty:
        asig_fecha_todas = asig_fecha_todas.drop_duplicates(subset=["asignacion_id"], keep="last")

    if "orden_id" in asig_fecha_todas.columns:
        asig_fecha = asig_fecha_todas[asig_fecha_todas["orden_id"].astype(str) == str(orden_sel)].copy()
    else:
        asig_fecha = asig_fecha_todas.iloc[0:0].copy()

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
        "Marca qué líneas de pedido salen en esta orden (lugar, camión o "
        "ruta). Las que no marques no aparecerán en el PDF ni en los totales "
        "de esta orden — quedan disponibles para otra orden de este mismo día."
    )

    if st.button("🔄 Regenerar sugerencia FIFO de esta orden (borra ediciones)", type="secondary"):
        reales_orden = asig_fecha[
            ~asig_fecha["linea_id"].astype(str).str.startswith("TOTALGAV:")
            & ~asig_fecha["linea_id"].astype(str).str.startswith("MANUAL:")
        ]
        lineas_a_regenerar = reales_orden["linea_id"].astype(str).unique().tolist()
        for _, a in reales_orden.iterrows():
            db.delete_row("orden_salida_asignaciones", "asignacion_id", a["asignacion_id"])
        for lid_r in lineas_a_regenerar:
            fila_lin = lineas_producidas[lineas_producidas["linea_id"].astype(str) == lid_r]
            if fila_lin.empty:
                continue
            _crear_fifo_para_linea(db, username, fecha_sel, orden_sel, fila_lin.iloc[0],
                                    cf_entradas, pasteurizacion, produccion_semi, mapa_kg_nominal, mapa_upg)
        st.rerun()

    lineas_incluidas_ids = []
    for _, linea in lineas_producidas.iterrows():
        lid = str(linea["linea_id"])
        pid = str(linea["presentacion_id"])
        chk_key = f"chk_{orden_sel}_{lid}"
        pending_chk_key = f"_pending_{chk_key}"
        # Igual que con el selectbox de orden: el checkbox tiene su propio
        # estado por key, que persiste entre reruns. Si el boton "Eliminar"
        # de mas abajo borra el ultimo lote de una linea, hay que
        # desmarcarla aqui, ANTES de instanciar el checkbox, o si no el
        # checkbox seguiria marcado y la linea se re-incluiria sola creando
        # un lote sugerido nuevo (pareceria que "Eliminar" no hizo nada).
        if pending_chk_key in st.session_state:
            st.session_state[chk_key] = st.session_state.pop(pending_chk_key)

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
                                 key=chk_key, label_visibility="collapsed")
                nombre_otra = mapa_orden_nombre.get(orden_de_linea, orden_de_linea)
                coltxt.markdown(f"~~{etiqueta}~~")
                coltxt.caption(f"🔒 Ya incluida en la orden «{nombre_otra}»")
                continue

            marcado = colchk.checkbox("Incluir", value=ya_en_esta,
                                       key=chk_key, label_visibility="collapsed")
            coltxt.markdown(etiqueta)
            obs_linea = str(linea.get("observaciones", "") or "").strip()
            if obs_linea:
                coltxt.caption(f"📝 {obs_linea}")

            if marcado and not ya_en_esta:
                _incluir_linea_en_orden(db, username, fecha_sel, orden_sel, linea, asig_fecha_todas,
                                         cf_entradas, pasteurizacion, produccion_semi, mapa_kg_nominal, mapa_upg)
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

            kg_nominal_linea = mapa_kg_nominal.get(pid, 0)
            upg_linea = mapa_upg.get(pid, 0)
            producto_txt = f"{linea['tipo_producto']} — {mapa_pres_nombre.get(pid, pid)}"

            hc0, hc1, hc2, hc2b, hc3, hc4, hc5 = st.columns([1.8, 1.8, 0.8, 0.8, 0.8, 1.2, 0.5])
            hc0.markdown("**Producto**")
            hc1.markdown("**Lote (editable)**")
            hc2.markdown("**Kg**")
            hc2b.markdown("**Unidades**")
            hc3.markdown("**Gavetas**")
            hc4.markdown("**Obs.**")
            hc5.markdown("**Quitar**")

            kg_total_asignado = 0.0
            for idx_a, (_, a) in enumerate(asig_linea.iterrows()):
                c0, c1, c2, c2b, c3, c4, c5 = st.columns([1.8, 1.8, 0.8, 0.8, 0.8, 1.2, 0.5])
                # Sufijo compuesto (orden + linea + asignacion + indice) para
                # asegurar unicidad de keys entre ordenes y reruns
                sk = f"{orden_sel}_{lid}_{a['asignacion_id']}_{idx_a}"
                c0.markdown(producto_txt)
                lote_curr = str(a.get("lote_producto_id", "") or "")
                nuevo_lote = c1.text_input(
                    "Lote", value=lote_curr,
                    key=f"lote_{sk}",
                    label_visibility="collapsed",
                    placeholder="Escribe el lote",
                )
                kg_curr = float(pd.to_numeric(a.get("kg_asignado", 0), errors="coerce") or 0)
                nuevo_kg = c2.number_input(
                    "Kg", min_value=0.0, step=0.5, value=kg_curr,
                    key=f"kg_{sk}",
                    label_visibility="collapsed",
                )
                kg_total_asignado += nuevo_kg
                # Unidades: solo informativa, se deriva del kg asignado (no se
                # guarda aparte -- si se necesita otro valor, ajusta el kg).
                unidades_calc = (nuevo_kg / kg_nominal_linea) if kg_nominal_linea > 0 else 0
                c2b.markdown(f"{unidades_calc:.0f}" if unidades_calc else "—")
                # Gavetas: sugerida (auto, segun unidades_por_gaveta de la
                # presentacion) mientras no se haya guardado nada distinto de
                # 0 para esta fila; despues queda libre para editarla a mano.
                # Se compara contra gav_guardado (no gav_curr) para que la
                # sugerencia se guarde sola apenas se muestra, sin necesidad
                # de que el usuario la toque.
                gav_guardado = float(pd.to_numeric(a.get("gavetas", 0), errors="coerce") or 0)
                gav_curr = gav_guardado
                if gav_curr <= 0 and kg_nominal_linea > 0 and upg_linea > 0:
                    gav_curr = float(math.ceil((nuevo_kg / kg_nominal_linea) / upg_linea))
                # gav_curr en la key: mientras no haya un valor guardado propio
                # (gav_guardado 0), la sugerencia recalculada fuerza una key
                # nueva (y por lo tanto un widget nuevo con el valor correcto)
                # cada vez que cambie el kg o se configure unidades_por_gaveta
                # en Catálogos -- si se dejara la key fija, Streamlit ignora el
                # nuevo "value" mientras el campo ya tenga algo guardado en la
                # sesión (por eso antes se quedaba pegado en 0).
                nuevo_gav = c3.number_input(
                    "Gavetas", min_value=0.0, step=0.1, value=gav_curr, format="%.1f",
                    key=f"gav_{sk}_{gav_curr:.1f}",
                    label_visibility="collapsed",
                )
                obs_a = c4.text_input(
                    "Obs.", value=str(a.get("observaciones", "") or ""),
                    key=f"obs_{sk}",
                    label_visibility="collapsed",
                )
                borrar = c5.button("🗑️", key=f"del_{sk}", help="Eliminar esta línea de asignación")
                if borrar:
                    db.delete_row("orden_salida_asignaciones", "asignacion_id", a["asignacion_id"])
                    if len(asig_linea) <= 1:
                        # Se borro el ultimo lote de esta linea: desmarcarla
                        # (bandera pendiente, ver arriba) para que no se
                        # vuelva a incluir sola con un lote sugerido nuevo.
                        st.session_state[pending_chk_key] = False
                    st.rerun()
                # Auto-guardar si cambio algo (o si la sugerencia de gavetas
                # todavia no se habia guardado)
                if (nuevo_lote != lote_curr or nuevo_kg != kg_curr or nuevo_gav != gav_guardado
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
            if st.button("➕ Agregar otro lote a esta línea", key=f"add_{orden_sel}_{lid}"):
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

    # -------- TOTALES POR PRODUCTO Y POR PRESENTACIÓN --------
    st.divider()
    st.markdown("### 📊 Totales")

    totales_prod = {}
    totales_pres = {}  # {pres_id: {kg, gavetas_sugeridas, unidades}}

    # Excluir los ajustes de totales (marcador especial) del cálculo
    asig_reales = asig_fecha[~asig_fecha["linea_id"].astype(str).str.startswith("TOTALGAV:")]

    for _, a in asig_reales.iterrows():
        kg_a = float(pd.to_numeric(a.get("kg_asignado", 0), errors="coerce") or 0)
        gav = float(pd.to_numeric(a.get("gavetas", 0), errors="coerce") or 0)
        # Determinar tipo y presentacion
        if str(a["linea_id"]).startswith("MANUAL:"):
            partes = str(a["linea_id"]).replace("MANUAL:", "").split("|")
            tipo = partes[1] if len(partes) > 1 else "Sin tipo"
            pres_id = partes[2] if len(partes) > 2 else ""
            unid_m = int(partes[3]) if len(partes) > 3 and partes[3].isdigit() else 0
        else:
            fila_lin = lineas_producidas[lineas_producidas["linea_id"].astype(str) == str(a["linea_id"])]
            if fila_lin.empty:
                continue
            tipo = fila_lin.iloc[0]["tipo_producto"]
            pres_id = str(fila_lin.iloc[0]["presentacion_id"])
            kg_nom = mapa_kg_nominal.get(pres_id, 0)
            unid_m = int(round(kg_a / kg_nom)) if kg_nom > 0 else 0

        pres_nombre_u = mapa_pres_nombre.get(pres_id, pres_id) if pres_id else "—"
        if tipo not in totales_prod:
            totales_prod[tipo] = {"kg": 0.0, "gavetas": 0.0, "unidades_por_pres": {}}
        totales_prod[tipo]["kg"] += kg_a
        totales_prod[tipo]["gavetas"] += gav
        totales_prod[tipo]["unidades_por_pres"][pres_nombre_u] = (
            totales_prod[tipo]["unidades_por_pres"].get(pres_nombre_u, 0) + unid_m
        )

        if pres_id not in totales_pres:
            totales_pres[pres_id] = {"kg": 0.0, "gavetas_sugeridas": 0.0, "unidades": 0}
        totales_pres[pres_id]["kg"] += kg_a
        totales_pres[pres_id]["gavetas_sugeridas"] += gav
        totales_pres[pres_id]["unidades"] += unid_m

    # Tabla: totales por producto
    if totales_prod:
        st.markdown("##### Totales por producto")
        df_prod = pd.DataFrame([
            {
                "Producto": t, "Kg totales": f"{v['kg']:.1f}",
                "Unidades": _fmt_unidades_por_pres(v["unidades_por_pres"]),
                "Gavetas": f"{v['gavetas']:.1f}",
            }
            for t, v in sorted(totales_prod.items())
        ])
        total_kg_p = sum(v["kg"] for v in totales_prod.values())
        total_gav_p = sum(v["gavetas"] for v in totales_prod.values())
        df_prod.loc[len(df_prod)] = ["TOTAL GENERAL", f"{total_kg_p:.1f}", "—", f"{total_gav_p:.1f}"]
        st.dataframe(df_prod, use_container_width=True, hide_index=True)
    else:
        st.caption("Todavía no hay pedidos incluidos en esta orden.")

    # Tabla: totales por presentación con gavetas reales editables
    gavetas_reales_map = {}  # {pres_id: gavetas_reales} para pasar al PDF
    if totales_pres:
        st.markdown("##### Totales por presentación")
        st.caption(
            "Las **gavetas sugeridas** son la suma de gavetas de cada línea. "
            "Puedes editar el número **real** que llevará el camión — acepta "
            "un decimal (ej. 12.5) para media gaveta — a veces se consolidan "
            "en menos gavetas físicas al agrupar productos."
        )
        # Cargar ajustes previos (TOTALGAV:<pres_id>)
        ajustes_previos = asig_fecha[asig_fecha["linea_id"].astype(str).str.startswith("TOTALGAV:")]
        mapa_ajuste_prev = {}
        for _, aj in ajustes_previos.iterrows():
            key_pres = str(aj["linea_id"]).replace("TOTALGAV:", "")
            mapa_ajuste_prev[key_pres] = {
                "asignacion_id": aj["asignacion_id"],
                "gavetas_reales": float(pd.to_numeric(aj.get("gavetas", 0), errors="coerce") or 0),
            }

        hc1, hc2, hc3, hc4, hc5 = st.columns([3, 1, 1, 1, 1])
        hc1.markdown("**Presentación**")
        hc2.markdown("**Kg**")
        hc3.markdown("**Unidades**")
        hc4.markdown("**Gavetas sug.**")
        hc5.markdown("**Gavetas reales**")
        for pres_id, v in sorted(totales_pres.items(), key=lambda x: mapa_pres_nombre.get(x[0], x[0])):
            c1, c2, c3, c4, c5 = st.columns([3, 1, 1, 1, 1])
            c1.markdown(mapa_pres_nombre.get(pres_id, pres_id))
            c2.markdown(f"{v['kg']:.1f}")
            c3.markdown(str(v['unidades']))
            c4.markdown(f"{v['gavetas_sugeridas']:.1f}")
            # Gavetas reales editables (persistidas), admite un decimal
            gav_real_prev = mapa_ajuste_prev.get(pres_id, {}).get("gavetas_reales", float(v["gavetas_sugeridas"]))
            gav_real_new = c5.number_input(
                "Reales", min_value=0.0, step=0.1, value=float(gav_real_prev), format="%.1f",
                key=f"gavr_{orden_sel}_{pres_id}", label_visibility="collapsed",
            )
            gavetas_reales_map[pres_id] = gav_real_new
            # Auto-guardar cambios
            if pres_id in mapa_ajuste_prev:
                if gav_real_new != mapa_ajuste_prev[pres_id]["gavetas_reales"]:
                    db.update_row(
                        "orden_salida_asignaciones", "asignacion_id",
                        mapa_ajuste_prev[pres_id]["asignacion_id"],
                        {"gavetas": gav_real_new},
                    )
            else:
                # Solo crear registro si el valor difiere del sugerido
                if gav_real_new != v["gavetas_sugeridas"]:
                    ajuste_id = db.siguiente_id("orden_salida_asignaciones", "TGV", fecha_sel)
                    db.append_row("orden_salida_asignaciones", {
                        "asignacion_id": ajuste_id,
                        "fecha_entrega": fecha_sel.isoformat(),
                        "linea_id": f"TOTALGAV:{pres_id}",
                        "lote_producto_id": "",
                        "kg_asignado": 0,
                        "gavetas": gav_real_new,
                        "usuario": username,
                        "observaciones": "Ajuste manual de gavetas totales reales",
                        "creado_en": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                        "orden_id": orden_sel,
                    })
        # Total general de gavetas reales
        total_gav_real = sum(gavetas_reales_map.values())
        total_gav_sug = sum(v["gavetas_sugeridas"] for v in totales_pres.values())
        st.metric(
            "🚚 Total de gavetas reales (para el camión)",
            f"{total_gav_real:.1f}",
            delta=f"{total_gav_real - total_gav_sug:+.1f} vs sugerido" if abs(total_gav_real - total_gav_sug) > 0.001 else None,
        )

    # -------- PDF --------
    st.divider()
    st.markdown("### 📄 Descargar orden de salida (PDF)")
    if st.button("📄 Generar PDF", type="primary", use_container_width=True):
        # Recargar asignaciones actualizadas, filtradas a esta fecha y esta orden
        asignaciones_df = db.get_df("orden_salida_asignaciones")
        asig_fecha_recarga = asignaciones_df[
            asignaciones_df["fecha_entrega"].astype(str) == fecha_sel.isoformat()
        ].copy()
        if not asig_fecha_recarga.empty:
            asig_fecha_recarga = asig_fecha_recarga.drop_duplicates(subset=["asignacion_id"], keep="last")
        if "orden_id" in asig_fecha_recarga.columns:
            asig_fecha_pdf = asig_fecha_recarga[asig_fecha_recarga["orden_id"].astype(str) == str(orden_sel)].copy()
        else:
            asig_fecha_pdf = asig_fecha_recarga.iloc[0:0].copy()
        mapa_cli_pdf = dict(zip(clientes["cliente_id"], clientes["nombre"])) if not clientes.empty else {}
        # Armar datos por cliente (solo lineas incluidas en esta orden)
        datos_pdf = {}
        for _, linea in lineas_incluidas.iterrows():
            cli = linea["cliente_nombre"]
            asig_l = asig_fecha_pdf[asig_fecha_pdf["linea_id"].astype(str) == str(linea["linea_id"])]
            pres_nombre = mapa_pres_nombre.get(str(linea["presentacion_id"]), str(linea["presentacion_id"]))
            for _, a in asig_l.iterrows():
                kg_a = float(pd.to_numeric(a.get("kg_asignado", 0), errors="coerce") or 0)
                gav = float(pd.to_numeric(a.get("gavetas", 0), errors="coerce") or 0)
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
        for _, a in asig_fecha_pdf.iterrows():
            if not str(a["linea_id"]).startswith("MANUAL:"):
                continue
            partes = str(a["linea_id"]).replace("MANUAL:", "").split("|")
            cli_id_m = partes[0] if len(partes) > 0 else ""
            tipo_m = partes[1] if len(partes) > 1 else ""
            pres_id_m = partes[2] if len(partes) > 2 else ""
            unid_m = int(partes[3]) if len(partes) > 3 and partes[3].isdigit() else 0
            cli_nombre_m = mapa_cli_pdf.get(cli_id_m, cli_id_m)
            kg_a = float(pd.to_numeric(a.get("kg_asignado", 0), errors="coerce") or 0)
            gav = float(pd.to_numeric(a.get("gavetas", 0), errors="coerce") or 0)
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
        pdf_bytes = _generar_pdf(fecha_pdf_sel, datos_pdf, totales_prod, nombre_orden_actual)
        nombre_archivo_orden = "".join(
            c if c.isalnum() else "_" for c in nombre_orden_actual
        ).strip("_").lower() or orden_sel
        st.download_button(
            "⬇️ Descargar PDF",
            data=pdf_bytes,
            file_name=f"orden_salida_{fecha_pdf_sel.isoformat()}_{nombre_archivo_orden}.pdf",
            mime="application/pdf",
            use_container_width=True,
        )


def _generar_pdf(fecha, datos_por_cliente, totales_prod=None, nombre_orden=None):
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
        f"<b>Fecha de entrega: {fecha.strftime('%d/%m/%Y')}</b>",
        ESTILOS["Normal"],
    ))
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
        # Tabla: Producto | Lote | Kg total | Kg/lote | Presentación | Unid. | Gavetas | Obs.
        encab = ["Producto", "Lote", "Kg total", "Kg lote", "Presentación", "Unid.", "Gavetas", "Observaciones"]
        datos = [[_p(h, negrita=True, pequeño=True) for h in encab]]
        total_kg = 0.0
        total_unid = 0
        total_gav = 0.0
        for f in filas:
            obs = f["obs_asig"] or f["obs_linea"]
            datos.append([
                _p(f["producto"], pequeño=True),
                _p(f["lote"], pequeño=True),
                _p(f"{f['kg_total']:.1f}", pequeño=True),
                _p(f"{f['kg_lote']:.1f}", pequeño=True),
                _p(f["presentacion"], pequeño=True),
                _p(str(f["unidades"]), pequeño=True),
                _p(f"{f['gavetas']:.1f}", pequeño=True),
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
            _p(f"{total_gav:.1f}", negrita=True, pequeño=True), _p(""),
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
        encab_tot = ["Producto", "Kg totales", "Unidades", "Gavetas totales"]
        datos_tot = [[_p(h, negrita=True) for h in encab_tot]]
        for tipo, v in sorted(totales_prod.items()):
            datos_tot.append([
                _p(tipo),
                _p(f"{v['kg']:.1f}"),
                _p(_fmt_unidades_por_pres(v["unidades_por_pres"])),
                _p(f"{v['gavetas']:.1f}"),
            ])
        # Fila TOTAL GENERAL
        tot_kg = sum(v["kg"] for v in totales_prod.values())
        tot_gav = sum(v["gavetas"] for v in totales_prod.values())
        datos_tot.append([
            _p("TOTAL GENERAL", negrita=True),
            _p(f"{tot_kg:.1f}", negrita=True),
            _p("—"),
            _p(f"{tot_gav:.1f}", negrita=True),
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

    doc.build(el)
    buffer.seek(0)
    return buffer.getvalue()
