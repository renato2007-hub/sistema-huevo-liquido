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

    # -------- PDF --------
    st.divider()
    st.markdown("### 📄 Descargar orden de salida (PDF)")
    if st.button("📄 Generar PDF", type="primary", use_container_width=True):
        # Recargar asignaciones actualizadas
        asignaciones_df = db.get_df("orden_salida_asignaciones")
        asig_fecha = asignaciones_df[
            asignaciones_df["fecha_entrega"].astype(str) == fecha_sel.isoformat()
        ].copy()
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
        pdf_bytes = _generar_pdf(fecha_sel, datos_pdf)
        st.download_button(
            "⬇️ Descargar PDF",
            data=pdf_bytes,
            file_name=f"orden_salida_{fecha_sel.isoformat()}.pdf",
            mime="application/pdf",
            use_container_width=True,
        )


def _generar_pdf(fecha, datos_por_cliente):
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

    doc.build(el)
    buffer.seek(0)
    return buffer.getvalue()
