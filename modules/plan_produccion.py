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

KG_POR_CUBETA_DEFAULT = 1.724  # kg líquido por cubeta si no hay categoría configurada


def _p(txt, negrita=False, pequeño=False):
    est = _ESTILO_B if negrita else (_ESTILO_T if pequeño else _ESTILO_N)
    return Paragraph(str(txt), est)


def _cubetas_necesarias(kg_liquido, kg_por_cubeta):
    if not kg_por_cubeta or kg_por_cubeta <= 0:
        kg_por_cubeta = KG_POR_CUBETA_DEFAULT
    return kg_liquido / kg_por_cubeta


def _generar_pdf(fecha, consolidado, detalle, cubetas_necesarias_total,
                 cubetas_disponibles, alerta_mp):
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
    datos = [[_p(h, negrita=True) for h in ["Producto", "Kg a producir", "Pedidos", "Clientes"]]]
    for row in consolidado:
        datos.append([
            _p(row["producto"]), _p(f"{row['kg']:.1f} kg"),
            _p(", ".join(row["pedidos"][:3]) + ("..." if len(row["pedidos"]) > 3 else "")),
            _p(", ".join(sorted(set(row["clientes"]))[:3])),
        ])
    datos.append([
        _p("TOTAL", negrita=True),
        _p(f"{sum(r['kg'] for r in consolidado):.1f} kg", negrita=True),
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

    mp_texto = (f"Cubetas necesarias: {cubetas_necesarias_total:.0f}  |  "
                f"Disponibles en bodega: {cubetas_disponibles:.0f}")
    el.append(Paragraph(mp_texto, ESTILOS["Normal"]))
    el.append(Spacer(1, 0.5*cm))

    el.append(Paragraph("Detalle por pedido", ESTILOS["Heading2"]))
    det = [[_p(h, negrita=True) for h in
            ["Pedido", "Cliente", "Producto", "Presentación", "Kg", "Unidades", "Entrega"]]]
    for row in detalle:
        det.append([
            _p(row["pedido_id"], pequeño=True), _p(row["cliente"], pequeño=True),
            _p(row["tipo_producto"], pequeño=True), _p(row["presentacion"], pequeño=True),
            _p(f"{row['kg']:.1f}", pequeño=True), _p(str(row["unidades"]), pequeño=True),
            _p(row["fecha_entrega"], pequeño=True),
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
    doc.build(el)
    buffer.seek(0)
    return buffer.getvalue()


def render(db, username, rol):
    st.title("📅 Plan de producción diario")

    pedidos_df    = db.get_df("pedidos")
    clientes_df   = db.get_df("clientes")
    presentac_df  = db.get_df("presentaciones")
    recepciones   = db.get_df("recepciones_mp")
    categorias    = db.get_df("categorias_huevo")
    cf_entradas   = db.get_df("cuarto_frio_entradas")
    pasteurizacion= db.get_df("pasteurizacion_envasado")
    produccion    = db.get_df("produccion_semielaborados")

    hoy = datetime.date.today()
    fecha_sel = st.date_input("📅 Fecha de producción a planificar", value=hoy)

    if pedidos_df.empty:
        st.info("No hay pedidos registrados todavía.")
        return

    pedidos_df["producido_bool"] = pedidos_df["producido"].astype(str).str.upper().isin(["TRUE","1","SI","SÍ"])
    pedidos_fecha = pedidos_df[
        (pedidos_df["fecha_produccion"].astype(str) == fecha_sel.isoformat()) &
        (~pedidos_df["producido_bool"])
    ].copy()

    if pedidos_fecha.empty:
        st.info(f"No hay pedidos con fecha de producción asignada para el {fecha_sel.strftime('%d/%m/%Y')}.")
        st.caption("Asigna fechas de producción en Recepción de pedidos → Todos los pedidos.")
        return

    pedidos_fecha["cantidad_kg"] = pd.to_numeric(pedidos_fecha["cantidad_kg"], errors="coerce").fillna(0)
    pedidos_fecha["unidades_solicitadas"] = pd.to_numeric(pedidos_fecha["unidades_solicitadas"], errors="coerce").fillna(0)

    # Resolver nombres de cliente y presentación
    mapa_cli = dict(zip(clientes_df["cliente_id"], clientes_df["nombre"])) if not clientes_df.empty else {}
    mapa_pres = dict(zip(presentac_df["presentacion_id"], presentac_df["nombre"])) if not presentac_df.empty else {}
    pedidos_fecha["cliente_nombre"] = pedidos_fecha["cliente_id"].map(mapa_cli).fillna(pedidos_fecha["cliente_id"])
    pedidos_fecha["presentacion_nombre"] = pedidos_fecha["presentacion_id"].map(mapa_pres).fillna(pedidos_fecha["presentacion_id"])

    # Calcular cubetas necesarias usando el rendimiento de la categoría
    kg_por_cubeta = KG_POR_CUBETA_DEFAULT
    if not categorias.empty and "kg_promedio_cubeta" in categorias.columns:
        vals = pd.to_numeric(categorias["kg_promedio_cubeta"], errors="coerce").dropna()
        if not vals.empty:
            kg_por_cubeta = float(vals.mean())
    rendimiento_liquido = 0.83  # pct promedio de kg líquido vs kg bruto
    kg_liquido_por_cubeta = kg_por_cubeta * rendimiento_liquido

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
    consolidado = []
    for tipo in pedidos_fecha["tipo_producto"].unique():
        grupo = pedidos_fecha[pedidos_fecha["tipo_producto"] == tipo]
        kg_total = grupo["cantidad_kg"].sum()
        consolidado.append({
            "producto": tipo,
            "kg": kg_total,
            "pedidos": list(grupo["pedido_id"]),
            "clientes": list(grupo["cliente_nombre"].unique()),
        })
    consolidado.sort(key=lambda r: ORDEN_PRODUCTOS.index(r["producto"]) if r["producto"] in ORDEN_PRODUCTOS else 99)

    kg_total_dia = sum(r["kg"] for r in consolidado)
    cubetas_necesarias_total = _cubetas_necesarias(kg_total_dia, kg_liquido_por_cubeta)
    alerta_mp = cubetas_necesarias_total > cubetas_disponibles

    # ── VISTA EN PANTALLA ──────────────────────────────────────────────────────
    st.markdown(f"### 📋 Plan del {fecha_sel.strftime('%d/%m/%Y')}")

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

    st.write("")

    # Detalle por pedido
    with st.expander("📋 Ver detalle de todos los pedidos de este día", expanded=True):
        detalle_df = pedidos_fecha[[
            "pedido_id", "cliente_nombre", "tipo_producto",
            "presentacion_nombre", "cantidad_kg", "unidades_solicitadas", "fecha_entrega",
        ]].rename(columns={
            "pedido_id": "Pedido",
            "cliente_nombre": "Cliente",
            "tipo_producto": "Producto",
            "presentacion_nombre": "Presentación",
            "cantidad_kg": "Kg",
            "unidades_solicitadas": "Unidades",
            "fecha_entrega": "Fecha entrega",
        }).sort_values(["Producto", "Cliente"])
        st.dataframe(detalle_df, use_container_width=True, hide_index=True)

    st.write("")

    # PDF
    detalle_lista = [
        {
            "pedido_id": row["pedido_id"],
            "cliente": row["cliente_nombre"],
            "tipo_producto": row["tipo_producto"],
            "presentacion": row["presentacion_nombre"],
            "kg": row["cantidad_kg"],
            "unidades": int(row["unidades_solicitadas"]),
            "fecha_entrega": str(row["fecha_entrega"]),
        }
        for _, row in pedidos_fecha.iterrows()
    ]
    pdf_bytes = _generar_pdf(
        fecha_sel, consolidado, detalle_lista,
        cubetas_necesarias_total, cubetas_disponibles, alerta_mp,
    )
    st.download_button(
        "📄 Descargar plan del día (PDF)",
        data=pdf_bytes,
        file_name=f"plan_produccion_{fecha_sel.isoformat()}.pdf",
        mime="application/pdf",
        use_container_width=True,
        type="primary",
    )
