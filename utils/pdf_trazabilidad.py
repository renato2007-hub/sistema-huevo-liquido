"""
Genera el PDF de trazabilidad a partir del arbol construido por
utils/trazabilidad.py. Usa reportlab (no requiere nada instalado en el
sistema operativo, solo la libreria de Python).
"""
import io
import datetime
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, KeepTogether,
)

ESTILOS = getSampleStyleSheet()
_ESTILO_CELDA = ESTILOS["Normal"].clone("celda_tabla")
_ESTILO_CELDA.fontSize = 9
_ESTILO_CELDA.leading = 11
_ESTILO_CELDA_NEGRITA = ESTILOS["Normal"].clone("celda_tabla_negrita")
_ESTILO_CELDA_NEGRITA.fontSize = 9
_ESTILO_CELDA_NEGRITA.leading = 11
_ESTILO_CELDA_NEGRITA.fontName = "Helvetica-Bold"


def _p(texto, negrita=False):
    """Envuelve texto en un Paragraph para que las tablas lo corten de línea
    correctamente -- una celda con texto plano (str) NO se ajusta sola al
    ancho de columna en reportlab y se monta sobre la celda vecina."""
    estilo = _ESTILO_CELDA_NEGRITA if negrita else _ESTILO_CELDA
    return Paragraph(str(texto), estilo)


def _tabla_pares(filas):
    """Tabla de 2 columnas tipo 'etiqueta: valor', sin encabezado."""
    datos = [[_p(a, negrita=True), _p(b)] for a, b in filas]
    t = Table(datos, colWidths=[6.5 * cm, 8.5 * cm])
    t.setStyle(TableStyle([
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.HexColor("#dddddd")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return t


def _tabla_encabezado(filas, encabezados):
    """Tabla con fila de encabezado en naranja."""
    datos = [[_p(h, negrita=True) for h in encabezados]] + [[_p(c) for c in fila] for fila in filas]
    t = Table(datos, repeatRows=1)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#d9690e")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#dddddd")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f5f5")]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return t


def generar_pdf_trazabilidad(arbol: list, tipo_lote: str, lote_id: str) -> bytes:
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=letter,
        topMargin=1.8 * cm, bottomMargin=1.8 * cm, leftMargin=1.8 * cm, rightMargin=1.8 * cm,
    )
    el = []
    el.append(Paragraph("Informe de trazabilidad", ESTILOS["Title"]))
    etiquetas_tipo = {
        "recepcion": "Recepción de materia prima",
        "semielaborado": "Lote semielaborado",
        "producto": "Lote de producto terminado",
    }
    el.append(Paragraph(
        f"Consulta iniciada desde: <b>{lote_id}</b> ({etiquetas_tipo.get(tipo_lote, tipo_lote)})",
        ESTILOS["Normal"],
    ))
    el.append(Paragraph(
        f"Generado: {datetime.datetime.now().strftime('%d/%m/%Y %H:%M')}",
        ESTILOS["Normal"],
    ))
    el.append(Spacer(1, 0.6 * cm))

    if not arbol:
        el.append(Paragraph(
            "No se encontró información de trazabilidad para este lote. "
            "Verifica que el código esté escrito correctamente.",
            ESTILOS["Normal"],
        ))
        doc.build(el)
        buffer.seek(0)
        return buffer.getvalue()

    for nodo_rec in arbol:
        bloque = []
        bloque.append(Paragraph(f"RECEPCIÓN DE MATERIA PRIMA: {nodo_rec['recepcion_id']}", ESTILOS["Heading2"]))
        bloque.append(_tabla_pares([
            ("Origen", nodo_rec["origen"]),
            ("Fecha de recepción", nodo_rec["fecha"]),
            ("Cubetas recibidas", nodo_rec["cubetas"]),
            ("Categoría de huevo", nodo_rec["categoria"]),
        ]))
        el.append(KeepTogether(bloque))
        el.append(Spacer(1, 0.3 * cm))

        if not nodo_rec["producciones"]:
            el.append(Paragraph("Sin producciones derivadas registradas todavía para este lote.", ESTILOS["Normal"]))

        for nodo_prod in nodo_rec["producciones"]:
            bloque = []
            bloque.append(Paragraph(
                f"Producción derivada: {nodo_prod['lote_id']} — {nodo_prod['tipo_producto']}",
                ESTILOS["Heading3"],
            ))
            cubetas_mostrar = nodo_prod["cubetas_de_este_lote"]
            if isinstance(cubetas_mostrar, (int, float)) and cubetas_mostrar == 0 and nodo_prod.get("lote_hermano"):
                cubetas_texto = f"Comparte el consumo de huevo con el lote {nodo_prod['lote_hermano']} (mismo quiebre)"
            else:
                cubetas_texto = f"{cubetas_mostrar:.2f}" if isinstance(cubetas_mostrar, float) else cubetas_mostrar
            bloque.append(_tabla_pares([
                ("Fecha de producción", nodo_prod["fecha"]),
                ("Turno", nodo_prod.get("turno", "—")),
                ("Orden de producción", nodo_prod["orden_produccion"]),
                ("Cubetas tomadas de este lote", cubetas_texto),
                ("Kg real obtenido", nodo_prod["kg_real"]),
            ]))
            el.append(KeepTogether(bloque))
            el.append(Spacer(1, 0.2 * cm))

            personal_lista = nodo_prod.get("personal", [])
            if personal_lista:
                el.append(Paragraph("Personal a cargo de este lote:", ESTILOS["Normal"]))
                el.append(_tabla_encabezado(
                    [[p["nombre"], f"{p['horas']:.1f} h"] for p in personal_lista],
                    ["Nombre", "Horas trabajadas"],
                ))
                el.append(Spacer(1, 0.2 * cm))

            if nodo_prod["saneamiento"]:
                el.append(Paragraph("Saneamiento registrado ese mismo día (contexto, no exclusivo de este lote):", ESTILOS["Normal"]))
                el.append(_tabla_encabezado(
                    [[s["area"], s["tipo"], s.get("turno", "—"), "Sí" if s["verificado"] else "No"] for s in nodo_prod["saneamiento"]],
                    ["Área", "Tipo", "Turno", "Verificado"],
                ))
                el.append(Spacer(1, 0.2 * cm))

            if not nodo_prod["pasteurizaciones"]:
                el.append(Paragraph("Sin pasteurización/envasado registrado todavía para este lote.", ESTILOS["Normal"]))
            else:
                bal = nodo_prod.get("balance_distribucion", {})
                diff_llenado = bal.get("kg_pasteurizado", 0) - bal.get("kg_empacado", 0)
                pct_diff_llenado = (
                    abs(diff_llenado) / bal["kg_pasteurizado"] * 100 if bal.get("kg_pasteurizado", 0) > 0 else 0
                )
                diff_distribucion = bal.get("kg_empacado", 0) - bal.get("kg_contabilizado", 0)
                pct_diff_distribucion = (
                    abs(diff_distribucion) / bal["kg_empacado"] * 100 if bal.get("kg_empacado", 0) > 0 else 0
                )
                el.append(Paragraph("Balance de masa de distribución (pasteurizado → empacado → despachado/inventario):", ESTILOS["Normal"]))
                el.append(_tabla_pares([
                    ("Kg pasteurizados (todas las presentaciones)", f"{bal.get('kg_pasteurizado', 0):.2f}"),
                    ("Kg empacados (unidades × kg nominal)", f"{bal.get('kg_empacado', 0):.2f}"),
                    ("Diferencia en llenado (pasteurizado vs. empacado)", f"{diff_llenado:.2f} kg ({pct_diff_llenado:.1f}%)"),
                    ("Kg ya despachados a clientes", f"{bal.get('kg_despachado', 0):.2f}"),
                    ("Kg aún en cuarto frío", f"{bal.get('kg_en_cuarto_frio', 0):.2f}"),
                    ("Total contabilizado (despachado + cuarto frío)", f"{bal.get('kg_contabilizado', 0):.2f}"),
                    ("Diferencia en distribución (empacado vs. contabilizado)", f"{diff_distribucion:.2f} kg ({pct_diff_distribucion:.1f}%)"),
                ]))
                if pct_diff_llenado > 2:
                    el.append(Paragraph(
                        f"<b>⚠ Atención:</b> hay una diferencia de {pct_diff_llenado:.1f}% entre lo "
                        f"pasteurizado y lo realmente empacado — revisa si hubo merma de llenado sin "
                        f"registrar, o un error al digitar kg usado / unidades reales.",
                        ESTILOS["Normal"],
                    ))
                if pct_diff_distribucion > 2:
                    el.append(Paragraph(
                        f"<b>⚠ Atención:</b> hay una diferencia de {pct_diff_distribucion:.1f}% entre lo "
                        f"empacado y lo contabilizado (despachado + inventario). Revisa si falta registrar "
                        f"un despacho, un ingreso a cuarto frío, o producto dañado/desechado sin registrar.",
                        ESTILOS["Normal"],
                    ))
                el.append(Spacer(1, 0.2 * cm))

            for nodo_past in nodo_prod["pasteurizaciones"]:
                bloque2 = []
                bloque2.append(Paragraph(
                    f"Pasteurización/envasado: {nodo_past['lote_id']} — {nodo_past['presentacion']}",
                    ESTILOS["Normal"],
                ))
                bloque2.append(_tabla_pares([
                    ("Fecha", nodo_past["fecha"]),
                    ("Turno", nodo_past.get("turno", "—")),
                    ("Kg usado", nodo_past["kg_usado"]),
                    ("Unidades envasadas", nodo_past["unidades"]),
                ]))
                el.append(KeepTogether(bloque2))
                el.append(Spacer(1, 0.15 * cm))

                for nodo_entrada in nodo_past["entradas_cf"]:
                    el.append(Paragraph(f"Ingreso a cuarto frío: {nodo_entrada['entrada_id']} ({nodo_entrada['fecha']})", ESTILOS["Normal"]))
                    if nodo_entrada["despachos"]:
                        el.append(_tabla_encabezado(
                            [[d["fecha"], d["cliente"], d["vehiculo"], d["cantidad"]] for d in nodo_entrada["despachos"]],
                            ["Fecha despacho", "Cliente", "Vehículo", "Cantidad"],
                        ))
                    else:
                        el.append(Paragraph("Sin despachar todavía — sigue en inventario de cuarto frío.", ESTILOS["Normal"]))
                    el.append(Spacer(1, 0.15 * cm))

            el.append(Spacer(1, 0.3 * cm))

        el.append(Spacer(1, 0.5 * cm))

    doc.build(el)
    buffer.seek(0)
    return buffer.getvalue()
