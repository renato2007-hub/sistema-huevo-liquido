"""
Plan de producción diario — consolida todos los pedidos con fecha de producción
asignada para un día específico, mostrando qué hay que producir, cuántas cubetas
se necesitan y si hay suficiente MP disponible.
"""
import datetime
import io
import pandas as pd
import streamlit as st

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether

ESTILOS = getSampleStyleSheet()
_ESTILO_N = ESTILOS["Normal"].clone("n"); _ESTILO_N.fontSize = 9; _ESTILO_N.leading = 11
_ESTILO_B = ESTILOS["Normal"].clone("b"); _ESTILO_B.fontSize = 9; _ESTILO_B.fontName = "Helvetica-Bold"; _ESTILO_B.leading = 11
_ESTILO_T = ESTILOS["Normal"].clone("t"); _ESTILO_T.fontSize = 8; _ESTILO_T.leading = 10

ORDEN_PRODUCTOS = [
    "Huevo entero pasteurizado",
    "Huevo entero sin pasteurizar",
    "Clara pasteurizada",
    "Clara sin pasteurizar",
    "Yema pasteurizada",
    "Yema sin pasteurizar",
]

KG_POR_CUBETA_DEFAULT = 1.724  # kg líquido por cubeta si no hay categoría configurada (fallback)

# Rendimiento de materia prima segun el producto que se este separando: por
# cada cubeta salen aprox. 1.50 kg de huevo entero, o (si se separa) 1.05 kg
# de clara + 0.54 kg de yema al mismo tiempo (mismos valores por defecto que
# DEFAULTS_RENDIMIENTO en produccion_semielaborados.py -- si cambian ahi,
# cambiar tambien aqui).
RENDIMIENTO_KG_POR_CUBETA = {
    "Huevo entero pasteurizado": 1.50,
    "Huevo entero sin pasteurizar": 1.50,
    "Clara pasteurizada": 1.05,
    "Clara sin pasteurizar": 1.05,
    "Yema pasteurizada": 0.54,
    "Yema sin pasteurizar": 0.54,
}

TIPOS_CLARA = {"Clara pasteurizada", "Clara sin pasteurizar"}
TIPOS_YEMA = {"Yema pasteurizada", "Yema sin pasteurizar"}


def _p(txt, negrita=False, pequeño=False):
    est = _ESTILO_B if negrita else (_ESTILO_T if pequeño else _ESTILO_N)
    return Paragraph(str(txt), est)


def _cubetas_necesarias(kg_liquido, kg_por_cubeta):
    if not kg_por_cubeta or kg_por_cubeta <= 0:
        kg_por_cubeta = KG_POR_CUBETA_DEFAULT
    return kg_liquido / kg_por_cubeta


def _cubetas_de_kg_por_tipo(kg_por_tipo: dict) -> float:
    """Cubetas necesarias para cubrir un conjunto de kg por tipo_producto,
    reconociendo que clara y yema son co-productos del mismo quiebre de huevo
    (no se suman aparte -- se toma la que cubra la demanda mayor de las dos).
    Se usa tanto para el total del día como para el de cada turno por separado."""
    kg_clara = sum(kg for tipo, kg in kg_por_tipo.items() if tipo in TIPOS_CLARA)
    kg_yema = sum(kg for tipo, kg in kg_por_tipo.items() if tipo in TIPOS_YEMA)
    factor_clara = RENDIMIENTO_KG_POR_CUBETA["Clara pasteurizada"]
    factor_yema = RENDIMIENTO_KG_POR_CUBETA["Yema pasteurizada"]
    cubetas_clara_yema = max(kg_clara / factor_clara, kg_yema / factor_yema) if (kg_clara > 0 or kg_yema > 0) else 0.0
    cubetas_otros = sum(
        kg / RENDIMIENTO_KG_POR_CUBETA.get(tipo, KG_POR_CUBETA_DEFAULT)
        for tipo, kg in kg_por_tipo.items()
        if tipo not in TIPOS_CLARA and tipo not in TIPOS_YEMA and kg > 0
    )
    return cubetas_clara_yema + cubetas_otros


def _generar_pdf(fecha, consolidado, detalle, cubetas_necesarias_total,
                 cubetas_disponibles, alerta_mp, notas="",
                 turnos_resumen=None):
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(buffer, pagesize=letter,
                             topMargin=1.8*cm, bottomMargin=1.8*cm,
                             leftMargin=1.8*cm, rightMargin=1.8*cm)
    el = []
    el.append(Paragraph("Plan de Producción Diario", ESTILOS["Title"]))
    el.append(Paragraph(f"Fecha: <b>{fecha.strftime('%d/%m/%Y')}</b>  |  "
                         f"Generado: {datetime.datetime.now().strftime('%d/%m/%Y %H:%M')}",
                         ESTILOS["Normal"]))
    el.append(Spacer(1, 0.4*cm))

    if alerta_mp:
        el.append(Paragraph(
            f"⚠ ALERTA: Se necesitan {cubetas_necesarias_total:.0f} cubetas pero solo hay "
            f"{cubetas_disponibles:.0f} disponibles en bodega.",
            ESTILOS["Normal"]
        ))
        el.append(Spacer(1, 0.3*cm))

    el.append(Paragraph("Consolidado del día", ESTILOS["Heading2"]))
    datos = [[_p(h, negrita=True) for h in ["Producto", "Kg a producir", "Cubetas", "Pedidos", "Clientes"]]]
    for row in consolidado:
        datos.append([
            _p(row["producto"]), _p(f"{row['kg']:.1f} kg"),
            _p(f"{row.get('cubetas', 0):.0f}"),
            _p(", ".join(row["pedidos"][:3]) + ("..." if len(row["pedidos"]) > 3 else "")),
            _p(", ".join(sorted(set(row["clientes"]))[:3])),
        ])
    datos.append([
        _p("TOTAL", negrita=True),
        _p(f"{sum(r['kg'] for r in consolidado):.1f} kg", negrita=True),
        _p(f"{cubetas_necesarias_total:.0f}", negrita=True),
        _p(""), _p(""),
    ])
    t = Table(datos, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#1565c0")),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("BACKGROUND", (0,-1), (-1,-1), colors.HexColor("#f5f5f5")),
        ("GRID", (0,0), (-1,-1), 0.3, colors.HexColor("#dddddd")),
        ("BOTTOMPADDING", (0,0), (-1,-1), 5),
        ("TOPPADDING", (0,0), (-1,-1), 5),
    ]))
    el.append(t)
    el.append(Spacer(1, 0.4*cm))

    if turnos_resumen:
        el.append(Paragraph("Distribución por turno", ESTILOS["Heading2"]))
        for tr in turnos_resumen:
            bloque_turno = [Paragraph(f"<b>{tr['turno']}</b>", ESTILOS["Normal"])]
            datos_t = [[_p(h, negrita=True) for h in ["Producto", "Kg a producir"]]]
            for prod, kg in tr["kg_por_producto"].items():
                datos_t.append([_p(prod), _p(f"{kg:.1f} kg")])
            datos_t.append([
                _p("Cubetas necesarias para este turno", negrita=True),
                _p(f"{tr['cubetas']:.0f}", negrita=True),
            ])
            tt = Table(datos_t, repeatRows=1, colWidths=[10*cm, 4*cm])
            tt.setStyle(TableStyle([
                ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#6a1b9a")),
                ("TEXTCOLOR", (0,0), (-1,0), colors.white),
                ("BACKGROUND", (0,-1), (-1,-1), colors.HexColor("#f5f5f5")),
                ("GRID", (0,0), (-1,-1), 0.3, colors.HexColor("#dddddd")),
                ("BOTTOMPADDING", (0,0), (-1,-1), 4),
                ("TOPPADDING", (0,0), (-1,-1), 4),
            ]))
            bloque_turno.append(tt)
            bloque_turno.append(Spacer(1, 0.25*cm))
            el.append(KeepTogether(bloque_turno))
        el.append(Spacer(1, 0.2*cm))

    mp_texto = (f"Cubetas necesarias: {cubetas_necesarias_total:.0f}  |  "
                f"Disponibles en bodega: {cubetas_disponibles:.0f}")
    el.append(Paragraph(mp_texto, ESTILOS["Normal"]))
    el.append(Spacer(1, 0.3*cm))

    el.append(Spacer(1, 0.5*cm))

    el.append(Paragraph("Detalle por pedido", ESTILOS["Heading2"]))
    det = [[_p(h, negrita=True) for h in
            ["Pedido", "Cliente", "Producto", "Presentación", "Kg", "Unid.", "Turno", "Entrega", "Observaciones"]]]
    for row in detalle:
        det.append([
            _p(row["pedido_id"], pequeño=True), _p(row["cliente"], pequeño=True),
            _p(row["tipo_producto"], pequeño=True), _p(row["presentacion"], pequeño=True),
            _p(f"{row['kg']:.1f}", pequeño=True), _p(str(row["unidades"]), pequeño=True),
            _p(row.get("turno_nombre", "—") or "—", pequeño=True),
            _p(row["fecha_entrega"], pequeño=True),
            _p(row.get("observaciones", "") or "", pequeño=True),
        ])
    td = Table(det, repeatRows=1)
    td.setStyle(TableStyle([
        ("BACKGROUND", (0,0), (-1,0), colors.HexColor("#2e7d32")),
        ("TEXTCOLOR", (0,0), (-1,0), colors.white),
        ("GRID", (0,0), (-1,-1), 0.3, colors.HexColor("#dddddd")),
        ("ROWBACKGROUNDS", (0,1), (-1,-1), [colors.white, colors.HexColor("#f5f5f5")]),
        ("BOTTOMPADDING", (0,0), (-1,-1), 4),
        ("TOPPADDING", (0,0), (-1,-1), 4),
    ]))
    el.append(td)

    # Bloque de Notas al final del PDF (texto escrito desde la app)
    el.append(Spacer(1, 1.0*cm))
    el.append(Paragraph("Notas", ESTILOS["Heading2"]))
    if notas and notas.strip():
        # Preservar saltos de linea del text_area
        texto_html = notas.strip().replace("\n", "<br/>")
        el.append(Paragraph(texto_html, ESTILOS["Normal"]))
    else:
        # Si no hay notas, dejar 5 lineas en blanco para escribir a mano
        notas_data = [[_p("")] for _ in range(5)]
        notas_tabla = Table(notas_data, colWidths=[17*cm])
        notas_tabla.setStyle(TableStyle([
            ("LINEBELOW", (0,0), (-1,-1), 0.5, colors.HexColor("#999999")),
            ("BOTTOMPADDING", (0,0), (-1,-1), 14),
            ("TOPPADDING", (0,0), (-1,-1), 6),
        ]))
        el.append(notas_tabla)

    doc.build(el)
    buffer.seek(0)
    return buffer.getvalue()


def render(db, username, rol):
    st.title("📅 Plan de producción diario")

    tab_plan, tab_hist = st.tabs(["🗓️ Planificar", "📚 Historial de planes"])

    with tab_plan:
        _render_planificar(db, username, rol)

    with tab_hist:
        _render_historial(db, username, rol)


def _render_planificar(db, username, rol):
    pedidos_df    = db.get_df("pedidos")
    pedidos_lineas_df = db.get_df("pedidos_lineas")
    clientes_df   = db.get_df("clientes")
    presentac_df  = db.get_df("presentaciones")
    recepciones   = db.get_df("recepciones_mp")
    categorias    = db.get_df("categorias_huevo")
    cf_entradas   = db.get_df("cuarto_frio_entradas")
    pasteurizacion= db.get_df("pasteurizacion_envasado")
    produccion    = db.get_df("produccion_semielaborados")

    hoy = datetime.date.today()
    fecha_sel = st.date_input("📅 Fecha de producción a planificar", value=hoy)

    if pedidos_lineas_df.empty:
        st.info("No hay líneas de pedido registradas todavía.")
        return

    # Trabajamos con LINEAS de pedido (cada linea tiene su propia fecha_produccion).
    # Se hace JOIN con la cabecera pedidos para traer cliente_id, estado, urgente.
    pedidos_lineas_df = pedidos_lineas_df.copy()
    pedidos_lineas_df["producido_bool"] = pedidos_lineas_df["producido"].astype(str).str.upper().isin(["TRUE","1","SI","SÍ"])
    cols_cab = [c for c in ["pedido_id", "cliente_id", "estado", "urgente", "pedido_cliente_ref"] if c in pedidos_df.columns]
    if cols_cab and not pedidos_df.empty:
        pedidos_lineas_df = pedidos_lineas_df.merge(pedidos_df[cols_cab], on="pedido_id", how="left")
    if "estado" in pedidos_lineas_df.columns:
        estado_norm = pedidos_lineas_df["estado"].fillna("pendiente").astype(str).str.strip().str.lower()
        pedidos_lineas_df = pedidos_lineas_df[estado_norm != "cancelado"]

    pedidos_fecha = pedidos_lineas_df[
        (pedidos_lineas_df["fecha_produccion"].astype(str) == fecha_sel.isoformat()) &
        (~pedidos_lineas_df["producido_bool"])
    ].copy()

    if pedidos_fecha.empty:
        st.info(f"No hay líneas de pedido con fecha de producción asignada para el {fecha_sel.strftime('%d/%m/%Y')}.")
        st.caption("Asigna fechas de producción en Recepción de pedidos → Todos los pedidos.")
        return

    pedidos_fecha["cantidad_kg"] = pd.to_numeric(pedidos_fecha["cantidad_kg"], errors="coerce").fillna(0)
    # La columna se llama 'unidades' en pedidos_lineas; crear alias unidades_solicitadas para compatibilidad
    if "unidades" in pedidos_fecha.columns:
        pedidos_fecha["unidades_solicitadas"] = pd.to_numeric(pedidos_fecha["unidades"], errors="coerce").fillna(0)
    else:
        pedidos_fecha["unidades_solicitadas"] = 0

    # Resolver nombres de cliente y presentación
    mapa_cli = dict(zip(clientes_df["cliente_id"], clientes_df["nombre"])) if not clientes_df.empty else {}
    mapa_pres = dict(zip(presentac_df["presentacion_id"], presentac_df["nombre"])) if not presentac_df.empty else {}
    pedidos_fecha["cliente_nombre"] = pedidos_fecha["cliente_id"].map(mapa_cli).fillna(pedidos_fecha["cliente_id"])
    pedidos_fecha["presentacion_nombre"] = pedidos_fecha["presentacion_id"].map(mapa_pres).fillna(pedidos_fecha["presentacion_id"])

    # Cubetas necesarias: usa el rendimiento especifico de RENDIMIENTO_KG_POR_CUBETA
    # segun el tipo de producto (huevo entero / clara / yema). Si aparece un
    # tipo_producto que no esta en esa tabla, cae a un rendimiento generico
    # calculado desde categorias_huevo.
    kg_por_cubeta_fallback = KG_POR_CUBETA_DEFAULT
    if not categorias.empty and "kg_promedio_cubeta" in categorias.columns:
        vals = pd.to_numeric(categorias["kg_promedio_cubeta"], errors="coerce").dropna()
        if not vals.empty:
            kg_por_cubeta_fallback = float(vals.mean())
    rendimiento_liquido_fallback = 0.83  # pct promedio de kg líquido vs kg bruto
    kg_liquido_por_cubeta_fallback = kg_por_cubeta_fallback * rendimiento_liquido_fallback

    # Cubetas disponibles en bodega
    cubetas_disponibles = 0
    if not recepciones.empty:
        recepciones["cubetas_saldo"] = pd.to_numeric(recepciones["cubetas_saldo"], errors="coerce").fillna(0)
        cubetas_disponibles = recepciones[recepciones["cubetas_saldo"] > 0]["cubetas_saldo"].sum()

    # Stock en cuarto frío disponible por tipo de producto
    stock_cf = {}
    if not cf_entradas.empty and not pasteurizacion.empty:
        cf_saldo = cf_entradas.copy()
        cf_saldo["saldo"] = pd.to_numeric(cf_saldo["saldo"], errors="coerce").fillna(0)
        cf_saldo = cf_saldo[cf_saldo["saldo"] > 0]
        if not cf_saldo.empty and not produccion.empty:
            cf_saldo = cf_saldo.merge(
                pasteurizacion[["lote_producto_id","lote_semielaborado_id"]],
                on="lote_producto_id", how="left",
            )
            cf_saldo = cf_saldo.merge(
                produccion[["lote_semielaborado_id","tipo_producto"]],
                on="lote_semielaborado_id", how="left",
            )
            if not presentac_df.empty:
                cf_saldo = cf_saldo.merge(
                    presentac_df[["presentacion_id","kg_nominal"]], on="presentacion_id", how="left"
                )
                cf_saldo["kg_nominal"] = pd.to_numeric(cf_saldo["kg_nominal"], errors="coerce").fillna(0)
            else:
                cf_saldo["kg_nominal"] = 0
            cf_saldo["kg_cf"] = cf_saldo["saldo"] * cf_saldo["kg_nominal"]
            stock_cf = cf_saldo.groupby("tipo_producto")["kg_cf"].sum().to_dict()

    # ── CONSOLIDADO ────────────────────────────────────────────────────────────
    # Traer nombres de turno para mostrarlos bonito
    turnos_cat = db.get_df("turnos")
    mapa_turnos = {}
    if not turnos_cat.empty:
        for _, tr in turnos_cat.iterrows():
            mapa_turnos[str(tr["turno_id"])] = str(tr.get("nombre", tr["turno_id"]))

    if "turno" not in pedidos_fecha.columns:
        pedidos_fecha["turno"] = ""
    pedidos_fecha["turno"] = pedidos_fecha["turno"].fillna("").astype(str).str.strip()

    turnos_del_dia = sorted([t for t in pedidos_fecha["turno"].unique() if t and t.lower() not in ("nan", "none")])
    hay_sin_asignar = (pedidos_fecha["turno"] == "").any() or (pedidos_fecha["turno"].str.lower().isin(["nan", "none"])).any()

    consolidado = []
    for tipo in pedidos_fecha["tipo_producto"].unique():
        grupo = pedidos_fecha[pedidos_fecha["tipo_producto"] == tipo]
        kg_total = grupo["cantidad_kg"].sum()
        # Desglose por turno
        kg_por_turno = {}
        for t in turnos_del_dia:
            kg_t = grupo[grupo["turno"] == t]["cantidad_kg"].sum()
            if kg_t > 0:
                kg_por_turno[t] = float(kg_t)
        kg_sin_turno = float(grupo[grupo["turno"].isin(["", "nan", "None"])]["cantidad_kg"].sum())
        kg_liquido_por_cubeta_tipo = RENDIMIENTO_KG_POR_CUBETA.get(tipo, kg_liquido_por_cubeta_fallback)
        cubetas_tipo = _cubetas_necesarias(kg_total, kg_liquido_por_cubeta_tipo)
        consolidado.append({
            "producto": tipo,
            "kg": kg_total,
            "cubetas": cubetas_tipo,
            "kg_por_turno": kg_por_turno,
            "kg_sin_turno": kg_sin_turno,
            "pedidos": list(grupo["pedido_id"]),
            "clientes": list(grupo["cliente_nombre"].unique()),
        })
    consolidado.sort(key=lambda r: ORDEN_PRODUCTOS.index(r["producto"]) if r["producto"] in ORDEN_PRODUCTOS else 99)

    kg_total_dia = sum(r["kg"] for r in consolidado)

    # Clara y yema salen del MISMO quiebre de huevo (co-productos) -- no se
    # suman sus cubetas por separado como si fueran dos quiebres distintos.
    # Se necesitan las cubetas que alcancen para cubrir la demanda MAYOR de
    # las dos; la otra sale "gratis" del mismo quiebre (con sobrante si su
    # demanda es menor). Huevo entero es un quiebre aparte, sí se suma normal.
    kg_clara_total = sum(r["kg"] for r in consolidado if r["producto"] in TIPOS_CLARA)
    kg_yema_total = sum(r["kg"] for r in consolidado if r["producto"] in TIPOS_YEMA)
    factor_clara = RENDIMIENTO_KG_POR_CUBETA["Clara pasteurizada"]
    factor_yema = RENDIMIENTO_KG_POR_CUBETA["Yema pasteurizada"]
    cubetas_clara_yema = 0.0
    if kg_clara_total > 0 or kg_yema_total > 0:
        cubetas_clara_yema = max(kg_clara_total / factor_clara, kg_yema_total / factor_yema)

    cubetas_otros = sum(
        r["cubetas"] for r in consolidado
        if r["producto"] not in TIPOS_CLARA and r["producto"] not in TIPOS_YEMA
    )
    cubetas_necesarias_total = cubetas_clara_yema + cubetas_otros
    alerta_mp = cubetas_necesarias_total > cubetas_disponibles

    # Resumen por turno: kg por producto y cubetas (con la logica de
    # co-producto clara/yema aplicada POR TURNO, no solo al total del dia) --
    # se usa tanto en pantalla como en el PDF.
    turnos_resumen = []
    claves_turno = [(t, mapa_turnos.get(t, t)) for t in turnos_del_dia]
    if hay_sin_asignar:
        claves_turno.append(("", "Sin asignar"))
    for clave, nombre in claves_turno:
        if clave == "":
            kg_por_tipo_t = {row["producto"]: row["kg_sin_turno"] for row in consolidado}
        else:
            kg_por_tipo_t = {row["producto"]: row["kg_por_turno"].get(clave, 0) for row in consolidado}
        kg_por_tipo_t = {k: v for k, v in kg_por_tipo_t.items() if v > 0}
        if not kg_por_tipo_t:
            continue
        turnos_resumen.append({
            "turno": nombre,
            "kg_por_producto": kg_por_tipo_t,
            "kg_total": sum(kg_por_tipo_t.values()),
            "cubetas": _cubetas_de_kg_por_tipo(kg_por_tipo_t),
        })
    mapa_cubetas_por_turno = {r["turno"]: r["cubetas"] for r in turnos_resumen}

    # ── VISTA EN PANTALLA ──────────────────────────────────────────────────────
    st.markdown(f"### 📋 Plan del {fecha_sel.strftime('%d/%m/%Y')}")

    # Tabla resumen por turno (si hay al menos un turno asignado)
    if turnos_del_dia or hay_sin_asignar:
        st.markdown("##### 🔀 Distribución por turno")
        fila_tur = {"Producto": [c["producto"] for c in consolidado] + ["🥚 Cubetas necesarias"]}
        for t in turnos_del_dia:
            nombre_t = mapa_turnos.get(t, t)
            fila_tur[nombre_t] = (
                [f"{c['kg_por_turno'].get(t, 0):.1f} kg" if c['kg_por_turno'].get(t, 0) > 0 else "—" for c in consolidado]
                + [f"{mapa_cubetas_por_turno.get(nombre_t, 0):.0f}"]
            )
        if hay_sin_asignar:
            fila_tur["Sin asignar"] = (
                [f"{c['kg_sin_turno']:.1f} kg" if c['kg_sin_turno'] > 0 else "—" for c in consolidado]
                + [f"{mapa_cubetas_por_turno.get('Sin asignar', 0):.0f}"]
            )
        fila_tur["Total"] = [f"{c['kg']:.1f} kg" for c in consolidado] + [f"{cubetas_necesarias_total:.0f}"]
        st.dataframe(pd.DataFrame(fila_tur), use_container_width=True, hide_index=True)
        st.caption(
            "Las cubetas de cada turno también aplican la lógica de co-producto "
            "clara/yema (no se suman por separado dentro del mismo turno)."
        )
        st.write("")

    # ── Asignar turno a las líneas de este día ──────────────────────────────
    with st.container(border=True):
        st.markdown("##### 🔀 Asignar turno de producción")
        if turnos_cat.empty:
            st.caption("Configura al menos un turno en Catálogos → Turnos para poder asignarlos aquí.")
        else:
            st.caption(
                "Indica en qué turno se va a producir cada línea de este día — déjalas "
                "todas en el mismo turno si ese día solo trabajas uno."
            )
            opciones_turno_nombre = ["Sin asignar"] + [mapa_turnos.get(str(t), str(t)) for t in turnos_cat["turno_id"]]
            mapa_nombre_a_id = {v: k for k, v in mapa_turnos.items()}

            editor_df = pedidos_fecha[
                ["linea_id", "pedido_id", "cliente_nombre", "tipo_producto", "cantidad_kg", "turno"]
            ].reset_index(drop=True).copy()
            editor_df["Turno"] = editor_df["turno"].apply(lambda t: mapa_turnos.get(t, "Sin asignar") if t else "Sin asignar")
            editor_mostrar = editor_df.rename(columns={
                "pedido_id": "Pedido", "cliente_nombre": "Cliente",
                "tipo_producto": "Producto", "cantidad_kg": "Kg",
            })[["Pedido", "Cliente", "Producto", "Kg", "Turno"]]

            editado = st.data_editor(
                editor_mostrar,
                column_config={
                    "Turno": st.column_config.SelectboxColumn("Turno", options=opciones_turno_nombre, required=True),
                    "Pedido": st.column_config.TextColumn(disabled=True),
                    "Cliente": st.column_config.TextColumn(disabled=True),
                    "Producto": st.column_config.TextColumn(disabled=True),
                    "Kg": st.column_config.NumberColumn(disabled=True, format="%.1f"),
                },
                hide_index=True, use_container_width=True,
                key=f"turno_editor_{fecha_sel.isoformat()}",
            )
            if st.button("💾 Guardar turnos", key="btn_guardar_turnos"):
                cambios = 0
                for idx in editor_df.index:
                    linea_id = editor_df.loc[idx, "linea_id"]
                    turno_anterior = str(editor_df.loc[idx, "turno"] or "")
                    turno_nuevo_nombre = editado.loc[idx, "Turno"]
                    turno_nuevo_id = mapa_nombre_a_id.get(turno_nuevo_nombre, "") if turno_nuevo_nombre != "Sin asignar" else ""
                    if str(turno_nuevo_id) != turno_anterior:
                        db.update_row("pedidos_lineas", "linea_id", linea_id, {"turno": turno_nuevo_id})
                        cambios += 1
                if cambios:
                    st.success(f"✅ Turno actualizado en {cambios} línea(s).")
                    st.rerun()
                else:
                    st.info("No hubo cambios de turno para guardar.")

    st.write("")

    # Alerta MP
    if alerta_mp:
        faltante = cubetas_necesarias_total - cubetas_disponibles
        st.error(
            f"⚠️ **Alerta de materia prima**: se necesitan **{cubetas_necesarias_total:.0f} cubetas** "
            f"pero solo hay **{cubetas_disponibles:.0f} disponibles** en bodega — "
            f"faltan **{faltante:.0f} cubetas** para cubrir todo el plan."
        )
    else:
        st.success(
            f"✅ Hay suficiente MP: se necesitan {cubetas_necesarias_total:.0f} cubetas y "
            f"hay {cubetas_disponibles:.0f} disponibles."
        )

    st.write("")

    # Tarjetas por producto
    cols = st.columns(min(len(consolidado), 3))
    for col, row in zip(cols * (len(consolidado)//len(cols)+1), consolidado):
        kg_en_cf = stock_cf.get(row["producto"], 0)
        kg_a_producir = max(0, row["kg"] - kg_en_cf)
        with col:
            with st.container(border=True):
                st.markdown(f"**{row['producto']}**")
                st.metric("Kg pedidos", f"{row['kg']:,.1f} kg")
                if row["producto"] in TIPOS_CLARA or row["producto"] in TIPOS_YEMA:
                    co_producto = "yema" if row["producto"] in TIPOS_CLARA else "clara"
                    st.caption(
                        f"🥚 Cubetas necesarias: {cubetas_clara_yema:.0f} "
                        f"(compartidas con {co_producto} — mismo quiebre)"
                    )
                else:
                    st.caption(f"🥚 Cubetas necesarias: {row['cubetas']:.0f}")
                if kg_en_cf > 0:
                    st.caption(f"✅ {kg_en_cf:.1f} kg ya en cuarto frío → producir {kg_a_producir:.1f} kg adicionales")
                else:
                    st.caption(f"Producir: {kg_a_producir:.1f} kg")
                st.caption(f"Pedidos: {len(row['pedidos'])} | Clientes: {', '.join(sorted(set(row['clientes'])))}")

    st.write("")

    # Resumen de MP
    with st.container(border=True):
        st.markdown("##### 🥚 Materia prima necesaria")
        c1, c2, c3 = st.columns(3)
        c1.metric("Kg totales a producir", f"{kg_total_dia:,.1f} kg")
        c2.metric("Cubetas necesarias", f"{cubetas_necesarias_total:.0f}")
        c3.metric("Cubetas disponibles en bodega", f"{cubetas_disponibles:.0f}",
                  delta=f"{cubetas_disponibles - cubetas_necesarias_total:+.0f}",
                  delta_color="normal" if not alerta_mp else "inverse")

        if kg_clara_total > 0 and kg_yema_total > 0:
            cubetas_suma_ingenua = (kg_clara_total / factor_clara) + (kg_yema_total / factor_yema)
            kg_clara_producida = cubetas_clara_yema * factor_clara
            kg_yema_producida = cubetas_clara_yema * factor_yema
            sobrante_clara = kg_clara_producida - kg_clara_total
            sobrante_yema = kg_yema_producida - kg_yema_total
            texto_sobrante = ""
            if sobrante_clara > 0.5:
                texto_sobrante = f" (sobran ~{sobrante_clara:.1f} kg de clara respecto a lo pedido hoy)"
            elif sobrante_yema > 0.5:
                texto_sobrante = f" (sobran ~{sobrante_yema:.1f} kg de yema respecto a lo pedido hoy)"
            st.caption(
                f"ℹ️ Clara y yema salen del mismo quiebre de huevo, no se suman por separado: "
                f"se necesitan **{cubetas_clara_yema:.0f} cubetas** para cubrir "
                f"{kg_clara_total:.1f} kg de clara + {kg_yema_total:.1f} kg de yema"
                f"{texto_sobrante} — no {cubetas_suma_ingenua:.0f} como saldría sumándolas."
            )

    st.write("")

    # ── Asignación de lotes de MP ────────────────────────────────────────────
    with st.container(border=True):
        st.markdown("##### 🥚 Asignación de lotes de huevo para este día (FIFO automático)")
        st.caption(
            "El sistema selecciona automáticamente los lotes siguiendo **FIFO** "
            "(el más antiguo primero) según la cantidad total que decidas usar. "
            "Solo edita la cantidad si necesitas más o menos que la recomendada."
        )

        plan_mp_df = db.get_df("plan_mp_asignado")
        plan_hoy   = plan_mp_df[plan_mp_df["fecha"].astype(str) == fecha_sel.isoformat()] if not plan_mp_df.empty else pd.DataFrame()

        # Recepciones disponibles (solo activas, con saldo)
        if recepciones.empty:
            st.info("No hay recepciones de MP registradas.")
        else:
            recepciones_a = recepciones.copy()
            if "estado" in recepciones_a.columns:
                estado_norm = recepciones_a["estado"].fillna("activo").astype(str).str.strip().str.lower()
                recepciones_a = recepciones_a[estado_norm.isin(["", "activo"])]
            recepciones_a["cubetas_saldo"] = pd.to_numeric(recepciones_a["cubetas_saldo"], errors="coerce").fillna(0)
            recepciones_a["fecha_dt"] = pd.to_datetime(recepciones_a["fecha"], errors="coerce")
            rec_disp = recepciones_a[recepciones_a["cubetas_saldo"] > 0].sort_values("fecha_dt", na_position="last")

            if rec_disp.empty:
                st.info("No hay lotes con saldo disponible en bodega.")
            else:
                total_bodega = int(rec_disp["cubetas_saldo"].sum())
                st.info(
                    f"📦 **{total_bodega} cubetas disponibles** en bodega — se descontarán FIFO por fecha de recepción."
                )

                # Cantidad a usar: recomendada = necesarias, editable
                cant_default = int(round(cubetas_necesarias_total)) if cubetas_necesarias_total > 0 else 0
                cant_a_usar = st.number_input(
                    f"Cubetas a usar (recomendadas por el plan: **{cant_default}**)",
                    min_value=0, max_value=total_bodega, step=1,
                    value=cant_default, key=f"pmp_cant_total_{fecha_sel.isoformat()}",
                )

                # Simular el descuento FIFO en vivo (no persiste — solo para mostrar)
                sim_asignaciones = []
                restante_sim = cant_a_usar
                for _, lote in rec_disp.iterrows():
                    if restante_sim <= 0:
                        break
                    saldo = float(lote["cubetas_saldo"])
                    tomar = min(saldo, restante_sim)
                    sim_asignaciones.append({
                        "recepcion_id": lote["recepcion_id"],
                        "fecha": str(lote.get("fecha", "")),
                        "cubetas": tomar,
                        "origen_id": str(lote.get("origen_id", "")),
                    })
                    restante_sim -= tomar

                if sim_asignaciones:
                    st.markdown("**Se van a asignar estos lotes (FIFO):**")
                    df_sim = pd.DataFrame(sim_asignaciones)
                    st.dataframe(
                        df_sim.rename(columns={
                            "recepcion_id": "Lote", "fecha": "Fecha recep.",
                            "cubetas": "Cubetas", "origen_id": "Proveedor",
                        }),
                        use_container_width=True, hide_index=True,
                    )
                    total_asignado_sim = sum(a["cubetas"] for a in sim_asignaciones)
                    diff = total_asignado_sim - cubetas_necesarias_total
                    if diff >= 0:
                        st.success(f"✅ {total_asignado_sim:.0f} cubetas — cubre el plan ({cubetas_necesarias_total:.0f} necesarias)")
                    else:
                        st.warning(f"⚠️ {total_asignado_sim:.0f} de {cubetas_necesarias_total:.0f} cubetas necesarias — faltan {abs(diff):.0f}")

                # Ver asignaciones ya guardadas del día (si las hay) y ofrecer limpiar
                if not plan_hoy.empty:
                    plan_hoy["cubetas_asignadas"] = pd.to_numeric(plan_hoy["cubetas_asignadas"], errors="coerce").fillna(0)
                    total_guardado = plan_hoy["cubetas_asignadas"].sum()
                    st.caption(f"ℹ️ Ya hay una asignación guardada para este día con {int(total_guardado)} cubetas.")

                col_g, col_l = st.columns(2)
                if col_g.button("💾 Confirmar y guardar asignación", type="primary", use_container_width=True):
                    # Limpiar asignación previa del día (si existe)
                    if not plan_hoy.empty:
                        for _, row in plan_hoy.iterrows():
                            db.delete_row("plan_mp_asignado", "plan_mp_id", row["plan_mp_id"])
                    # Guardar la nueva asignación FIFO
                    for a in sim_asignaciones:
                        pmp_id = db.siguiente_id("plan_mp_asignado", "PMP", fecha_sel)
                        db.append_row("plan_mp_asignado", {
                            "plan_mp_id": pmp_id,
                            "fecha": fecha_sel.isoformat(),
                            "recepcion_id": a["recepcion_id"],
                            "cubetas_asignadas": a["cubetas"],
                            "usuario": username,
                            "observaciones": "Asignacion automatica FIFO",
                        })
                    st.success(f"✅ Plan guardado: {int(sum(a['cubetas'] for a in sim_asignaciones))} cubetas en {len(sim_asignaciones)} lote(s).")
                    st.rerun()
                if not plan_hoy.empty and col_l.button("🗑️ Limpiar asignación del día", use_container_width=True):
                    for _, row in plan_hoy.iterrows():
                        db.delete_row("plan_mp_asignado", "plan_mp_id", row["plan_mp_id"])
                    st.success("Asignación eliminada.")
                    st.rerun()

    st.write("")

    # Detalle por pedido
    with st.expander("📋 Ver detalle de todos los pedidos de este día", expanded=True):
        # Merge observaciones de linea con observaciones del pedido para mostrar ambos
        if "observaciones" not in pedidos_fecha.columns:
            pedidos_fecha["observaciones"] = ""
        detalle_df = pedidos_fecha[[
            "pedido_id", "cliente_nombre", "tipo_producto",
            "presentacion_nombre", "cantidad_kg", "unidades_solicitadas", "fecha_entrega",
            "observaciones",
        ]].rename(columns={
            "pedido_id": "Pedido",
            "cliente_nombre": "Cliente",
            "tipo_producto": "Producto",
            "presentacion_nombre": "Presentación",
            "cantidad_kg": "Kg",
            "unidades_solicitadas": "Unidades",
            "fecha_entrega": "Fecha entrega",
            "observaciones": "Observaciones",
        }).sort_values(["Producto", "Cliente"])
        st.dataframe(detalle_df, use_container_width=True, hide_index=True)

    st.write("")

    detalle_lista = [
        {
            "pedido_id": row["pedido_id"],
            "cliente": row["cliente_nombre"],
            "tipo_producto": row["tipo_producto"],
            "presentacion": row["presentacion_nombre"],
            "kg": row["cantidad_kg"],
            "unidades": int(row["unidades_solicitadas"]),
            "fecha_entrega": str(row["fecha_entrega"]),
            "observaciones": str(row.get("observaciones", "") or ""),
            "turno_nombre": mapa_turnos.get(str(row.get("turno", "") or "").strip(), "Sin asignar") if str(row.get("turno", "") or "").strip() else "Sin asignar",
        }
        for _, row in pedidos_fecha.iterrows()
    ]
    # ---- Notas del plan (editables desde la app, aparecen en el PDF) ----
    # Se guardan en la columna 'notas' de plan_mp_asignado del dia (todas las
    # filas del mismo dia comparten el mismo texto de notas).
    notas_prev = ""
    if not plan_hoy.empty and "notas" in plan_hoy.columns:
        for v in plan_hoy["notas"].fillna("").astype(str):
            if v.strip():
                notas_prev = v
                break

    st.markdown("##### 📝 Notas del plan")
    st.caption("Escribe cualquier observación o instrucción del día. Aparecerá en la última sección del PDF.")
    notas_input = st.text_area(
        "Notas", value=notas_prev, height=120, key=f"plan_notas_{fecha_sel.isoformat()}",
        label_visibility="collapsed",
    )
    if notas_input != notas_prev:
        if st.button("💾 Guardar notas", key=f"btn_notas_{fecha_sel.isoformat()}"):
            if plan_hoy.empty:
                st.error("Primero guarda una asignación de lotes para poder asociar las notas al plan del día.")
            else:
                for _, row in plan_hoy.iterrows():
                    db.update_row("plan_mp_asignado", "plan_mp_id", row["plan_mp_id"], {"notas": notas_input})
                st.success("✅ Notas guardadas.")
                st.rerun()
    st.write("")

    pdf_bytes = _generar_pdf(
        fecha_sel, consolidado, detalle_lista,
        cubetas_necesarias_total, cubetas_disponibles, alerta_mp,
        notas=notas_input, turnos_resumen=turnos_resumen,
    )
    st.download_button(
        "📄 Descargar plan del día (PDF)",
        data=pdf_bytes,
        file_name=f"plan_produccion_{fecha_sel.isoformat()}.pdf",
        mime="application/pdf",
        use_container_width=True,
        type="primary",
    )


def _render_historial(db, username, rol):
    """Historial de planes de produccion (agrupado por fecha) con opcion
    de eliminacion. Un 'plan' es el conjunto de filas de plan_mp_asignado
    con la misma fecha, mas los pedidos con esa fecha_produccion."""
    from utils.bitacora import log_cambio

    st.caption(
        "Aquí ves los planes de producción por fecha, con la cantidad de "
        "asignaciones de materia prima y pedidos vinculados. Puedes eliminar "
        "un plan para limpiar la lista o si fue un error de planificación — "
        "los pedidos de esa fecha volverán a estar sin planificar."
    )

    plan_mp = db.get_df("plan_mp_asignado")
    pedidos = db.get_df("pedidos")

    if plan_mp.empty:
        st.info("No hay planes de producción registrados todavía.")
        return

    plan_mp = plan_mp.copy()
    plan_mp["cubetas_asignadas"] = pd.to_numeric(plan_mp["cubetas_asignadas"], errors="coerce").fillna(0)

    # Filtros
    c1, c2 = st.columns(2)
    hoy = datetime.date.today()
    desde = c1.date_input(
        "Desde", value=hoy - datetime.timedelta(days=30), key="hp_desde",
    )
    hasta = c2.date_input("Hasta", value=hoy, key="hp_hasta")

    plan_f = plan_mp[
        (plan_mp["fecha"].astype(str) >= desde.isoformat())
        & (plan_mp["fecha"].astype(str) <= hasta.isoformat())
    ]

    if plan_f.empty:
        st.info("No hay planes en ese rango de fechas.")
        return

    # Resumen por fecha
    resumen = plan_f.groupby("fecha").agg(
        asignaciones_mp=("plan_mp_id", "count"),
        cubetas_planificadas=("cubetas_asignadas", "sum"),
    ).reset_index()

    # Contar pedidos por fecha
    if not pedidos.empty and "fecha_produccion" in pedidos.columns:
        pedidos_c = pedidos.copy()
        pedidos_c["producido_bool"] = pedidos_c["producido"].astype(str).str.upper().isin(["TRUE", "1", "SI", "SÍ"])
        agrupa_ped = pedidos_c[
            pedidos_c["fecha_produccion"].astype(str).isin(resumen["fecha"].astype(str))
        ].groupby("fecha_produccion").agg(
            pedidos_planificados=("pedido_id", "count"),
            pedidos_producidos=("producido_bool", "sum"),
        ).reset_index().rename(columns={"fecha_produccion": "fecha"})
        resumen = resumen.merge(agrupa_ped, on="fecha", how="left")
    resumen["pedidos_planificados"] = resumen.get("pedidos_planificados", 0).fillna(0).astype(int)
    resumen["pedidos_producidos"] = resumen.get("pedidos_producidos", 0).fillna(0).astype(int)
    resumen = resumen.sort_values("fecha", ascending=False)

    st.dataframe(
        resumen.rename(columns={
            "fecha": "Fecha", "asignaciones_mp": "N° asignaciones MP",
            "cubetas_planificadas": "Cubetas planificadas",
            "pedidos_planificados": "Pedidos planificados",
            "pedidos_producidos": "Pedidos ya producidos",
        }),
        use_container_width=True, hide_index=True,
    )
    c1, c2 = st.columns(2)
    c1.metric("Planes en el rango", len(resumen))
    c2.metric("Total cubetas planificadas", f"{int(resumen['cubetas_planificadas'].sum()):,}")

    st.divider()
    st.markdown("### 🗑️ Eliminar plan de un día")
    st.caption(
        "**Bloqueado si hay pedidos de esa fecha ya producidos** (para no "
        "romper la trazabilidad). Si necesitas revertir un plan ya ejecutado, "
        "primero revierte las producciones asociadas."
    )

    fechas_disponibles = resumen["fecha"].tolist()
    fecha_elim = st.selectbox(
        "Plan a eliminar (por fecha)",
        fechas_disponibles,
        format_func=lambda f: (
            f"{f} — {int(resumen.set_index('fecha').loc[f, 'asignaciones_mp'])} asig., "
            f"{int(resumen.set_index('fecha').loc[f, 'cubetas_planificadas'])} cub., "
            f"{int(resumen.set_index('fecha').loc[f, 'pedidos_planificados'])} ped."
        ),
        key="hp_fecha_elim",
    )
    producidos = int(resumen.set_index("fecha").loc[fecha_elim, "pedidos_producidos"])
    if producidos > 0:
        st.error(
            f"🔒 Este plan tiene {producidos} pedido(s) ya producido(s). "
            f"No se puede eliminar mientras haya producciones asociadas."
        )
    else:
        with st.form(f"form_elim_plan_{fecha_elim}"):
            confirm = st.text_input(
                f"Escribe **{fecha_elim}** para confirmar", key="hp_confirm",
            )
            motivo = st.text_area("Motivo de la eliminación (obligatorio)", "", key="hp_motivo")
            submitted = st.form_submit_button("🗑️ Eliminar plan del día", type="secondary")
            if submitted:
                if confirm.strip() != str(fecha_elim):
                    st.error(f"La confirmación no coincide. Escribe exactamente: {fecha_elim}")
                elif not motivo.strip():
                    st.error("Escribe el motivo de la eliminación.")
                else:
                    # Eliminar filas del plan_mp_asignado
                    n_elim = 0
                    for _, fila in plan_f[plan_f["fecha"].astype(str) == str(fecha_elim)].iterrows():
                        if db.delete_row("plan_mp_asignado", "plan_mp_id", fila["plan_mp_id"]):
                            n_elim += 1
                    # Limpiar fecha_produccion de los pedidos vinculados
                    n_ped = 0
                    if not pedidos.empty and "fecha_produccion" in pedidos.columns:
                        pedidos_vinc = pedidos[pedidos["fecha_produccion"].astype(str) == str(fecha_elim)]
                        for _, pv in pedidos_vinc.iterrows():
                            db.update_row("pedidos", "pedido_id", pv["pedido_id"], {"fecha_produccion": ""})
                            n_ped += 1
                    # Bitacora
                    log_cambio(
                        db, username,
                        modulo="Plan de produccion", tabla="plan_mp_asignado",
                        id_registro=str(fecha_elim), accion="eliminacion",
                        motivo=(
                            f"Plan del dia {fecha_elim}: {n_elim} asignaciones eliminadas, "
                            f"{n_ped} pedidos volvieron a sin planificar. Motivo: {motivo.strip()}"
                        ),
                    )
                    st.success(
                        f"✅ Plan del {fecha_elim} eliminado — {n_elim} asignaciones borradas "
                        f"y {n_ped} pedidos volvieron a estar sin planificar."
                    )
                    st.rerun()
