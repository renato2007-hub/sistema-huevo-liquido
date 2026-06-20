"""
Clasificacion de horas trabajadas, segun la regla operativa que definio el
negocio (no es un calculo legal completo de nomina -- no contempla recargo
nocturno ni otras reglas del Codigo de Trabajo; para nomina real, validar
con un especialista laboral):

- Horas normales: en un dia NORMAL (no feriado), hasta 8 horas.
- Horas extras: en un dia NORMAL, lo que excede de 8 horas.
- Horas dobles: TODAS las horas trabajadas en un dia feriado SIN compensacion
  acordada (se le paga doble).
- Horas compensadas: TODAS las horas trabajadas en un dia feriado CON
  compensacion acordada (la persona descansa otro dia en su lugar, en vez de
  cobrar doble) -- registradas en la tabla 'compensaciones_feriado'.

Importante: la clasificacion se hace por PERSONA Y DIA, sumando primero
todas las horas que esa persona trabajo ese dia (pueden venir de varios
lotes/producciones), y recien despues aplicando el limite de 8 horas y la
revision de compensacion -- si se aplicara lote por lote se perderian las
horas extra reales del dia, o se podria marcar solo una parte del dia como
compensada quedando inconsistente.
"""
import pandas as pd


def clasificar_horas_por_dia(
    df, feriados_fechas: set, compensados: set,
    col_fecha="fecha", col_horas="horas", col_persona="personal_id",
):
    """
    df: dataframe YA agrupado a nivel (persona, fecha) -- es decir, una sola
    fila por persona y dia, con el total de horas de ese dia. Debe tener
    las columnas col_fecha, col_horas y col_persona.
    compensados: set de tuplas (fecha_texto, personal_id) que se acordaron
    como compensadas con descanso, en vez de pago doble.
    Devuelve el mismo df con 4 columnas nuevas: horas_normales, horas_extras,
    horas_dobles, horas_compensadas.
    """
    df = df.copy()
    es_feriado = df[col_fecha].astype(str).isin(feriados_fechas)
    es_compensado = df.apply(
        lambda r: (str(r[col_fecha]), r[col_persona]) in compensados, axis=1,
    )
    horas = pd.to_numeric(df[col_horas], errors="coerce").fillna(0)
    horas_base = horas.clip(upper=8)

    df["horas_normales"] = (~es_feriado) * horas_base
    df["horas_extras"] = (~es_feriado) * (horas - horas_base).clip(lower=0)
    df["horas_dobles"] = (es_feriado & ~es_compensado) * horas
    df["horas_compensadas"] = (es_feriado & es_compensado) * horas
    return df


def feriados_como_set(df_feriados) -> set:
    """Convierte la tabla 'feriados' en un set de fechas (texto) activas,
    listo para pasarle a clasificar_horas_por_dia."""
    if df_feriados.empty:
        return set()
    activos = df_feriados[df_feriados.get("activo", "TRUE").astype(str).str.upper() != "FALSE"]
    return set(activos["fecha"].astype(str))


def compensaciones_como_set(df_compensaciones) -> set:
    """Convierte la tabla 'compensaciones_feriado' en un set de tuplas
    (fecha, personal_id), listo para pasarle a clasificar_horas_por_dia."""
    if df_compensaciones.empty:
        return set()
    return set(zip(df_compensaciones["fecha"].astype(str), df_compensaciones["personal_id"]))
