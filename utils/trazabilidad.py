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


def _extraer_recipiente_retorno(observaciones: str):
    """Busca 'Retorno desde recipiente GR-XXX' en las observaciones (lo que
    guarda cuarto_frio.py cuando un recipiente a granel vuelve a produccion
    como lote nuevo, sin consumo de MP propio). Devuelve el codigo del
    recipiente, o None si el lote no nacio de un retorno."""
    m = re.search(r"[Rr]etorno desde recipiente ([^\s).,;]+)", str(observaciones))
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


def _buscar_pedido(pedidos_df, pedido_id):
    """Devuelve los datos relevantes del pedido original, o None si no existe."""
    if pedidos_df.empty or not pedido_id:
        return None
    fila = pedidos_df[pedidos_df["pedido_id"] == pedido_id]
    if fila.empty:
        return None
    f = fila.iloc[0]
    return {
        "pedido_id": pedido_id,
        "cliente_ref": str(f.get("pedido_cliente_ref", "") or ""),
        "fecha_pedido": str(f.get("fecha_pedido", "") or ""),
        "fecha_entrega": str(f.get("fecha_entrega", "") or ""),
        "tipo_producto": str(f.get("tipo_producto", "") or ""),
        "cantidad_kg": float(pd.to_numeric(f.get("cantidad_kg", 0), errors="coerce") or 0),
        "medio_recepcion": str(f.get("medio_recepcion", "") or ""),
    }


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
    turnos = tablas["turnos"]
    produccion_personal = tablas["produccion_personal"]
    personal_cat = tablas["personal"]
    usuarios_cat = tablas["usuarios"]
    pedidos_df = tablas.get("pedidos", pd.DataFrame())
    granel = tablas.get("stock_a_granel", pd.DataFrame())
    movimientos_insumos = tablas.get("movimientos_envases_insumos", pd.DataFrame())
    catalogo_insumos = tablas.get(
        "envases_insumos",
        tablas.get("catalogo_envases_insumos", tablas.get("insumos", pd.DataFrame())),
    )

    def _lote_origen_de_recipiente(recipiente_id):
        """Resuelve recipiente GR -> lote_origen usando stock_a_granel."""
        if granel.empty or "lote_origen" not in granel.columns or not recipiente_id:
            return None
        col_rec = next(
            (c for c in ("recipiente_id", "codigo_recipiente", "recipiente", "codigo")
             if c in granel.columns),
            None,
        )
        if not col_rec:
            return None
        fg = granel[granel[col_rec].astype(str) == str(recipiente_id)]
        if fg.empty:
            return None
        origen = fg.iloc[0]["lote_origen"]
        if origen is None or (isinstance(origen, float) and pd.isna(origen)):
            return None
        origen = str(origen).strip()
        return origen or None

    def _origen_granel_de(lote_semi_id):
        """Si el lote nacio de un retorno desde granel, devuelve un dict con
        el recipiente y el lote de origen; si no, None."""
        if produccion.empty:
            return None
        fila_p = produccion[produccion["lote_semielaborado_id"] == lote_semi_id]
        if fila_p.empty:
            return None
        recipiente = _extraer_recipiente_retorno(fila_p.iloc[0].get("observaciones", ""))
        if not recipiente:
            return None
        return {
            "recipiente": recipiente,
            "lote_origen": _lote_origen_de_recipiente(recipiente) or "no encontrado en stock_a_granel",
        }

    def _insumos_de_lote_producto(lote_producto_id):
        """Movimientos de envases/insumos descontados por este envasado
        (vinculados via observaciones = lote_producto_id)."""
        if movimientos_insumos.empty or "observaciones" not in movimientos_insumos.columns:
            return []
        rel = movimientos_insumos[
            movimientos_insumos["observaciones"].astype(str) == str(lote_producto_id)
        ]
        if rel.empty:
            return []
        col_item = next(
            (c for c in ("item_id", "envase_insumo_id", "insumo_id", "articulo_id")
             if c in rel.columns),
            None,
        )
        col_cant = next((c for c in ("cantidad", "unidades") if c in rel.columns), None)
        col_mov = next((c for c in ("tipo_movimiento", "tipo", "movimiento") if c in rel.columns), None)
        lista = []
        for _, mfila in rel.iterrows():
            item = str(mfila.get(col_item, "") or "") if col_item else ""
            nombre, tipo_item = item, ""
            if not catalogo_insumos.empty and item:
                col_cat_id = next(
                    (c for c in (col_item, "item_id", "envase_insumo_id", "insumo_id")
                     if c and c in catalogo_insumos.columns),
                    None,
                )
                if col_cat_id:
                    fci = catalogo_insumos[catalogo_insumos[col_cat_id].astype(str) == item]
                    if not fci.empty:
                        nombre = str(fci.iloc[0].get("nombre", item) or item)
                        col_cat_tipo = next(
                            (c for c in ("tipo", "categoria") if c in catalogo_insumos.columns),
                            None,
                        )
                        if col_cat_tipo:
                            tipo_item = str(fci.iloc[0].get(col_cat_tipo, "") or "")
            lista.append({
                "nombre": nombre,
                "tipo": tipo_item,
                "cantidad": float(pd.to_numeric(mfila.get(col_cant, 0), errors="coerce") or 0) if col_cant else 0.0,
                "movimiento": str(mfila.get(col_mov, "") or "") if col_mov else "",
            })
        return lista

    def _nombre_turno(turno_id):
        if turnos.empty or not turno_id:
            return str(turno_id) if turno_id else "—"
        ft = turnos[turnos["turno_id"] == turno_id]
        return ft.iloc[0]["nombre"] if not ft.empty else str(turno_id)

    def recepciones_de(lote_semi_id, _visitados=None):
        # _visitados evita un bucle infinito si los datos tuvieran una cadena
        # circular de retornos (no deberia pasar, pero mejor no colgarse)
        if _visitados is None:
            _visitados = set()
        if lote_semi_id in _visitados:
            return []
        _visitados.add(lote_semi_id)

        if not consumo_mp.empty:
            filas = consumo_mp[consumo_mp["lote_semielaborado_id"] == lote_semi_id]
            recs = list(filas["recepcion_id"].unique())
            if recs:
                return recs
        if produccion.empty:
            return []
        fila_p = produccion[produccion["lote_semielaborado_id"] == lote_semi_id]
        if fila_p.empty:
            return []
        observaciones = fila_p.iloc[0].get("observaciones", "")
        # caso 1: lote "hermano" (co-producto clara/yema) sin consumo propio
        hermano = _extraer_lote_hermano(observaciones)
        if hermano and not consumo_mp.empty:
            recs = list(consumo_mp[consumo_mp["lote_semielaborado_id"] == hermano]["recepcion_id"].unique())
            if recs:
                return recs
        # caso 2: lote creado por retorno desde recipiente a granel -- saltar
        # al lote de origen del recipiente (recursivo, por si el origen es a
        # su vez otro retorno o un co-producto)
        recipiente = _extraer_recipiente_retorno(observaciones)
        if recipiente:
            lote_origen = _lote_origen_de_recipiente(recipiente)
            if lote_origen and lote_origen != lote_semi_id:
                return recepciones_de(lote_origen, _visitados)
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
        # agregar lotes hermanos (co-productos clara/yema) que no tienen consumo propio
        for lid in list(lotes_semi):
            if produccion.empty:
                continue
            fp = produccion[produccion["lote_semielaborado_id"] == lid]
            if not fp.empty:
                hermano = _extraer_lote_hermano(fp.iloc[0].get("observaciones", ""))
                if hermano:
                    lotes_semi.add(hermano)

        # agregar lotes creados por "Retorno desde recipiente GR-..." cuyo
        # lote de origen (via stock_a_granel.lote_origen) pertenece a esta
        # recepcion. Se itera hasta punto fijo por si hay retornos encadenados
        # (lote A -> granel -> lote B -> granel -> lote C).
        if not produccion.empty:
            cambio = True
            while cambio:
                cambio = False
                for _, rowp in produccion.iterrows():
                    lid_ret = rowp["lote_semielaborado_id"]
                    if lid_ret in lotes_semi:
                        continue
                    recipiente = _extraer_recipiente_retorno(rowp.get("observaciones", ""))
                    if not recipiente:
                        continue
                    lote_origen = _lote_origen_de_recipiente(recipiente)
                    if lote_origen and lote_origen in lotes_semi:
                        lotes_semi.add(lid_ret)
                        cambio = True

        # Si la consulta es por un lote específico, mostrar solo ese lote y su
        # co-producto directo — no todos los lotes de la misma recepción.
        if tipo_lote == "semielaborado":
            lote_especifico = lote_id
            hermano_directo = None
            if not produccion.empty:
                fp_esp = produccion[produccion["lote_semielaborado_id"] == lote_especifico]
                if not fp_esp.empty:
                    hermano_directo = _extraer_lote_hermano(fp_esp.iloc[0].get("observaciones", ""))
            # también buscar si el lote es el co-producto (el hermano apunta a él)
            hermano_inverso = None
            if not produccion.empty:
                for _, row in produccion.iterrows():
                    h = _extraer_lote_hermano(row.get("observaciones", ""))
                    if h == lote_especifico:
                        hermano_inverso = row["lote_semielaborado_id"]
                        break
            lotes_semi = {l for l in lotes_semi if l in (
                {lote_especifico} | ({hermano_directo} if hermano_directo else set()) |
                ({hermano_inverso} if hermano_inverso else set())
            )}
        elif tipo_lote == "producto":
            # mostrar solo el lote semielaborado padre del producto consultado
            if not pasteurizacion.empty:
                fila_past = pasteurizacion[pasteurizacion["lote_producto_id"] == lote_id]
                if not fila_past.empty:
                    lote_semi_padre = fila_past.iloc[0]["lote_semielaborado_id"]
                    hermano_directo = None
                    if not produccion.empty:
                        fp_esp = produccion[produccion["lote_semielaborado_id"] == lote_semi_padre]
                        if not fp_esp.empty:
                            hermano_directo = _extraer_lote_hermano(fp_esp.iloc[0].get("observaciones", ""))
                    lotes_semi = {l for l in lotes_semi if l in (
                        {lote_semi_padre} | ({hermano_directo} if hermano_directo else set())
                    )}

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
                        "turno": _nombre_turno(sfila.get("turno", "")),
                        "verificado": str(_val(sfila, "verificado")).upper() == "TRUE",
                    })

            # personal que trabajo este lote (o el lote hermano, si el consumo/personal
            # quedo registrado bajo ese -- ver _extraer_lote_hermano)
            lote_personal = lote_hermano if (lote_hermano and not consumo_mp.empty and
                                              not consumo_mp[consumo_mp["lote_semielaborado_id"] == lote_hermano].empty) else lid
            personal_lista = []
            if not produccion_personal.empty:
                rel_personal = produccion_personal[produccion_personal["lote_semielaborado_id"] == lote_personal]
                for _, pfila in rel_personal.iterrows():
                    nombre_persona = pfila.get("personal_id", "")
                    if not personal_cat.empty:
                        fpc = personal_cat[personal_cat["personal_id"] == pfila.get("personal_id")]
                        if not fpc.empty:
                            nombre_persona = fpc.iloc[0]["nombre"]
                    personal_lista.append({
                        "nombre": nombre_persona,
                        "horas": float(pd.to_numeric(pfila.get("horas", 0), errors="coerce") or 0),
                    })

            nodo_prod = {
                "lote_id": lid,
                "tipo_producto": _val(fp, "tipo_producto"),
                "fecha": str(_val(fp, "fecha")),
                "orden_produccion": _val(fp, "orden_produccion"),
                "turno": _nombre_turno(fp.get("turno", "")),
                "cubetas_de_este_lote": cubetas_de_este_lote,
                "lote_hermano": lote_hermano,
                "origen_granel": _origen_granel_de(lid),
                "kg_real": _val(fp, "kg_real", 0),
                "costo_unitario_kg": _val(fp, "costo_unitario_kg", 0),
                "personal": personal_lista,
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
                    "turno": _nombre_turno(fpast.get("turno", "")),
                    "kg_usado": kg_usado_val,
                    "unidades": unidades_reales,
                    "kg_nominal": kg_nominal,
                    "kg_empacado": unidades_reales * kg_nominal,
                    "insumos": _insumos_de_lote_producto(fpast["lote_producto_id"]),
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
                            despachador_nombre = fsal.get("despachador", "")
                            if not personal_cat.empty and despachador_nombre:
                                fu = personal_cat[personal_cat["personal_id"] == despachador_nombre]
                                if not fu.empty:
                                    despachador_nombre = fu.iloc[0]["nombre"]
                            despachos.append({
                                "fecha": str(_val(fsal, "fecha")),
                                "cliente": cliente_nombre,
                                "vehiculo": vehiculo_placa,
                                "cantidad": float(pd.to_numeric(_val(fsal, "cantidad", 0), errors="coerce") or 0),
                                "despachador": despachador_nombre,
                                "pedido_ref": _val(fsal, "pedido_ref", ""),
                                "pedido_info": _buscar_pedido(pedidos_df, _val(fsal, "pedido_ref", "")),
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
