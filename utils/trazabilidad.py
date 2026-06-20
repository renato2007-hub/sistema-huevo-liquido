"""
Construye el arbol completo de trazabilidad de un lote, sin importar en que
etapa lo elijas (recepcion de huevo, lote semielaborado SR/R/TK, o lote de
producto terminado PROD) -- siempre encuentra la(s) recepcion(es) de huevo
de origen y arma el arbol completo HACIA ADELANTE desde ahi, hasta los
despachos a clientes.

No usa la base de datos directamente -- recibe los DataFrames ya cargados,
para no acoplarse a SheetsDB y poder probarse con datos de ejemplo.
"""
import re
import pandas as pd


def _extraer_lote_hermano(observaciones: str):
    """Busca 'co-producto junto con lote clara XXXX' o '... lote yema XXXX'
    en las observaciones (lo que se guarda cuando una produccion se separa
    en Clara y Yema). El codigo capturado excluye espacios y el parentesis
    de cierre."""
    m = re.search(r"co-producto junto con lote (?:clara|yema) ([^\s)]+)", str(observaciones))
    return m.group(1) if m else None


def _val(fila, columna, default=""):
    if fila is None or columna not in fila or pd.isna(fila[columna]):
        return default
    return fila[columna]


def _nombre_origen(recepcion_fila, galpones, proveedores):
    origen_tipo = _val(recepcion_fila, "origen_tipo")
    origen_id = _val(recepcion_fila, "origen_id")
    catalogo = galpones if origen_tipo == "Galpón propio" else proveedores
    col_id = "galpon_id" if origen_tipo == "Galpón propio" else "proveedor_id"
    if catalogo.empty or col_id not in catalogo.columns:
        return f"{origen_tipo} ({origen_id})"
    fila = catalogo[catalogo[col_id].astype(str) == str(origen_id)]
    if fila.empty:
        return f"{origen_tipo} ({origen_id})"
    return f"{origen_tipo}: {fila.iloc[0]['nombre']}"


def construir_arbol_trazabilidad(tablas: dict, tipo_lote: str, lote_id: str) -> list:
    """
    tablas: dict con los DataFrames ya cargados (claves: recepciones_mp,
    consumo_mp_produccion, produccion_semielaborados, limpieza_desinfeccion,
    areas_limpieza, pasteurizacion_envasado, presentaciones,
    cuarto_frio_entradas, cuarto_frio_salidas, clientes, vehiculos,
    galpones, proveedores, categorias_huevo).
    tipo_lote: "recepcion" | "semielaborado" | "producto"
    Devuelve una lista de nodos-recepcion (normalmente 1, pero un lote
    semielaborado puede haberse alimentado de varias recepciones).
    """
    recepciones_mp = tablas["recepciones_mp"]
    consumo_mp = tablas["consumo_mp_produccion"]
    produccion = tablas["produccion_semielaborados"]
    limpieza = tablas["limpieza_desinfeccion"]
    areas = tablas["areas_limpieza"]
    pasteurizacion = tablas["pasteurizacion_envasado"]
    presentaciones = tablas["presentaciones"]
    cf_entradas = tablas["cuarto_frio_entradas"]
    cf_salidas = tablas["cuarto_frio_salidas"]
    clientes = tablas["clientes"]
    vehiculos = tablas["vehiculos"]
    galpones = tablas["galpones"]
    proveedores = tablas["proveedores"]
    categorias = tablas["categorias_huevo"]

    def recepciones_de(lote_semi_id):
        if consumo_mp.empty:
            return []
        filas = consumo_mp[consumo_mp["lote_semielaborado_id"] == lote_semi_id]
        recs = list(filas["recepcion_id"].unique())
        if recs:
            return recs
        # puede ser un lote "hermano" (co-producto clara/yema) sin consumo propio
        if produccion.empty:
            return []
        fila_p = produccion[produccion["lote_semielaborado_id"] == lote_semi_id]
        if fila_p.empty:
            return []
        hermano = _extraer_lote_hermano(fila_p.iloc[0].get("observaciones", ""))
        if hermano and not consumo_mp.empty:
            return list(consumo_mp[consumo_mp["lote_semielaborado_id"] == hermano]["recepcion_id"].unique())
        return []

    # 1. determinar la(s) recepcion(es) raiz segun donde empezo la consulta
    if tipo_lote == "recepcion":
        recepciones_raiz = [lote_id]
    elif tipo_lote == "semielaborado":
        recepciones_raiz = recepciones_de(lote_id)
    elif tipo_lote == "producto":
        if pasteurizacion.empty:
            recepciones_raiz = []
        else:
            fila_p = pasteurizacion[pasteurizacion["lote_producto_id"] == lote_id]
            recepciones_raiz = recepciones_de(fila_p.iloc[0]["lote_semielaborado_id"]) if not fila_p.empty else []
    else:
        recepciones_raiz = []

    arbol = []
    for recepcion_id in recepciones_raiz:
        fila_rec = recepciones_mp[recepciones_mp["recepcion_id"] == recepcion_id]
        if fila_rec.empty:
            continue
        fila_rec = fila_rec.iloc[0]
        categoria_nombre = fila_rec.get("categoria_id", "")
        if not categorias.empty:
            fc = categorias[categorias["categoria_id"] == fila_rec.get("categoria_id")]
            if not fc.empty:
                categoria_nombre = fc.iloc[0]["nombre"]

        nodo_rec = {
            "recepcion_id": recepcion_id,
            "origen": _nombre_origen(fila_rec, galpones, proveedores),
            "fecha": str(_val(fila_rec, "fecha")),
            "cubetas": _val(fila_rec, "cubetas", 0),
            "categoria": categoria_nombre,
            "costo_cubeta": _val(fila_rec, "costo_cubeta", 0),
            "producciones": [],
        }

        lotes_semi = set()
        if not consumo_mp.empty:
            lotes_semi.update(
                consumo_mp[consumo_mp["recepcion_id"] == recepcion_id]["lote_semielaborado_id"].unique()
            )
        # agregar lotes hermanos (clara/yema) que no tienen consumo propio
        for lid in list(lotes_semi):
            if produccion.empty:
                continue
            fp = produccion[produccion["lote_semielaborado_id"] == lid]
            if not fp.empty:
                hermano = _extraer_lote_hermano(fp.iloc[0].get("observaciones", ""))
                if hermano:
                    lotes_semi.add(hermano)

        for lid in sorted(lotes_semi):
            fp = produccion[produccion["lote_semielaborado_id"] == lid] if not produccion.empty else pd.DataFrame()
            if fp.empty:
                continue
            fp = fp.iloc[0]

            cubetas_de_este_lote = 0
            lote_hermano = None
            if not consumo_mp.empty:
                rel = consumo_mp[
                    (consumo_mp["lote_semielaborado_id"] == lid) & (consumo_mp["recepcion_id"] == recepcion_id)
                ]
                if not rel.empty:
                    cubetas_de_este_lote = pd.to_numeric(rel["cubetas_usadas"], errors="coerce").fillna(0).sum()
                else:
                    lote_hermano = _extraer_lote_hermano(fp.get("observaciones", ""))

            saneamiento = []
            if not limpieza.empty:
                mismo_dia = limpieza[limpieza["fecha"].astype(str) == str(_val(fp, "fecha"))]
                for _, sfila in mismo_dia.iterrows():
                    area_nombre = sfila.get("area_id", "")
                    if not areas.empty:
                        fa = areas[areas["area_id"] == sfila.get("area_id")]
                        if not fa.empty:
                            area_nombre = fa.iloc[0]["nombre"]
                    saneamiento.append({
                        "area": area_nombre,
                        "tipo": _val(sfila, "tipo_limpieza"),
                        "verificado": str(_val(sfila, "verificado")).upper() == "TRUE",
                    })

            nodo_prod = {
                "lote_id": lid,
                "tipo_producto": _val(fp, "tipo_producto"),
                "fecha": str(_val(fp, "fecha")),
                "orden_produccion": _val(fp, "orden_produccion"),
                "cubetas_de_este_lote": cubetas_de_este_lote,
                "lote_hermano": lote_hermano,
                "kg_real": _val(fp, "kg_real", 0),
                "costo_unitario_kg": _val(fp, "costo_unitario_kg", 0),
                "saneamiento": saneamiento,
                "pasteurizaciones": [],
            }

            lotes_past = pasteurizacion[pasteurizacion["lote_semielaborado_id"] == lid] if not pasteurizacion.empty else pd.DataFrame()
            for _, fpast in lotes_past.iterrows():
                pres_nombre = fpast.get("presentacion_id", "")
                kg_nominal = 0.0
                if not presentaciones.empty:
                    fpr = presentaciones[presentaciones["presentacion_id"] == fpast.get("presentacion_id")]
                    if not fpr.empty:
                        pres_nombre = fpr.iloc[0]["nombre"]
                        kg_nominal = float(pd.to_numeric(fpr.iloc[0].get("kg_nominal", 0), errors="coerce") or 0)

                unidades_reales = float(pd.to_numeric(_val(fpast, "unidades_reales", 0), errors="coerce") or 0)
                kg_usado_val = float(pd.to_numeric(_val(fpast, "kg_usado", 0), errors="coerce") or 0)

                nodo_past = {
                    "lote_id": fpast["lote_producto_id"],
                    "presentacion": pres_nombre,
                    "fecha": str(_val(fpast, "fecha")),
                    "kg_usado": kg_usado_val,
                    "unidades": unidades_reales,
                    "kg_nominal": kg_nominal,
                    "kg_empacado": unidades_reales * kg_nominal,
                    "entradas_cf": [],
                }

                entradas = cf_entradas[cf_entradas["lote_producto_id"] == fpast["lote_producto_id"]] if not cf_entradas.empty else pd.DataFrame()
                for _, fent in entradas.iterrows():
                    despachos = []
                    if not cf_salidas.empty:
                        rel_sal = cf_salidas[cf_salidas["entrada_id"] == fent["entrada_id"]]
                        for _, fsal in rel_sal.iterrows():
                            cliente_nombre = fsal.get("cliente_id", "")
                            if not clientes.empty:
                                fc = clientes[clientes["cliente_id"] == fsal.get("cliente_id")]
                                if not fc.empty:
                                    cliente_nombre = fc.iloc[0]["nombre"]
                            vehiculo_placa = fsal.get("vehiculo_id", "")
                            if not vehiculos.empty:
                                fv = vehiculos[vehiculos["vehiculo_id"] == fsal.get("vehiculo_id")]
                                if not fv.empty:
                                    vehiculo_placa = fv.iloc[0]["placa"]
                            despachos.append({
                                "fecha": str(_val(fsal, "fecha")),
                                "cliente": cliente_nombre,
                                "vehiculo": vehiculo_placa,
                                "cantidad": float(pd.to_numeric(_val(fsal, "cantidad", 0), errors="coerce") or 0),
                            })
                    nodo_past["entradas_cf"].append({
                        "entrada_id": fent["entrada_id"],
                        "fecha": str(_val(fent, "fecha")),
                        "saldo_actual": float(pd.to_numeric(_val(fent, "saldo", 0), errors="coerce") or 0),
                        "despachos": despachos,
                    })

                nodo_prod["pasteurizaciones"].append(nodo_past)

            # ---- balance de masa de distribucion: pasteurizado = empacado = despachado + en cuarto frio ----
            kg_pasteurizado = sum(p["kg_usado"] for p in nodo_prod["pasteurizaciones"])
            kg_empacado = sum(p["kg_empacado"] for p in nodo_prod["pasteurizaciones"])
            kg_despachado = sum(
                d["cantidad"] * p["kg_nominal"]
                for p in nodo_prod["pasteurizaciones"] for e in p["entradas_cf"] for d in e["despachos"]
            )
            kg_en_cuarto_frio = sum(
                e["saldo_actual"] * p["kg_nominal"]
                for p in nodo_prod["pasteurizaciones"] for e in p["entradas_cf"]
            )
            nodo_prod["balance_distribucion"] = {
                "kg_pasteurizado": kg_pasteurizado,
                "kg_empacado": kg_empacado,
                "kg_despachado": kg_despachado,
                "kg_en_cuarto_frio": kg_en_cuarto_frio,
                "kg_contabilizado": kg_despachado + kg_en_cuarto_frio,
            }

            nodo_rec["producciones"].append(nodo_prod)

        arbol.append(nodo_rec)

    return arbol
