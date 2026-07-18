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

    # ── tabla principal de cargas (con fila TOTAL) ──
    el.append(Paragraph("Cargas del vehículo", ESTILOS["Heading3"]))
    filas = []
    total_unid = 0.0
    total_kg = 0.0
    for l in lineas:
        cant = float(l.get("cantidad", 0) or 0)
        kg = float(l.get("kg", 0) or 0)
        total_unid += cant
        total_kg += kg
        filas.append([
            l.get("cliente", "—"),
            l.get("lote_origen", "—") or "—",
            l.get("presentacion", "—"),
            _fmt_num(cant),
            _fmt_num(kg, 1),
            l.get("pedido_ref", "") or "—",
            l.get("observaciones", "") or "",
        ])
    # fila de total
    filas.append(["TOTAL", "", "", _fmt_num(total_unid), _fmt_num(total_kg, 1), "", ""])
    tabla_cargas = _tabla_encabezado(
        filas,
        ["Cliente", "Lote", "Presentación", "Unid.", "Kg", "Pedido", "Observaciones"],
        anchos_cm=[3.3, 2.0, 2.6, 1.3, 1.5, 1.7, 4.5],
    )
    # resaltar la fila TOTAL (ultima fila)
    tabla_cargas.setStyle(TableStyle([
        ("BACKGROUND", (0, len(filas)), (-1, len(filas)), colors.HexColor("#fff3e0")),
        ("FONTNAME", (0, len(filas)), (-1, len(filas)), "Helvetica-Bold"),
        ("SPAN", (0, len(filas)), (2, len(filas))),
    ]))
    el.append(tabla_cargas)
    el.append(Spacer(1, 0.4 * cm))

    # ── saldo remanente en cuarto frio (inventario completo despues de esta carga) ──
    el.append(Paragraph(
        "Inventario en cuarto frío después de esta carga",
        ESTILOS["Heading3"],
    ))
    el.append(Paragraph(
        "<i>Confirma este inventario <b>antes</b> de que el camión salga del cuarto frío. "
        "Si no cuadra con el conteo físico, no despaches — reporta la diferencia al supervisor.</i>",
        ESTILOS["Normal"],
    ))
    el.append(Spacer(1, 0.15 * cm))
    if saldo_rem:
        filas_sr = [
            [r.get("tipo_producto", "") or "—",
             r.get("lote_origen", "—") or "—",
             r.get("presentacion", "—"),
             _fmt_num(r.get("saldo_unidades", 0)),
             _fmt_num(r.get("saldo_kg", 0), 1)]
            for r in saldo_rem
        ]
        # fila de total del inventario
        tot_u = sum(float(r.get("saldo_unidades", 0) or 0) for r in saldo_rem)
        tot_k = sum(float(r.get("saldo_kg", 0) or 0) for r in saldo_rem)
        filas_sr.append(["TOTAL", "", "", _fmt_num(tot_u), _fmt_num(tot_k, 1)])
        tabla_sr = _tabla_encabezado(
            filas_sr,
            ["Producto", "Lote", "Presentación", "Saldo unid.", "Saldo kg"],
            anchos_cm=[2.8, 2.5, 5.0, 2.8, 3.8],
            color_encabezado="#1565c0",
        )
        tabla_sr.setStyle(TableStyle([
            ("BACKGROUND", (0, len(filas_sr)), (-1, len(filas_sr)), colors.HexColor("#e3f2fd")),
            ("FONTNAME", (0, len(filas_sr)), (-1, len(filas_sr)), "Helvetica-Bold"),
            ("SPAN", (0, len(filas_sr)), (2, len(filas_sr))),
        ]))
        el.append(tabla_sr)
    else:
        el.append(Paragraph(
            "El cuarto frío quedó vacío después de esta carga.",
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
