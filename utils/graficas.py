"""
Piezas compartidas para las graficas de los historiales de bodega (materia
prima y envases/insumos): series por periodo, estilo comun de las graficas
de barras y el cuadro compacto de analisis estadistico que va debajo.

Colores: paleta categorica validada (daltonismo y contraste). Cada entidad
lleva siempre el mismo color en todas las graficas de un modulo.
"""
import pandas as pd
import streamlit as st

GRANULARIDAD = {"Día": "D", "Semana": "W-SUN", "Mes": "M"}

AZUL = "#2a78d6"
NARANJA = "#eb6834"
AQUA = "#1baf7a"
AMARILLO = "#eda100"
MAGENTA = "#e87ba4"
VERDE = "#008300"
VIOLETA = "#6250d6"
ROJO = "#e34948"
# Polos para graficas de signo (lo que suma / lo que resta)
COLOR_POSITIVO = AZUL
COLOR_NEGATIVO = ROJO


def fmt(valor):
    """Numero con separador de miles y hasta 2 decimales, sin ceros de mas
    (30 -> '30', 2.5 -> '2.5', 1234.567 -> '1,234.57')."""
    texto = f"{float(valor):,.2f}"
    return texto.rstrip("0").rstrip(".") if "." in texto else texto


def selector_granularidad(desde, hasta, key):
    """Radio Dia/Semana/Mes; sugiere uno segun el largo del periodo."""
    dias = (hasta - desde).days + 1
    sugerida = "Día" if dias <= 45 else ("Semana" if dias <= 200 else "Mes")
    nombre = st.radio(
        "Agrupar por", list(GRANULARIDAD), index=list(GRANULARIDAD).index(sugerida),
        horizontal=True, key=key,
    )
    return nombre, GRANULARIDAD[nombre]


def dias_produccion(db, desde, hasta, extra=()):
    """Dias del periodo en que hubo produccion: las fechas de la hoja de
    produccion de semielaborados (mas las fechas 'extra' que se pasen). La
    planta no trabaja todos los dias, asi que los promedios diarios se
    calculan solo sobre estos dias."""
    dias = {f for f in extra if desde <= f <= hasta}
    prod = db.get_df("produccion_semielaborados")
    if not prod.empty and "fecha" in prod.columns:
        fechas = pd.to_datetime(prod["fecha"], errors="coerce").dropna().dt.date
        dias |= {f for f in fechas if desde <= f <= hasta}
    return dias


def serie_por_periodo(df, columna, desde, hasta, freq):
    """Suma 'columna' por periodo (dia/semana/mes) incluyendo en cero los
    periodos sin movimiento. df necesita una columna 'fecha' datetime."""
    rango = pd.period_range(pd.Timestamp(desde), pd.Timestamp(hasta), freq=freq)
    if df.empty:
        return pd.Series(0.0, index=rango)
    sumas = df.groupby(df["fecha"].dt.to_period(freq))[columna].sum()
    return sumas.reindex(rango, fill_value=0.0).astype(float)


def etiqueta_periodo(periodo, freq):
    if freq == "D":
        return periodo.start_time.strftime("%d/%m")
    if freq.startswith("W"):
        return "Sem " + periodo.start_time.strftime("%d/%m")
    meses = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
    return f"{meses[periodo.start_time.month - 1]} {periodo.start_time.year}"


def layout_base(fig, alto=300, leyenda=False, eje="Cubetas"):
    fig.update_layout(
        height=alto, margin=dict(l=10, r=10, t=10, b=10),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        bargap=0.35, showlegend=leyenda,
        legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0),
        hoverlabel=dict(bgcolor="white", font_size=12),
        xaxis=dict(showgrid=False, linecolor="#d9d8d3"),
        yaxis=dict(gridcolor="rgba(128,128,128,0.15)", zerolinecolor="#b5b4ae", title=eje),
    )
    try:
        fig.update_traces(marker_cornerradius=4)  # extremo redondeado (plotly >= 5.19)
    except (ValueError, TypeError):
        pass
    return fig


def cuadro_html(celdas):
    """Cuadro compacto de estadisticas: celdas pequeñas que se acomodan solas
    en varias filas segun el ancho disponible, para que no se traslapen
    cuando la grafica esta en media pantalla."""
    items = "".join(
        f'<div style="flex:1 1 90px;min-width:90px;padding:4px 8px;">'
        f'<div style="font-size:11px;opacity:.65;line-height:1.2;">{etiqueta}</div>'
        f'<div style="font-size:13px;line-height:1.3;">{valor}</div></div>'
        for etiqueta, valor in celdas
    )
    st.markdown(
        '<div style="display:flex;flex-wrap:wrap;gap:2px;border:1px solid rgba(128,128,128,.25);'
        f'border-radius:8px;padding:4px;margin:-8px 0 16px 0;">{items}</div>',
        unsafe_allow_html=True,
    )


def cuadro_estadistico(serie, freq_nombre, etiquetas, n_dias_prod, unidad="cub."):
    """Analisis estadistico de una serie por periodo. Si se agrupa por dia,
    la serie ya viene solo con los dias de produccion (y los dias con
    movimiento), asi que promedio, variabilidad y tendencia no se
    distorsionan con los dias en que no se trabaja."""
    import numpy as np
    periodo = {"Día": "día", "Semana": "semana", "Mes": "mes"}[freq_nombre]
    total = float(serie.sum())
    n = len(serie)
    con_mov = int((serie > 0).sum())
    promedio = total / n if n else 0.0
    prom_dia_prod = total / n_dias_prod if n_dias_prod else None
    desv = float(serie.std(ddof=1)) if n > 1 else 0.0
    cv = (desv / promedio * 100) if promedio else 0.0
    if con_mov:
        i_max = int(np.argmax(serie.values))
        maximo = f"{fmt(serie.iloc[i_max])} ({etiquetas[i_max]})"
    else:
        maximo = "—"
    if n >= 3 and con_mov >= 3:
        pendiente = float(np.polyfit(np.arange(n), serie.values, 1)[0])
        if abs(pendiente) < 0.05 * max(promedio, 1e-9):
            tendencia = "→ estable"
        else:
            sufijo = "día prod." if periodo == "día" else periodo
            tendencia = f"{'↑ sube' if pendiente > 0 else '↓ baja'} {fmt(abs(pendiente))} {unidad}/{sufijo}"
    else:
        tendencia = "—"
    celdas = [
        ("Total", f"<b>{fmt(total)}</b> {unidad}"),
        ("Prom./día de producción",
         f"<b>{fmt(prom_dia_prod)}</b> {unidad} ({n_dias_prod} días)" if prom_dia_prod is not None else "—"),
    ]
    if periodo != "día":
        celdas.append((f"Promedio/{periodo}", f"{fmt(promedio)} {unidad}"))
    celdas += [
        ("Días con mov." if periodo == "día" else f"{periodo.capitalize()}s con mov.", f"{con_mov} de {n}"),
        ("Máximo", maximo),
        ("Variabilidad", f"±{fmt(desv)} (CV {cv:,.0f}%)"),
        ("Tendencia", tendencia),
    ]
    cuadro_html(celdas)


def grafica_serie(titulo, df, columna, color, desde, hasta, freq, freq_nombre, vacio, dias_prod,
                  unidad="cub.", eje="Cubetas"):
    """Grafica de barras de 'columna' por periodo + su cuadro estadistico."""
    import plotly.graph_objects as go
    st.markdown(f"##### {titulo}")
    serie = serie_por_periodo(df, columna, desde, hasta, freq)
    if freq == "D":
        # Solo dias de produccion y dias con movimiento: los dias en que la
        # planta no trabaja no se dibujan ni cuentan en el analisis.
        serie = serie[[(p.start_time.date() in dias_prod) or v > 0 for p, v in serie.items()]]
    etiquetas = [etiqueta_periodo(p, freq) for p in serie.index]
    if serie.sum() == 0:
        st.info(vacio)
        return
    fig = go.Figure(go.Bar(
        x=etiquetas, y=serie.values, marker_color=color,
        hovertemplate=f"%{{x}}<br><b>%{{y:,.2f}}</b> {unidad}<extra></extra>",
    ))
    # etiqueta solo en el valor más alto (no un número en cada barra)
    i_max = int(serie.values.argmax())
    fig.add_annotation(
        x=etiquetas[i_max], y=serie.iloc[i_max], text=fmt(serie.iloc[i_max]),
        showarrow=False, yshift=10, font=dict(size=11, color="#52514e"),
    )
    fig = layout_base(fig, eje=eje)
    fig.update_xaxes(type="category")
    st.plotly_chart(fig, use_container_width=True)
    cuadro_estadistico(serie, freq_nombre, etiquetas, len(dias_prod), unidad=unidad)


def barras_horizontales(nombres, valores, color, eje, hover, customdata=None, alto_min=220):
    """Ranking en barras horizontales (de menor a mayor, el mayor arriba)."""
    import plotly.graph_objects as go
    fig = go.Figure(go.Bar(
        x=valores, y=nombres, orientation="h", marker_color=color,
        customdata=customdata, text=[fmt(v) for v in valores], textposition="outside",
        textfont=dict(color="#52514e"), cliponaxis=False,
        hovertemplate=hover + "<extra></extra>",
    ))
    fig = layout_base(fig, alto=max(alto_min, 40 * len(nombres) + 60))
    fig.update_layout(
        xaxis=dict(title=eje, showgrid=True, gridcolor="rgba(128,128,128,0.15)"),
        yaxis=dict(title=None, gridcolor="rgba(0,0,0,0)", type="category"),
        margin=dict(l=10, r=50, t=10, b=10),
    )
    st.plotly_chart(fig, use_container_width=True)
