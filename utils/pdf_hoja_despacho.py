"""
Genera la hoja de despacho en PDF que firma el despachador (y opcionalmente
el conductor) antes de que el camión salga del cuarto frio. Incluye las
cantidades exactas que se cargaron y el saldo que queda en cuarto frio por
lote y presentacion, para que se confirmen antes de despachar y se
minimicen los errores de despachar menos o mas de lo que corresponde.
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
_CELDA = ESTILOS["Normal"].clone("hd_celda")
_CELDA.fontSize = 9
_CELDA.leading = 11
_CELDA_B = ESTILOS["Normal"].clone("hd_celda_b")
_CELDA_B.fontSize = 9
_CELDA_B.leading = 11
_CELDA_B.fontName = "Helvetica-Bold"
_TITULO = ESTILOS["Title"].clone("hd_titulo")
_TITULO.fontSize = 18


def _p(texto, negrita=False):
    estilo = _CELDA_B if negrita else _CELDA
    return Paragraph(str(texto) if texto is not None else "", estilo)


def _tabla_pares(filas, ancho_etiq=5.0, ancho_valor=11.5):
    datos = [[_p(a, negrita=True), _p(b)] for a, b in filas]
    t = Table(datos, colWidths=[ancho_etiq * cm, ancho_valor * cm])
    t.setStyle(TableStyle([
        ("BOTTOMPADDING", (0, 0), (-1, -1), 3),
        ("TOPPADDING", (0, 0), (-1, -1), 3),
        ("LINEBELOW", (0, 0), (-1, -1), 0.3, colors.HexColor("#dddddd")),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return t


def _tabla_encabezado(filas, encabezados, anchos_cm=None, color_encabezado="#d9690e"):
    datos = [[_p(h, negrita=True) for h in encabezados]] + [
        [_p(c) for c in fila] for fila in filas
    ]
    kwargs = {"repeatRows": 1}
    if anchos_cm:
        kwargs["colWidths"] = [a * cm for a in anchos_cm]
    t = Table(datos, **kwargs)
    t.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(color_encabezado)),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 0), (-1, -1), 4),
        ("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#dddddd")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#f5f5f5")]),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    return t


def _fmt_num(v, decimales=0):
    try:
        n = float(v)
    except (TypeError, ValueError):
        return "0"
    if decimales == 0:
        return f"{int(round(n))}"
    return f"{n:,.{decimales}f}"


def generar_pdf_hoja_despacho(datos: dict) -> bytes:
    """
    datos: {
        "fecha": "2026-07-17",
        "vehiculo": "PBX-1234 — Camión Isuzu",
        "despachadores": ["Juan Pérez", ...],
        "lineas": [  # cada linea es un despacho ya enriquecido
            {
                "cliente": "Panadería Central",
                "lote_origen": "SR170726",
                "presentacion": "Funda 2 kg",
                "cantidad": 20, "kg": 40.0,
                "pedido_ref": "PED-001",
                "observaciones": "Entregar antes de las 10",
            },
            ...
        ],
        "saldo_remanente": [  # que queda en cuarto frio despues de esta hoja
            {"lote_origen": "SR170726", "presentacion": "Funda 2 kg",
             "saldo_unidades": 40, "saldo_kg": 80.0},
            ...
        ],
    }
    """
    buffer = io.BytesIO()
    doc = SimpleDocTemplate(
        buffer, pagesize=letter,
        topMargin=1.5 * cm, bottomMargin=1.5 * cm, leftMargin=1.6 * cm, rightMargin=1.6 * cm,
    )
    el = []

    fecha = datos.get("fecha", "")
    vehiculo = datos.get("vehiculo", "—")
    despachadores = datos.get("despachadores") or []
    lineas = datos.get("lineas") or []
    saldo_rem = datos.get("saldo_remanente") or []

    el.append(Paragraph("Hoja de Despacho", _TITULO))
    el.append(_tabla_pares([
        ("Fecha", fecha),
        ("Vehículo", vehiculo),
        ("Despachador(es)", ", ".join(despachadores) if despachadores else "—"),
        ("Generado", datetime.datetime.now().strftime("%d/%m/%Y %H:%M")),
    ]))
    el.append(Spacer(1, 0.4 * cm))

    if not lineas:
        el.append(Paragraph("No hay despachos registrados para esta selección.", ESTILOS["Normal"]))
        doc.build(el)
        buffer.seek(0)
        return buffer.getvalue()

    # ── tabla principal de cargas ──
    el.append(Paragraph("Cargas del vehículo", ESTILOS["Heading3"]))
    filas = []
    for l in lineas:
        filas.append([
            l.get("cliente", "—"),
            l.get("lote_origen", "—") or "—",
            l.get("presentacion", "—"),
            _fmt_num(l.get("cantidad", 0)),
            _fmt_num(l.get("kg", 0), 1),
            l.get("pedido_ref", "") or "—",
            l.get("observaciones", "") or "",
        ])
    el.append(_tabla_encabezado(
        filas,
        ["Cliente", "Lote", "Presentación", "Unid.", "Kg", "Pedido", "Observaciones"],
        anchos_cm=[3.3, 2.0, 2.6, 1.3, 1.5, 1.7, 4.5],
    ))
    el.append(Spacer(1, 0.3 * cm))

    # ── subtotales por cliente ──
    subtotales = {}
    for l in lineas:
        c = l.get("cliente", "—")
        s = subtotales.setdefault(c, {"unid": 0.0, "kg": 0.0})
        s["unid"] += float(l.get("cantidad", 0) or 0)
        s["kg"] += float(l.get("kg", 0) or 0)
    filas_st = [[c, _fmt_num(v["unid"]), _fmt_num(v["kg"], 1)] for c, v in subtotales.items()]
    total_unid = sum(v["unid"] for v in subtotales.values())
    total_kg = sum(v["kg"] for v in subtotales.values())
    filas_st.append(["TOTAL", _fmt_num(total_unid), _fmt_num(total_kg, 1)])
    el.append(Paragraph("Subtotales por cliente", ESTILOS["Heading4"]))
    tabla_st = _tabla_encabezado(
        filas_st,
        ["Cliente", "Unidades", "Kg"],
        anchos_cm=[9.5, 3.5, 3.9],
    )
    # resaltar fila TOTAL
    tabla_st.setStyle(TableStyle([
        ("BACKGROUND", (0, len(filas_st)), (-1, len(filas_st)), colors.HexColor("#fff3e0")),
        ("FONTNAME", (0, len(filas_st)), (-1, len(filas_st)), "Helvetica-Bold"),
    ]))
    el.append(tabla_st)
    el.append(Spacer(1, 0.4 * cm))

    # ── saldo remanente en cuarto frio ──
    el.append(Paragraph(
        "Saldo remanente en cuarto frío (por lote y presentación)",
        ESTILOS["Heading3"],
    ))
    el.append(Paragraph(
        "<i>Confirma este saldo <b>antes</b> de que el camión salga del cuarto frío. "
        "Si no cuadra con lo físico, no despaches — reporta la diferencia al supervisor.</i>",
        ESTILOS["Normal"],
    ))
    el.append(Spacer(1, 0.15 * cm))
    if saldo_rem:
        filas_sr = [
            [r.get("lote_origen", "—") or "—",
             r.get("presentacion", "—"),
             _fmt_num(r.get("saldo_unidades", 0)),
             _fmt_num(r.get("saldo_kg", 0), 1)]
            for r in saldo_rem
        ]
        el.append(_tabla_encabezado(
            filas_sr,
            ["Lote", "Presentación", "Saldo unid.", "Saldo kg"],
            anchos_cm=[3.5, 6.5, 3.0, 3.9],
            color_encabezado="#1565c0",
        ))
    else:
        el.append(Paragraph(
            "No queda saldo en cuarto frío para los lotes despachados.",
            ESTILOS["Normal"],
        ))
    el.append(Spacer(1, 0.6 * cm))

    # ── firmas ──
    bloque_firmas = Table([
        [_p("Despachador (nombre y firma):", negrita=True),
         _p("Conductor (nombre y firma):", negrita=True)],
        [_p(" "), _p(" ")],
        [_p("_________________________________"),
         _p("_________________________________")],
    ], colWidths=[8.5 * cm, 8.5 * cm])
    bloque_firmas.setStyle(TableStyle([
        ("BOTTOMPADDING", (0, 0), (-1, -1), 4),
        ("TOPPADDING", (0, 1), (-1, 1), 22),
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
    ]))
    el.append(KeepTogether(bloque_firmas))

    doc.build(el)
    buffer.seek(0)
    return buffer.getvalue()
