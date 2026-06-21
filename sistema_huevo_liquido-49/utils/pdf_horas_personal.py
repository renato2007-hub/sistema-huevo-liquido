"""
Genera el PDF del reporte de horas de personal para un periodo determinado.
Incluye a TODO el personal activo del catalogo -- incluso quienes no
trabajaron nada ese periodo (util sobre todo para personal ocasional, para
ver de un vistazo quien trabajo y quien no).
"""
import io
import datetime
from reportlab.lib import colors
from reportlab.lib.pagesizes import letter, landscape
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import cm
from reportlab.platypus import SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle

ESTILOS = getSampleStyleSheet()
_ESTILO_CELDA = ESTILOS["Normal"].clone("celda_horas")
_ESTILO_CELDA.fontSize = 8.5
_ESTILO_CELDA.leading = 10
_ESTILO_CELDA_NEGRITA = ESTILOS["Normal"].clone("celda_horas_negrita")
_ESTILO_CELDA_NEGRITA.fontSize = 8.5
_ESTILO_CELDA_NEGRITA.leading = 10
_ESTILO_CELDA_NEGRITA.fontName = "Helvetica-Bold"


def _p(texto, negrita=False):
    estilo = _ESTILO_CELDA_NEGRITA if negrita else _ESTILO_CELDA
    return Paragraph(str(texto), estilo)


def generar_pdf_horas_personal(filas: list, desde: datetime.date, hasta: datetime.date) -> bytes:
    """
    filas: lista de dicts, uno por persona, con claves: nombre, cargo,
    tipo_personal, trabajo (bool), horas_normales, horas_extras,
    horas_dobles, horas_compensadas, horas_nocturnas, horas_totales, costo.
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=landscape(letter),
        topMargin=1.5 * cm, bottomMargin=1.5 * cm, leftMargin=1.5 * cm, rightMargin=1.5 * cm,
    )
    el = []
    el.append(Paragraph("Reporte de horas de personal", ESTILOS["Title"]))
    el.append(Paragraph(
        f"Período: {desde.strftime('%d/%m/%Y')} al {hasta.strftime('%d/%m/%Y')}",
        ESTILOS["Normal"],
    ))
    el.append(Paragraph(
        f"Generado: {datetime.datetime.now().strftime('%d/%m/%Y %H:%M')}",
        ESTILOS["Normal"],
    ))
    el.append(Spacer(1, 0.5 * cm))

    encabezados = [
        "Nombre", "Cargo", "Tipo", "¿Trabajó?", "Normales", "Extras",
        "Dobles", "Compensadas", "Nocturnas", "Total h", "Costo",
    ]
    datos = [[_p(h, negrita=True) for h in encabezados]]
    for f in filas:
        datos.append([
            _p(f["nombre"]),
            _p(f.get("cargo", "")),
            _p(f.get("tipo_personal", "")),
            _p("Sí" if f["trabajo"] else "No"),
            _p(f"{f['horas_normales']:.1f}"),
            _p(f"{f['horas_extras']:.1f}"),
            _p(f"{f['horas_dobles']:.1f}"),
            _p(f"{f['horas_compensadas']:.1f}"),
            _p(f"{f.get('horas_nocturnas', 0):.1f}"),
            _p(f"{f['horas_totales']:.1f}"),
            _p(f"{f['costo']:,.2f}"),
        ])

    tabla = Table(datos, repeatRows=1)
    estilos_tabla = [
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#0B6E4F")),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#dddddd")),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f5f5")]),
    ]
    # resaltar en amarillo claro a quienes NO trabajaron, para que salten a la vista
    for i, f in enumerate(filas, start=1):
        if not f["trabajo"]:
            estilos_tabla.append(("BACKGROUND", (0, i), (-1, i), colors.HexColor("#FFF3CD")))
    tabla.setStyle(TableStyle(estilos_tabla))
    el.append(tabla)

    el.append(Spacer(1, 0.5 * cm))
    ocasionales_sin_trabajar = [f["nombre"] for f in filas if f.get("tipo_personal") == "Ocasional" and not f["trabajo"]]
    if ocasionales_sin_trabajar:
        el.append(Paragraph(
            f"<b>Personal ocasional que NO registró horas en este período:</b> "
            f"{', '.join(ocasionales_sin_trabajar)}",
            ESTILOS["Normal"],
        ))
    el.append(Paragraph(
        "Filas resaltadas en amarillo = la persona no registró horas en este período.",
        ESTILOS["Normal"],
    ))

    doc.build(el)
    buffer.seek(0)
    return buffer.getvalue()
