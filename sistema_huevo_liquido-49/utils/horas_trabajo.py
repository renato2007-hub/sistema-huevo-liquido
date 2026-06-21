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
import datetime
import pandas as pd


def calcular_horas_sesion(hora_entrada: datetime.time, hora_salida: datetime.time, fecha: datetime.date):
    """
    Calcula las horas totales trabajadas y cuantas de esas horas caen dentro
    de la franja nocturna (19:00 a 05:00 del dia siguiente), a partir de la
    hora de entrada y salida. Maneja correctamente jornadas que cruzan la
    medianoche (ej. entrada 22:00, salida 06:00) y jornadas que empiezan de
    madrugada dentro de la franja nocturna de la noche anterior (ej. entrada
    02:00, salida 10:00).
    Devuelve (horas_totales, horas_nocturnas).
    """
    inicio = datetime.datetime.combine(fecha, hora_entrada)
    fin = datetime.datetime.combine(fecha, hora_salida)
    if fin <= inicio:
        fin += datetime.timedelta(days=1)  # la jornada cruzo la medianoche

    horas_totales = (fin - inicio).total_seconds() / 3600

    # dos ventanas nocturnas candidatas: la que empezo "ayer" 19:00 y la que
    # empieza "hoy" 19:00 -- cada una dura 10 horas (hasta las 05:00)
    horas_nocturnas = 0.0
    for offset in (-1, 0):
        noche_inicio = datetime.datetime.combine(fecha + datetime.timedelta(days=offset), datetime.time(19, 0))
        noche_fin = noche_inicio + datetime.timedelta(hours=10)
        solape_inicio = max(inicio, noche_inicio)
        solape_fin = min(fin, noche_fin)
        if solape_fin > solape_inicio:
            horas_nocturnas += (solape_fin - solape_inicio).total_seconds() / 3600

    return round(horas_totales, 2), round(horas_nocturnas, 2)


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
