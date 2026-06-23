"""
Genera el PDF de una orden de compra (solicitud de MP e insumos) lista para
enviar al correo de compras / proveedor.
"""
import io
import re
import datetime
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

ESTILOS = getSampleStyleSheet()
_ESTILO_CELDA = ESTILOS["Normal"].clone("celda_oc")
_ESTILO_CELDA.fontSize = 9
_ESTILO_CELDA.leading = 11
_ESTILO_CELDA_NEGRITA = ESTILOS["Normal"].clone("celda_oc_negrita")
_ESTILO_CELDA_NEGRITA.fontSize = 9
_ESTILO_CELDA_NEGRITA.leading = 11
_ESTILO_CELDA_NEGRITA.fontName = "Helvetica-Bold"


def _p(texto, negrita=False):
    estilo = _ESTILO_CELDA_NEGRITA if negrita else _ESTILO_CELDA
    return Paragraph(str(texto), estilo)


def _tabla_pares(filas):
    datos = [[_p(a, negrita=True), _p(b)] for a, b in filas]
    t = Table(datos, colWidths=[5.5 * cm, 9.5 * cm])
    t.setStyle(TableStyle([
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.HexColor("#dddddd")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return t


def _sin_emoji(texto):
    """Quita emojis del texto -- la fuente del PDF no los soporta y se ven
    como cuadros negros. En la app (HTML) se siguen viendo bien, esto solo
    afecta al documento."""
    return re.sub(r"[^\w\sÁÉÍÓÚÑáéíóúñ.,()/:-]", "", str(texto)).strip()


def generar_pdf_orden_compra(solicitud: dict, items: list) -> bytes:
    """
    solicitud: dict con numero_oc, fecha_solicitud, fecha_maxima_recepcion,
    proveedor_recomendado, observaciones.
    items: lista de dicts con categoria, nombre_item, cantidad, unidad.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=letter,
        topMargin=1.8 * cm, bottomMargin=1.8 * cm, leftMargin=1.8 * cm, rightMargin=1.8 * cm,
    )
    el = []
    el.append(Paragraph("Orden de compra", ESTILOS["Title"]))
    el.append(Spacer(1, 0.3 * cm))
    el.append(_tabla_pares([
        ("N° de OC", solicitud.get("numero_oc") or "—"),
        ("Proveedor recomendado", solicitud.get("proveedor_recomendado") or "—"),
        ("Fecha de solicitud", solicitud.get("fecha_solicitud", "")),
        ("Fecha máxima esperada de recepción", solicitud.get("fecha_maxima_recepcion", "")),
    ]))
    el.append(Spacer(1, 0.5 * cm))

    if not items:
        el.append(Paragraph("Sin ítems registrados en esta solicitud.", ESTILOS["Normal"]))
    else:
        encabezados = ["Categoría", "Ítem", "Cantidad", "Unidad", "Proveedor"]
        datos = [[_p(h, negrita=True) for h in encabezados]]
        for it in items:
            datos.append([
                _p(_sin_emoji(it.get("categoria", ""))), _p(it.get("nombre_item", "")),
                _p(it.get("cantidad", "")), _p(it.get("unidad", "")),
                _p(it.get("proveedor", "—") or "—"),
            ])
        tabla = Table(datos, repeatRows=1)
        tabla.setStyle(TableStyle([
            ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#2D6CA2")),
            ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
            ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
            ("TOPPADDING", (0, 0), (-1, -1), 5),
            ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#dddddd")),
            ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f5f5")]),
            ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ]))
        el.append(tabla)

    if solicitud.get("observaciones"):
        el.append(Spacer(1, 0.4 * cm))
        el.append(Paragraph(f"<b>Observaciones:</b> {solicitud['observaciones']}", ESTILOS["Normal"]))

    el.append(Spacer(1, 0.6 * cm))
    el.append(Paragraph(
        f"Generado: {datetime.datetime.now().strftime('%d/%m/%Y %H:%M')}",
        ESTILOS["Normal"],
    ))

    doc.build(el)
    buffer.seek(0)
    return buffer.getvalue()
