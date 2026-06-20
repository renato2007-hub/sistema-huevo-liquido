"""
Logica de costeo e inventario:
- FEFO: que lote(s) de huevo conviene tomar primero (vence antes primero).
- Costo promedio ponderado: cuando una produccion combina varios lotes de
  huevo con distinto costo, el costo del lote resultante es el promedio
  ponderado de lo que realmente se tomo de cada uno.
- Rendimiento teorico: segun la categoria/tamano de huevo, cuanto kg de
  huevo (y de clara/yema/cascara si aplica) se espera obtener.

El operador siempre puede editar la sugerencia de lotes antes de guardar
(sugerencia automatica + edicion manual, segun lo definido).
"""
import pandas as pd


def sugerir_lotes_fefo(df_recepciones: pd.DataFrame, categoria_id, cantidad_necesaria: float):
    """
    Devuelve una lista de dicts [{recepcion_id, cantidad_a_tomar, costo_cubeta,
    fecha_vencimiento}, ...] tomando primero los lotes con fecha de vencimiento
    mas proxima de la categoria indicada, hasta cubrir cantidad_necesaria.
    Si el inventario no alcanza, devuelve todo lo disponible (el llamador debe
    avisar que falta).
    """
    if df_recepciones.empty:
        return []

    disponibles = df_recepciones[
        (df_recepciones["categoria_id"].astype(str) == str(categoria_id))
        & (pd.to_numeric(df_recepciones["cubetas_saldo"], errors="coerce").fillna(0) > 0)
    ].copy()
    if disponibles.empty:
        return []

    disponibles["fecha_vencimiento"] = pd.to_datetime(disponibles["fecha_vencimiento"], errors="coerce")
    disponibles = disponibles.sort_values("fecha_vencimiento")

    sugerencia = []
    restante = cantidad_necesaria
    for _, lote in disponibles.iterrows():
        if restante <= 0:
            break
        saldo = float(lote["cubetas_saldo"])
        tomar = min(saldo, restante)
        sugerencia.append({
            "recepcion_id": lote["recepcion_id"],
            "cantidad_a_tomar": tomar,
            "costo_cubeta": float(lote["costo_cubeta"]),
            "fecha_vencimiento": lote["fecha_vencimiento"],
        })
        restante -= tomar
    return sugerencia


def costo_ponderado(detalle_lotes: list) -> float:
    """
    detalle_lotes: [{"cantidad_a_tomar": x, "costo_cubeta": y}, ...]
    Devuelve el costo promedio ponderado por cubeta de ese consumo combinado.
    """
    total_cantidad = sum(d["cantidad_a_tomar"] for d in detalle_lotes)
    if total_cantidad == 0:
        return 0.0
    total_costo = sum(d["cantidad_a_tomar"] * d["costo_cubeta"] for d in detalle_lotes)
    return total_costo / total_cantidad


PREFIJOS_LOTE_SEMIELABORADO = {
    "Huevo entero": "SR",
    "Clara": "R",
    "Yema": "TK",
}


def sugerir_codigo_lote(tipo_producto: str, fecha) -> str:
    """
    Sugiere un codigo de lote inicial siguiendo la convencion de planta:
    SR = huevo entero, R = clara, TK = yema, seguido de la fecha en formato
    DDMMAA (ej. SR190626 para huevo entero producido el 19/06/2026).
    Es solo una SUGERENCIA editable -- quien registra el dato decide el
    codigo final, puede cambiarlo libremente en el formulario.
    """
    prefijo = PREFIJOS_LOTE_SEMIELABORADO.get(tipo_producto, "SR")
    return f"{prefijo}{fecha.strftime('%d%m%y')}"


def rendimiento_teorico(cubetas: float, categoria) -> dict:
    """
    categoria: fila (Series) de categorias_huevo, con kg_promedio_cubeta,
    pct_clara, pct_yema, pct_cascara.

    kg_promedio_cubeta es el peso BRUTO del huevo entero (con cascara).
    Devuelve tanto el teorico bruto como el teorico de liquido (sin cascara),
    que es el que debe compararse contra lo realmente pesado al final del
    proceso (kg_real) -- comparar contra el bruto subestima el rendimiento,
    porque la cascara nunca se cuenta como liquido.
    """
    kg_total = cubetas * float(categoria["kg_promedio_cubeta"])
    clara_teorica_kg = kg_total * float(categoria["pct_clara"]) / 100
    yema_teorica_kg = kg_total * float(categoria["pct_yema"]) / 100
    cascara_teorica_kg = kg_total * float(categoria["pct_cascara"]) / 100
    return {
        "kg_teorico_bruto": kg_total,
        "kg_liquido_teorico": clara_teorica_kg + yema_teorica_kg,
        "clara_teorica_kg": clara_teorica_kg,
        "yema_teorica_kg": yema_teorica_kg,
        "cascara_teorica_kg": cascara_teorica_kg,
    }
