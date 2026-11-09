"""
Bodega de materia prima: recepcion de huevo desde proveedores (internos PC/PSU
y externos). Simplificacion vs. version anterior: el operador solo elige
proveedor, cubetas, costo por cubeta y fecha; el sistema genera automaticamente
el codigo de lote con el formato PREFIJO+DDMMYY (ej. PC270726).

Si el mismo proveedor entrega dos veces el mismo dia, se consolida en el mismo
lote sumando cubetas y recalculando el costo promedio ponderado, para no
duplicar codigos.

El consumo hacia produccion se registra desde el modulo 'Produccion de
semielaborados' con descuento FIFO estricto por fecha de recepcion.
"""
import datetime
import re
from html import escape
import streamlit as st
import pandas as pd
from utils.permisos import ve_costos
from utils.bitacora import log_cambio, log_cambios_multiples


CAUSAS_MERMA_HUEVO = ["Caída", "Rotura en bodega", "Daño de transporte/proveedor", "Otro"]


def _prefijo_valido(prefijo):
    """Prefijo de proveedor: 2-4 letras mayusculas."""
    return bool(re.fullmatch(r"[A-Z]{2,4}", str(prefijo or "").strip()))


def _proveedores_activos(db):
    """Devuelve proveedores activos con prefijo valido. Los que estan activos
    pero sin prefijo se devuelven aparte para forzar al admin a asignarlo."""
    prov = db.get_df("proveedores")
    if prov.empty:
        return pd.DataFrame(), pd.DataFrame()
    activos = prov[prov.get("activo", "TRUE").astype(str).str.upper() != "FALSE"].copy()
    if "prefijo" not in activos.columns:
        activos["prefijo"] = ""
    activos["prefijo"] = activos["prefijo"].astype(str).str.strip().str.upper()
    con_prefijo = activos[activos["prefijo"].apply(_prefijo_valido)].copy()
    sin_prefijo = activos[~activos["prefijo"].apply(_prefijo_valido)].copy()
    return con_prefijo, sin_prefijo


def _generar_lote(prefijo, fecha):
    """Genera el codigo de lote a partir del prefijo y la fecha (DDMMYY)."""
    return f"{prefijo}{fecha.strftime('%d%m%y')}"


def _lote_estado_activo(fila):
    """Una recepcion se considera activa (visible en bodega) si su columna
    'estado' esta vacia o vale 'activo'. Cualquier otro valor (p.ej.
    'eliminado') la excluye."""
    estado = str(fila.get("estado", "activo") or "activo").strip().lower()
    return estado in ("", "activo")


def _recepciones_activas(db):
    """Devuelve solo las recepciones activas (no eliminadas logicamente)."""
    df = db.get_df("recepciones_mp")
    if df.empty:
        return df
    if "estado" not in df.columns:
        return df
    df = df.copy()
    df["estado"] = df["estado"].fillna("activo").astype(str).str.strip().str.lower()
    return df[df["estado"].isin(["", "activo"])]


def _recepcion_ya_consumida(db, recepcion_id):
    """True si la recepcion ya aparece en consumo_mp_produccion o en mermas_mp
    (no se puede eliminar ni editar campos criticos)."""
    consumos = db.get_df("consumo_mp_produccion")
    if not consumos.empty and "recepcion_id" in consumos.columns:
        if str(recepcion_id) in consumos["recepcion_id"].astype(str).values:
            return True
    mermas = db.get_df("mermas_mp")
    if not mermas.empty and "recepcion_id" in mermas.columns:
        if str(recepcion_id) in mermas["recepcion_id"].astype(str).values:
            return True
    return False


TURNOS_RECEPCION = {1: "Primer turno", 2: "Segundo turno"}


def _turno_de(fila, base):
    """Turno de una recepcion: el de la columna 'turno' o, para las que se
    registraron antes de existir esa columna, se deduce del codigo
    (sin sufijo = primer turno, sufijo -2 = segundo turno)."""
    t = str(fila.get("turno", "") or "").strip()
    if t.isdigit():
        return int(t)
    rid = str(fila.get("recepcion_id", ""))
    if rid == base:
        return 1
    if rid == f"{base}-2":
        return 2
    return None


def _texto_turno(fila):
    """Turno para mostrar en tablas ('Primer turno' / 'Segundo turno')."""
    rid = str(fila.get("recepcion_id", ""))
    base = rid.split("-")[0]
    t = _turno_de(fila, base)
    return TURNOS_RECEPCION.get(t, "Primer turno")


def _lote_para_turno(db, prefijo, fecha, turno):
    """Devuelve (codigo_de_lote, recepcion_activa_que_ya_ocupa_ese_turno).
    Primer turno = PREFIJO+DDMMYY (ej. PC081126); segundo turno = mismo
    codigo con -2 (PC081126-2). Si ese codigo ya lo usa una recepcion
    ELIMINADA, se toma el siguiente sufijo libre (-3, -4...) para no repetir
    ids; el turno queda igual guardado en su columna."""
    base = _generar_lote(prefijo, fecha)
    preferido = base if turno == 1 else f"{base}-{turno}"
    todas = db.get_df("recepciones_mp")
    if todas.empty or "recepcion_id" not in todas.columns:
        return preferido, None
    todas = todas.copy()
    todas["recepcion_id"] = todas["recepcion_id"].astype(str)
    del_dia = todas[
        (todas["recepcion_id"] == base) | todas["recepcion_id"].str.startswith(f"{base}-")
    ]
    for _, fila in del_dia.iterrows():
        if _lote_estado_activo(fila) and _turno_de(fila, base) == turno:
            return fila["recepcion_id"], fila
    usados = set(todas["recepcion_id"])
    if preferido not in usados:
        return preferido, None
    n = 3
    while f"{base}-{n}" in usados:
        n += 1
    return f"{base}-{n}", None


def render(db, username, rol):
    st.title("Bodega de materia prima — huevo")

    # Advertencia si hay proveedores sin prefijo asignado -- los bloqueamos
    # aqui para que el admin los complete antes de recibir cubetas de ellos.
    _, sin_prefijo = _proveedores_activos(db)
    if not sin_prefijo.empty:
        st.warning(
            f"⚠️ Hay **{len(sin_prefijo)} proveedor(es) activo(s) sin prefijo asignado**. "
            f"El prefijo es obligatorio porque genera el código de lote (ej. PC270726). "
            f"Ve a **Catálogos → Proveedores** y asígnales un prefijo antes de "
            f"registrar recepciones de ellos."
        )
        with st.expander("Ver proveedores sin prefijo"):
            st.dataframe(
                sin_prefijo[["proveedor_id", "nombre"]] if "proveedor_id" in sin_prefijo.columns else sin_prefijo,
                use_container_width=True,
            )

    tab_recepcion, tab_inventario, tab_historial, tab_perdida, tab_ajuste, tab_corregir = st.tabs(
        ["Registrar recepción", "Inventario actual", "Historial",
         "⚠️ Registrar pérdida/daño", "⚖️ Ajuste de inventario",
         "✏️ Corregir / eliminar recepción"]
    )

    with tab_ajuste:
        _render_ajuste_inventario(db, username)

    # ======================== REGISTRAR RECEPCIÓN ========================
    with tab_recepcion:
        con_prefijo, _ = _proveedores_activos(db)
        if con_prefijo.empty:
            st.warning(
                "No hay proveedores activos con prefijo válido. Ve a "
                "**Catálogos → Proveedores** y crea al menos uno (con prefijo "
                "de 2-4 letras) antes de registrar recepciones."
            )
        else:
            st.caption(
                "Solo elige proveedor, turno, cubetas y costo — el sistema genera el "
                "código de lote automáticamente (prefijo+fecha, ej. **PC270726**). "
                "Cada entrega queda registrada por separado: la del **segundo turno** "
                "lleva el sufijo **-2** (ej. **PC270726-2**)."
            )

            # Preview del lote que se va a generar (fuera del form para que
            # se actualice en vivo al cambiar proveedor, fecha o turno)
            con_prefijo = con_prefijo.sort_values(
                ["tipo", "nombre"] if "tipo" in con_prefijo.columns else ["nombre"]
            )
            proveedor_id = st.selectbox(
                "Proveedor",
                con_prefijo["proveedor_id"],
                format_func=lambda x: (
                    f"{con_prefijo.set_index('proveedor_id').loc[x, 'nombre']} "
                    f"({con_prefijo.set_index('proveedor_id').loc[x, 'prefijo']})"
                    + (f" — {con_prefijo.set_index('proveedor_id').loc[x, 'tipo']}"
                       if 'tipo' in con_prefijo.columns else "")
                ),
                key="rec_prov",
            )
            c_fecha, c_turno = st.columns(2)
            fecha = c_fecha.date_input("Fecha de recepción", value=datetime.date.today(), key="rec_fecha")
            turno = c_turno.radio(
                "Turno", list(TURNOS_RECEPCION), format_func=TURNOS_RECEPCION.get,
                horizontal=True, key="rec_turno",
            )

            fila_prov = con_prefijo.set_index("proveedor_id").loc[proveedor_id]
            prefijo_sel = str(fila_prov["prefijo"]).strip().upper()
            lote_preview, existente = _lote_para_turno(db, prefijo_sel, fecha, turno)

            if existente is not None:
                cub_prev = float(pd.to_numeric(existente.get("cubetas", 0), errors="coerce") or 0)
                st.warning(
                    f"⚠️ Ya hay una recepción de este proveedor en el **{TURNOS_RECEPCION[turno].lower()}** "
                    f"de esa fecha: lote **{lote_preview}** con **{cub_prev:.0f} cubetas**. "
                    f"Si es otra entrega, elige el otro turno. Si la cantidad de ese lote "
                    f"está mal, corrígela en **✏️ Corregir / eliminar recepción**."
                )
            else:
                st.success(f"📦 Se creará el lote nuevo: **{lote_preview}**")

            with st.form("form_recepcion"):
                cubetas = st.number_input(
                    "Cubetas recibidas (30 huevos c/u)", min_value=1, step=1, key="rec_cub",
                )
                costo_cubeta = st.number_input(
                    "Costo por cubeta ($)", min_value=0.0, step=0.01, format="%.2f", key="rec_costo",
                )
                observaciones = st.text_area("Observaciones", "", key="rec_obs")
                guardar = st.form_submit_button("Registrar recepción", disabled=existente is not None)

            if guardar:
                # Releer la hoja: si otro usuario registro el mismo turno
                # mientras se llenaba el formulario, no se duplica el lote.
                db.get_df("recepciones_mp", forzar_actualizacion=True)
                lote_nuevo, existente = _lote_para_turno(db, prefijo_sel, fecha, turno)
                if cubetas <= 0:
                    st.error("Ingresa una cantidad mayor a cero.")
                elif existente is not None:
                    st.error(
                        f"Ya existe la recepción {lote_nuevo} para ese proveedor, fecha y turno. "
                        f"No se guardó nada."
                    )
                else:
                    costo_total = cubetas * costo_cubeta
                    db.append_row("recepciones_mp", {
                        "recepcion_id": lote_nuevo,
                        "fecha": fecha.isoformat(),
                        "origen_tipo": "Proveedor",
                        "origen_id": proveedor_id,
                        "cubetas": cubetas,
                        "costo_cubeta": costo_cubeta,
                        "costo_total": costo_total,
                        "cubetas_saldo": cubetas,
                        "usuario": username,
                        "observaciones": observaciones,
                        "estado": "activo",
                        "turno": turno,
                    })
                    st.success(
                        f"✅ Recepción **{lote_nuevo}** registrada — "
                        f"{cubetas:.0f} cub. @ ${costo_cubeta:.2f}/cub. "
                        f"(total ${costo_total:,.2f})"
                    )
                    st.rerun()

    # ======================== INVENTARIO ACTUAL ========================
    with tab_inventario:
        if st.button("🔄 Actualizar inventario", key="mp_inv_refrescar"):
            db.get_df("recepciones_mp", forzar_actualizacion=True)
        df = _recepciones_activas(db)
        if df.empty:
            st.info("Todavía no hay recepciones activas registradas.")
        else:
            df["cubetas_saldo"] = pd.to_numeric(df["cubetas_saldo"], errors="coerce").fillna(0)
            inventario = df[df["cubetas_saldo"] > 0].copy()
            if inventario.empty:
                st.info("No hay saldo disponible en bodega de materia prima.")
            else:
                inventario["fecha"] = pd.to_datetime(inventario["fecha"], errors="coerce")
                inventario["costo_cubeta"] = pd.to_numeric(inventario["costo_cubeta"], errors="coerce")
                inventario["valor_saldo"] = inventario["cubetas_saldo"] * inventario["costo_cubeta"]
                # FIFO: el mas antiguo primero
                inventario = inventario.sort_values("fecha", na_position="last")

                # Traer nombre y prefijo del proveedor para claridad
                prov = db.get_df("proveedores")
                if not prov.empty and "proveedor_id" in prov.columns:
                    inventario = inventario.merge(
                        prov[["proveedor_id", "nombre", "tipo"]].rename(
                            columns={"nombre": "proveedor_nombre", "proveedor_id": "origen_id"}
                        ) if "tipo" in prov.columns else
                        prov[["proveedor_id", "nombre"]].rename(
                            columns={"nombre": "proveedor_nombre", "proveedor_id": "origen_id"}
                        ),
                        on="origen_id", how="left",
                    )
                    inventario["proveedor_nombre"] = inventario["proveedor_nombre"].fillna(
                        inventario["origen_id"]
                    )

                inventario["turno_txt"] = [_texto_turno(f) for _, f in inventario.iterrows()]
                columnas_inv = ["recepcion_id", "fecha", "turno_txt", "proveedor_nombre", "cubetas_saldo"]
                if "tipo" in inventario.columns:
                    columnas_inv.insert(3, "tipo")
                if ve_costos(rol):
                    columnas_inv += ["costo_cubeta", "valor_saldo"]
                columnas_inv = [c for c in columnas_inv if c in inventario.columns]
                # Solo la fecha, sin la hora 00:00:00 que agrega pd.to_datetime
                tabla_inv = inventario[columnas_inv].copy()
                tabla_inv["fecha"] = tabla_inv["fecha"].dt.date
                st.dataframe(
                    tabla_inv.rename(columns={
                        "recepcion_id": "Lote", "fecha": "Fecha", "proveedor_nombre": "Proveedor",
                        "tipo": "Tipo", "turno_txt": "Turno", "cubetas_saldo": "Saldo (cub.)",
                        "costo_cubeta": "Costo/cub. ($)", "valor_saldo": "Valor saldo ($)",
                    }),
                    use_container_width=True, hide_index=True,
                    column_config={
                        "Fecha": st.column_config.DateColumn(format="YYYY-MM-DD"),
                        "Saldo (cub.)": st.column_config.NumberColumn(format="%.2f"),
                        "Costo/cub. ($)": st.column_config.NumberColumn(format="%.2f"),
                        "Valor saldo ($)": st.column_config.NumberColumn(format="%.2f"),
                    },
                )
                st.caption(
                    "Para ver los ingresos y salidas de cada lote, revisa la pestaña **Historial**."
                )

                total_cubetas = int(inventario["cubetas_saldo"].sum())
                c1, c2 = st.columns(2)
                c1.metric("Total cubetas en bodega", f"{total_cubetas:,}")
                c2.metric("Total huevos en bodega", f"{total_cubetas * 30:,}")
                if ve_costos(rol):
                    valor_total = float(inventario["valor_saldo"].sum())
                    st.metric("Valor total en bodega ($)", f"{valor_total:,.2f}")

                # Grafica de barras horizontales, coloreadas por tipo (interno vs externo)
                import plotly.graph_objects as go
                st.write("")
                if "tipo" in inventario.columns:
                    colores = inventario["tipo"].astype(str).str.lower().map(
                        {"interno": "#1565c0", "externo": "#f57c00"}
                    ).fillna("#546e7a")
                else:
                    colores = "#2e7d32"
                etiquetas_y = (
                    inventario["recepcion_id"].astype(str)
                    + " — " + inventario["proveedor_nombre"].astype(str)
                    if "proveedor_nombre" in inventario.columns
                    else inventario["recepcion_id"].astype(str)
                )
                fig = go.Figure(go.Bar(
                    x=inventario["cubetas_saldo"].tolist(),
                    y=etiquetas_y.tolist(),
                    orientation="h",
                    marker_color=colores if not isinstance(colores, str) else colores,
                    text=inventario["cubetas_saldo"].apply(lambda v: f"{int(v)} cub.").tolist(),
                    textposition="outside",
                    hovertemplate="%{y}: %{x} cubetas<extra></extra>",
                ))
                fig.update_layout(
                    title="Cubetas disponibles por lote (orden FIFO: arriba el más antiguo)",
                    xaxis_title="Cubetas",
                    height=max(250, len(inventario) * 45),
                    margin=dict(l=10, r=60, t=40, b=20),
                    paper_bgcolor="rgba(0,0,0,0)",
                    plot_bgcolor="rgba(0,0,0,0)",
                )
                st.plotly_chart(fig, use_container_width=True)

    # ======================== HISTORIAL ========================
    with tab_historial:
        _render_historial(db, rol)

    # ======================== REGISTRAR PÉRDIDA/DAÑO ========================
    with tab_perdida:
        st.caption(
            "Para huevo que se daña en bodega ANTES de entrar a producción "
            "(se cayó, se rompió, llegó dañado, etc.) — esto descuenta del "
            "saldo del lote igual que un consumo, pero queda registrado como "
            "pérdida, no como producción."
        )
        df = _recepciones_activas(db)
        if df.empty:
            st.info("No hay recepciones activas registradas todavía.")
        else:
            df["cubetas_saldo"] = pd.to_numeric(df["cubetas_saldo"], errors="coerce").fillna(0)
            disponibles = df[df["cubetas_saldo"] > 0]
            if disponibles.empty:
                st.info("No hay saldo disponible en ningún lote.")
            else:
                fecha = st.date_input("Fecha", value=datetime.date.today(), key="perdida_fecha")
                recepcion_id = st.selectbox(
                    "Lote afectado",
                    disponibles["recepcion_id"],
                    format_func=lambda x: (
                        f"{x} — saldo {disponibles.set_index('recepcion_id').loc[x, 'cubetas_saldo']:.0f} cubetas"
                    ),
                )
                fila_lote = disponibles.set_index("recepcion_id").loc[recepcion_id]
                cubetas_saldo_lote = float(fila_lote["cubetas_saldo"])
                costo_cubeta_lote = float(pd.to_numeric(fila_lote.get("costo_cubeta", 0), errors="coerce") or 0)

                huevos_danados = st.number_input(
                    "Cantidad de huevos dañados/perdidos", min_value=0, step=1,
                )
                cubetas_equivalentes = huevos_danados / 30
                if cubetas_equivalentes > cubetas_saldo_lote:
                    st.error(
                        f"Eso equivale a {cubetas_equivalentes:.2f} cubetas, pero el lote solo "
                        f"tiene {cubetas_saldo_lote:.2f} cubetas de saldo. Revisa la cantidad."
                    )
                costo_estimado = cubetas_equivalentes * costo_cubeta_lote
                if ve_costos(rol):
                    st.caption(
                        f"≈ {cubetas_equivalentes:.2f} cubetas — costo estimado de la pérdida: ${costo_estimado:,.2f}"
                    )
                else:
                    st.caption(f"≈ {cubetas_equivalentes:.2f} cubetas")

                causa = st.selectbox("Causa", CAUSAS_MERMA_HUEVO)
                observaciones = st.text_area("Observaciones", "", key="perdida_obs")

                if st.button("Registrar pérdida"):
                    if huevos_danados <= 0:
                        st.error("Ingresa una cantidad mayor a cero.")
                    elif cubetas_equivalentes > cubetas_saldo_lote:
                        st.error("La cantidad supera el saldo disponible del lote. Corrige antes de guardar.")
                    else:
                        merma_id = db.siguiente_id("mermas_mp", "MERMP", fecha)
                        db.append_row("mermas_mp", {
                            "merma_id": merma_id,
                            "fecha": fecha.isoformat(),
                            "recepcion_id": recepcion_id,
                            "causa": causa,
                            "huevos_danados": huevos_danados,
                            "cubetas_equivalentes": cubetas_equivalentes,
                            "costo_estimado": costo_estimado,
                            "usuario": username,
                            "observaciones": observaciones,
                        })
                        frescas = db.get_df("recepciones_mp", forzar_actualizacion=True)
                        fila_fresca = frescas[frescas["recepcion_id"].astype(str) == str(recepcion_id)]
                        saldo_real = (
                            float(pd.to_numeric(fila_fresca.iloc[0]["cubetas_saldo"], errors="coerce") or 0)
                            if not fila_fresca.empty else cubetas_saldo_lote
                        )
                        db.update_row("recepciones_mp", "recepcion_id", recepcion_id, {
                            "cubetas_saldo": saldo_real - cubetas_equivalentes,
                        })
                        st.success(
                            f"Pérdida {merma_id} registrada — {huevos_danados} huevos "
                            f"({cubetas_equivalentes:.2f} cubetas, costo ${costo_estimado:,.2f})"
                        )
                        st.rerun()

        st.divider()
        st.markdown("**Histórico de pérdidas**")
        mermas = db.get_df("mermas_mp")
        if mermas.empty:
            st.info("No hay pérdidas registradas todavía.")
        else:
            columnas_merma = ["fecha", "recepcion_id", "causa", "huevos_danados", "observaciones"]
            if ve_costos(rol):
                columnas_merma.insert(4, "costo_estimado")
            columnas_merma = [c for c in columnas_merma if c in mermas.columns]
            st.dataframe(
                mermas[columnas_merma].sort_values("fecha", ascending=False),
                use_container_width=True, hide_index=True,
            )
            if ve_costos(rol) and "costo_estimado" in mermas.columns:
                costo_total_mermas = pd.to_numeric(mermas["costo_estimado"], errors="coerce").fillna(0).sum()
                st.metric("Costo total acumulado en pérdidas", f"${costo_total_mermas:,.2f}")

    # ======================== CORREGIR / ELIMINAR RECEPCIÓN ========================
    with tab_corregir:
        st.caption(
            "Aquí puedes corregir errores de digitación (cubetas, saldo, costo) o "
            "**eliminar** una recepción que fue creada por error. Las recepciones "
            "que ya se consumieron en producción o tienen mermas registradas "
            "**no se pueden eliminar** — si hay un error real, corrige el "
            "consumo/merma primero desde su módulo."
        )
        recepciones = db.get_df("recepciones_mp")
        if recepciones.empty:
            st.info("No hay recepciones registradas todavía.")
        else:
            # Solo las activas
            if "estado" in recepciones.columns:
                recepciones["estado"] = recepciones["estado"].fillna("activo").astype(str).str.strip().str.lower()
                recepciones = recepciones[recepciones["estado"].isin(["", "activo"])]

            if recepciones.empty:
                st.info("Todas las recepciones registradas están eliminadas.")
            else:
                recepciones["cubetas_saldo"] = pd.to_numeric(recepciones["cubetas_saldo"], errors="coerce").fillna(0)
                recepciones["cubetas"] = pd.to_numeric(recepciones["cubetas"], errors="coerce").fillna(0)
                recepciones["costo_cubeta"] = pd.to_numeric(recepciones["costo_cubeta"], errors="coerce").fillna(0)

                rec_sel = st.selectbox(
                    "Recepción a corregir o eliminar",
                    recepciones["recepcion_id"],
                    format_func=lambda x: (
                        f"{x} — {recepciones.set_index('recepcion_id').loc[x, 'fecha']} — "
                        f"{recepciones.set_index('recepcion_id').loc[x, 'cubetas']:.0f} cub. recibidas / "
                        f"{recepciones.set_index('recepcion_id').loc[x, 'cubetas_saldo']:.0f} cub. saldo"
                    ),
                    key="mp_corr_sel",
                )
                fila_r = recepciones.set_index("recepcion_id").loc[rec_sel]
                ya_consumida = _recepcion_ya_consumida(db, rec_sel)
                if ya_consumida:
                    st.warning(
                        "🔒 Esta recepción ya fue **consumida en producción** o "
                        "tiene **mermas registradas**. Puedes corregir saldo/costo "
                        "con cuidado, pero no puedes eliminarla."
                    )

                st.markdown("### ✏️ Corregir campos")
                with st.form("form_corr_mp"):
                    c1, c2, c3 = st.columns(3)
                    nuevas_cub   = c1.number_input("Cubetas recibidas", min_value=0.0, step=1.0,
                                                    value=float(fila_r["cubetas"]))
                    nuevo_saldo  = c2.number_input("Saldo actual (cubetas)", min_value=0.0, step=1.0,
                                                    value=float(fila_r["cubetas_saldo"]))
                    nuevo_costo  = c3.number_input("Costo por cubeta ($)", min_value=0.0, step=0.01,
                                                    value=float(fila_r["costo_cubeta"]))
                    motivo = st.text_input("Motivo de la corrección", "")
                    submitted = st.form_submit_button("💾 Guardar corrección", type="primary")
                    if submitted:
                        if not motivo.strip():
                            st.error("Escribe el motivo de la corrección.")
                        else:
                            obs_prev = str(fila_r.get("observaciones", "") or "")
                            marca_edit = (
                                f"Corregido {datetime.date.today().isoformat()} por {username}: "
                                f"cub {float(fila_r['cubetas']):.0f}→{nuevas_cub:.0f}, "
                                f"saldo {float(fila_r['cubetas_saldo']):.0f}→{nuevo_saldo:.0f}, "
                                f"costo {float(fila_r['costo_cubeta']):.2f}→{nuevo_costo:.2f}. "
                                f"Motivo: {motivo.strip()}"
                            )
                            db.update_row("recepciones_mp", "recepcion_id", rec_sel, {
                                "cubetas": nuevas_cub,
                                "cubetas_saldo": nuevo_saldo,
                                "costo_cubeta": nuevo_costo,
                                "costo_total": round(nuevas_cub * nuevo_costo, 2),
                                "observaciones": (obs_prev + " | " + marca_edit).strip(" |"),
                            })
                            log_cambios_multiples(
                                db, username,
                                modulo="Bodega de materia prima", tabla="recepciones_mp",
                                id_registro=rec_sel,
                                cambios={
                                    "cubetas": (float(fila_r["cubetas"]), nuevas_cub),
                                    "cubetas_saldo": (float(fila_r["cubetas_saldo"]), nuevo_saldo),
                                    "costo_cubeta": (float(fila_r["costo_cubeta"]), nuevo_costo),
                                },
                                motivo=motivo.strip(),
                            )
                            st.success(f"✅ Recepción {rec_sel} corregida.")
                            st.rerun()

                st.divider()
                st.markdown("### 🗑️ Eliminar recepción")
                if ya_consumida:
                    st.info("No disponible: esta recepción ya tiene consumos o mermas asociados.")
                else:
                    st.caption(
                        "**Eliminación lógica**: la recepción se marca como "
                        "eliminada y desaparece del inventario, pero se conserva "
                        "en la base para auditoría. Para confirmar, escribe el "
                        "código exacto del lote."
                    )
                    with st.form("form_elim_mp"):
                        confirmacion = st.text_input(
                            f"Escribe **{rec_sel}** para confirmar la eliminación",
                            key="mp_elim_confirm",
                        )
                        motivo_elim = st.text_area(
                            "Motivo de la eliminación (obligatorio)", "",
                            key="mp_elim_motivo",
                        )
                        submitted_elim = st.form_submit_button("🗑️ Eliminar recepción", type="secondary")
                        if submitted_elim:
                            if confirmacion.strip() != str(rec_sel):
                                st.error(
                                    f"La confirmación no coincide. Debes escribir exactamente: {rec_sel}"
                                )
                            elif not motivo_elim.strip():
                                st.error("Escribe el motivo de la eliminación.")
                            else:
                                obs_prev = str(fila_r.get("observaciones", "") or "")
                                marca_elim = (
                                    f"ELIMINADA {datetime.date.today().isoformat()} por "
                                    f"{username}. Motivo: {motivo_elim.strip()}"
                                )
                                db.update_row("recepciones_mp", "recepcion_id", rec_sel, {
                                    "estado": "eliminado",
                                    "observaciones": (obs_prev + " | " + marca_elim).strip(" |"),
                                })
                                log_cambio(
                                    db, username,
                                    modulo="Bodega de materia prima", tabla="recepciones_mp",
                                    id_registro=rec_sel, accion="eliminacion",
                                    motivo=motivo_elim.strip(),
                                )
                                st.success(
                                    f"✅ Recepción {rec_sel} eliminada (borrado lógico). "
                                    f"Ya no aparece en el inventario."
                                )
                                st.rerun()


MOTIVOS_AJUSTE_MP = [
    "Conteo físico no coincide",
    "Recepción registrada con menos cubetas",
    "Recepción registrada con más cubetas",
    "Consumo de producción mal registrado",
    "Otro",
]


def _render_ajuste_inventario(db, username):
    """Lleva el saldo de un lote al conteo fisico, hacia arriba o hacia abajo.
    Exige motivo y trabajador responsable; el historial cuenta los ajustes por
    responsable para detectar a quien capacitar."""
    personal = db.get_df("personal")
    mapa_personal = (
        dict(zip(personal["personal_id"], personal["nombre"]))
        if not personal.empty and "personal_id" in personal.columns else {}
    )
    prov = db.get_df("proveedores")
    mapa_prov = (
        dict(zip(prov["proveedor_id"], prov["nombre"]))
        if not prov.empty and "proveedor_id" in prov.columns else {}
    )

    sub_registrar, sub_historial = st.tabs(["➕ Registrar ajuste", "📊 Historial de ajustes"])

    with sub_registrar:
        st.caption(
            "Usa esto cuando el conteo físico de cubetas no coincide con el sistema — "
            "sirve tanto para **agregar** como para **quitar** cubetas. Escribe cuántas "
            "hay **realmente** en el lote; el sistema calcula la diferencia y la deja "
            "registrada con el motivo y el responsable."
        )
        recepciones = _recepciones_activas(db)
        if recepciones.empty:
            st.info("No hay recepciones activas registradas todavía.")
        elif not mapa_personal:
            st.warning("Configura el personal en Catálogos para poder registrar al responsable.")
        else:
            recepciones = recepciones.copy()
            recepciones["cubetas_saldo"] = pd.to_numeric(recepciones["cubetas_saldo"], errors="coerce").fillna(0)
            incluir_cero = st.checkbox(
                "Mostrar también lotes que el sistema tiene en cero (para agregar cubetas)",
                key="mp_ajuste_incluir_cero",
            )
            lotes = recepciones if incluir_cero else recepciones[recepciones["cubetas_saldo"] > 0]
            lotes = lotes.sort_values("fecha", ascending=False)
            if lotes.empty:
                st.info("No hay lotes con saldo. Marca la casilla de arriba para ver los lotes en cero.")
            else:
                def _etiqueta_lote(rid):
                    r = lotes.set_index("recepcion_id").loc[rid]
                    return (
                        f"{rid} | {r['fecha']} | {mapa_prov.get(r['origen_id'], r['origen_id'])} | "
                        f"en sistema: {r['cubetas_saldo']:g} cub."
                    )

                rec_sel = st.selectbox(
                    "Lote", lotes["recepcion_id"], format_func=_etiqueta_lote, key="mp_ajuste_lote",
                )
                saldo_sistema = float(lotes.set_index("recepcion_id").loc[rec_sel, "cubetas_saldo"])

                c1, c2, c3 = st.columns(3)
                c1.metric("📦 En sistema", f"{saldo_sistema:g} cub.")
                contado = c2.number_input(
                    "Cubetas contadas físicamente", min_value=0.0, step=1.0,
                    value=saldo_sistema, key=f"mp_ajuste_contado_{rec_sel}",
                )
                diferencia = round(contado - saldo_sistema, 4)
                c3.metric("Diferencia", f"{diferencia:+g} cub.")

                responsable = st.selectbox(
                    "Trabajador responsable del descuadre",
                    [""] + list(mapa_personal),
                    format_func=lambda x: "— Selecciona el responsable —" if x == "" else mapa_personal[x],
                    key="mp_ajuste_responsable",
                )
                motivo = st.selectbox("Motivo del ajuste", MOTIVOS_AJUSTE_MP, key="mp_ajuste_motivo")
                detalle = st.text_area(
                    "Explica qué pasó (obligatorio)",
                    placeholder="ej. 'llegaron 75 cubetas más de La Libertad y no se registraron'",
                    key="mp_ajuste_detalle",
                )

                if st.button("💾 Guardar ajuste", type="primary", key="mp_ajuste_guardar"):
                    if diferencia == 0:
                        st.error("El conteo es igual al sistema — no hay nada que ajustar.")
                    elif not responsable:
                        st.error("Selecciona el trabajador responsable.")
                    elif not detalle.strip():
                        st.error("Explica qué pasó antes de guardar.")
                    else:
                        # Saldo fresco de la hoja: si produccion consumio del lote
                        # mientras se llenaba el formulario, el ajuste se calcula
                        # contra el saldo real y no contra el de la pantalla.
                        frescas = db.get_df("recepciones_mp", forzar_actualizacion=True)
                        fila = frescas[frescas["recepcion_id"].astype(str) == str(rec_sel)].iloc[0]
                        saldo_anterior = float(pd.to_numeric(fila["cubetas_saldo"], errors="coerce") or 0)
                        hoy = datetime.date.today()
                        ajuste_id = db.siguiente_id("mp_ajustes", "AJMP", hoy)
                        db.append_row("mp_ajustes", {
                            "ajuste_id": ajuste_id,
                            "fecha": hoy.isoformat(),
                            "recepcion_id": rec_sel,
                            "origen_id": fila["origen_id"],
                            "saldo_anterior": saldo_anterior,
                            "saldo_nuevo": contado,
                            "diferencia": round(contado - saldo_anterior, 4),
                            "motivo": motivo,
                            "detalle": detalle.strip(),
                            "responsable": responsable,
                            "usuario": username,
                        })
                        obs_prev = str(fila.get("observaciones", "") or "")
                        db.update_row("recepciones_mp", "recepcion_id", rec_sel, {
                            "cubetas_saldo": contado,
                            "observaciones": (
                                obs_prev + f" | {ajuste_id}: saldo {saldo_anterior:g}→{contado:g} ({motivo})"
                            ).strip(" |"),
                        })
                        log_cambio(
                            db, username,
                            modulo="Bodega de materia prima", tabla="recepciones_mp",
                            id_registro=rec_sel, accion="ajuste_inventario",
                            campo="cubetas_saldo", valor_antes=saldo_anterior, valor_despues=contado,
                            motivo=f"{ajuste_id} — {motivo}: {detalle.strip()} "
                                   f"(responsable: {mapa_personal.get(responsable, responsable)})",
                        )
                        st.success(
                            f"✅ Ajuste {ajuste_id} guardado: {rec_sel} pasa de "
                            f"{saldo_anterior:g} a {contado:g} cubetas."
                        )
                        st.rerun()

    with sub_historial:
        ajustes = db.get_df("mp_ajustes")
        if ajustes.empty:
            st.info("No hay ajustes de inventario registrados todavía.")
            return
        ajustes = ajustes.copy()
        ajustes["diferencia"] = pd.to_numeric(ajustes["diferencia"], errors="coerce").fillna(0)
        ajustes["responsable_nombre"] = ajustes["responsable"].apply(
            lambda x: mapa_personal.get(str(x), str(x)) if str(x).strip() else "Sin registrar"
        )
        ajustes["proveedor"] = ajustes["origen_id"].map(mapa_prov).fillna(ajustes["origen_id"])

        c1, c2 = st.columns(2)
        desde = c1.date_input("Desde", value=datetime.date.today() - datetime.timedelta(days=30), key="mp_ajuste_desde")
        hasta = c2.date_input("Hasta", value=datetime.date.today(), key="mp_ajuste_hasta")
        periodo = ajustes[
            (ajustes["fecha"].astype(str) >= desde.isoformat())
            & (ajustes["fecha"].astype(str) <= hasta.isoformat())
        ]
        if periodo.empty:
            st.info("No hay ajustes en ese período.")
            return

        faltantes = abs(periodo.loc[periodo["diferencia"] < 0, "diferencia"].sum())
        sobrantes = periodo.loc[periodo["diferencia"] > 0, "diferencia"].sum()
        m1, m2, m3 = st.columns(3)
        m1.metric("Ajustes registrados", len(periodo))
        m2.metric("Cubetas quitadas", f"{faltantes:,.0f}")
        m3.metric("Cubetas agregadas", f"{sobrantes:,.0f}")

        st.markdown("##### Ajustes por trabajador responsable")
        conteo = periodo.groupby("responsable_nombre").agg(
            ajustes=("ajuste_id", "count"),
            quitadas=("diferencia", lambda s: -s[s < 0].sum()),
            agregadas=("diferencia", lambda s: s[s > 0].sum()),
        ).reset_index().sort_values("ajustes", ascending=False)
        st.dataframe(
            conteo.rename(columns={
                "responsable_nombre": "Responsable", "ajustes": "Ajustes",
                "quitadas": "Cub. quitadas", "agregadas": "Cub. agregadas",
            }),
            use_container_width=True, hide_index=True,
        )
        st.bar_chart(conteo.set_index("responsable_nombre")["ajustes"])

        st.markdown("##### Detalle")
        st.dataframe(
            periodo.sort_values("ajuste_id", ascending=False)[[
                "fecha", "ajuste_id", "responsable_nombre", "recepcion_id", "proveedor",
                "saldo_anterior", "saldo_nuevo", "diferencia", "motivo", "detalle", "usuario",
            ]].rename(columns={
                "fecha": "Fecha", "ajuste_id": "Ajuste", "responsable_nombre": "Responsable",
                "recepcion_id": "Lote", "proveedor": "Proveedor",
                "saldo_anterior": "Antes", "saldo_nuevo": "Después", "diferencia": "Diferencia",
                "motivo": "Motivo", "detalle": "Detalle", "usuario": "Registrado por",
            }),
            use_container_width=True, hide_index=True,
        )


def _correcciones_bitacora(db):
    """Correcciones y eliminaciones de recepciones hechas desde '✏️ Corregir /
    eliminar recepcion', leidas de la bitacora de cambios."""
    bit = db.get_df("bitacora_cambios")
    if bit.empty or "tabla" not in bit.columns:
        return pd.DataFrame(columns=["fecha", "recepcion_id", "accion", "campo",
                                     "antes", "despues", "motivo", "usuario"])
    bit = bit[bit["tabla"].astype(str) == "recepciones_mp"].copy()
    bit = bit[bit["accion"].astype(str).isin(["edicion", "eliminacion"])]
    return pd.DataFrame({
        "fecha": bit["fecha_hora"].astype(str).str[:10],
        "recepcion_id": bit["id_registro"].astype(str),
        "accion": bit["accion"].astype(str),
        "campo": bit["campo"].astype(str),
        "antes": pd.to_numeric(bit["valor_antes"], errors="coerce"),
        "despues": pd.to_numeric(bit["valor_despues"], errors="coerce"),
        "motivo": bit["motivo"].astype(str),
        "usuario": bit["usuario"].astype(str),
    })


def _kardex_mp(db):
    """Arma un movimiento por fila con todo lo que entra y sale de la bodega
    de huevo:
      - recepciones (ingreso, con la cantidad ORIGINAL recibida),
      - correcciones de saldo hechas en 'Corregir' (ingreso o salida),
      - ajustes de inventario (ingreso o salida segun su signo),
      - consumo en produccion y perdidas (salida),
      - eliminacion de una recepcion (salida de todo lo que le quedaba).
    Asi el saldo que resulta coincide con el saldo actual de cada lote."""
    recepciones = db.get_df("recepciones_mp")
    if recepciones.empty:
        return pd.DataFrame()
    recepciones = recepciones.copy()
    recepciones["recepcion_id"] = recepciones["recepcion_id"].astype(str)
    estado = recepciones.get("estado", pd.Series("", index=recepciones.index))
    recepciones["eliminada"] = ~estado.fillna("").astype(str).str.strip().str.lower().isin(["", "activo"])
    costo_lote = dict(zip(
        recepciones["recepcion_id"],
        pd.to_numeric(recepciones["costo_cubeta"], errors="coerce").fillna(0),
    ))
    num = lambda serie: pd.to_numeric(serie, errors="coerce").fillna(0)
    costo_de = lambda rid: rid.map(costo_lote).fillna(0)
    correcciones = _correcciones_bitacora(db)
    partes = []

    # Cantidad original recibida = cubetas actuales menos lo que se corrigio
    # despues en el campo 'cubetas' (la correccion se muestra aparte).
    corr_cub = correcciones[(correcciones["accion"] == "edicion") & (correcciones["campo"] == "cubetas")]
    delta_cub = (corr_cub["despues"] - corr_cub["antes"]).fillna(0).groupby(corr_cub["recepcion_id"]).sum()
    cub_original = num(recepciones["cubetas"]) - recepciones["recepcion_id"].map(delta_cub).fillna(0)
    turnos = [_texto_turno(f) for _, f in recepciones.iterrows()]

    # prioridad: a igual fecha, primero el ingreso del lote y luego el resto
    partes.append(pd.DataFrame({
        "fecha": recepciones["fecha"], "recepcion_id": recepciones["recepcion_id"],
        "movimiento": [f"Recepción ({t.lower()})" for t in turnos],
        "ingreso": cub_original, "salida": 0.0,
        "costo_total": cub_original * costo_de(recepciones["recepcion_id"]),
        "detalle": recepciones.get("observaciones", ""), "usuario": recepciones.get("usuario", ""),
        "prioridad": 0,
    }))

    corr_saldo = correcciones[
        (correcciones["accion"] == "edicion") & (correcciones["campo"] == "cubetas_saldo")
    ]
    if not corr_saldo.empty:
        dif = (corr_saldo["despues"] - corr_saldo["antes"]).fillna(0)
        partes.append(pd.DataFrame({
            "fecha": corr_saldo["fecha"], "recepcion_id": corr_saldo["recepcion_id"],
            "movimiento": "Corrección de recepción",
            "ingreso": dif.clip(lower=0), "salida": (-dif).clip(lower=0),
            "costo_total": dif.abs() * costo_de(corr_saldo["recepcion_id"]),
            "detalle": "Saldo " + corr_saldo["antes"].map("{:g}".format) + " → "
                       + corr_saldo["despues"].map("{:g}".format) + ". Motivo: " + corr_saldo["motivo"],
            "usuario": corr_saldo["usuario"], "prioridad": 1,
        }))

    ajustes = db.get_df("mp_ajustes")
    if not ajustes.empty:
        dif = num(ajustes["diferencia"])
        rid = ajustes["recepcion_id"].astype(str)
        partes.append(pd.DataFrame({
            "fecha": ajustes["fecha"], "recepcion_id": rid,
            "movimiento": "Ajuste de inventario",
            "ingreso": dif.clip(lower=0), "salida": (-dif).clip(lower=0),
            "costo_total": dif.abs() * costo_de(rid),
            "detalle": ajustes["motivo"].astype(str) + " — " + ajustes["detalle"].astype(str),
            "usuario": ajustes["usuario"], "prioridad": 1,
        }))

    consumos = db.get_df("consumo_mp_produccion")
    if not consumos.empty:
        partes.append(pd.DataFrame({
            "fecha": consumos["fecha"], "recepcion_id": consumos["recepcion_id"].astype(str),
            "movimiento": "Consumo en producción", "ingreso": 0.0,
            "salida": num(consumos["cubetas_usadas"]),
            "costo_total": num(consumos["costo_total_aplicado"]),
            "detalle": "Lote producción: " + consumos["lote_semielaborado_id"].astype(str),
            "usuario": consumos["usuario"], "prioridad": 2,
        }))

    mermas = db.get_df("mermas_mp")
    if not mermas.empty:
        partes.append(pd.DataFrame({
            "fecha": mermas["fecha"], "recepcion_id": mermas["recepcion_id"].astype(str),
            "movimiento": "Pérdida / daño", "ingreso": 0.0,
            "salida": num(mermas["cubetas_equivalentes"]),
            "costo_total": num(mermas["costo_estimado"]),
            "detalle": mermas["causa"].astype(str) + (
                " — " + mermas["observaciones"].astype(str)
            ).where(mermas["observaciones"].astype(str).str.strip() != "", ""),
            "usuario": mermas["usuario"], "prioridad": 2,
        }))

    # Eliminaciones: fecha y motivo de la bitacora; si no quedo registro,
    # se toma la fecha anotada en observaciones ("ELIMINADA AAAA-MM-DD").
    eliminadas = recepciones[recepciones["eliminada"]]
    if not eliminadas.empty:
        elim_bit = correcciones[correcciones["accion"] == "eliminacion"].drop_duplicates(
            "recepcion_id", keep="last").set_index("recepcion_id")
        filas = []
        for _, r in eliminadas.iterrows():
            rid = r["recepcion_id"]
            if rid in elim_bit.index:
                fecha_e, motivo_e, usuario_e = (
                    elim_bit.loc[rid, "fecha"], elim_bit.loc[rid, "motivo"], elim_bit.loc[rid, "usuario"]
                )
            else:
                m = re.search(r"ELIMINADA (\d{4}-\d{2}-\d{2})", str(r.get("observaciones", "")))
                fecha_e, motivo_e, usuario_e = (m.group(1) if m else r["fecha"]), "", ""
            filas.append({
                "fecha": fecha_e, "recepcion_id": rid, "movimiento": "Eliminación de recepción",
                "ingreso": 0.0, "salida": 0.0,  # se calcula abajo con el saldo que tenia
                "costo_total": 0.0, "detalle": f"Motivo: {motivo_e}" if motivo_e else "",
                "usuario": usuario_e, "prioridad": 3,
            })
        partes.append(pd.DataFrame(filas))

    kardex = pd.concat(partes, ignore_index=True)
    kardex = kardex[kardex["recepcion_id"].isin(set(recepciones["recepcion_id"]))].copy()
    kardex["fecha"] = pd.to_datetime(kardex["fecha"], errors="coerce")
    kardex["orden"] = range(len(kardex))
    kardex = kardex.sort_values(["fecha", "prioridad", "orden"], na_position="first").reset_index(drop=True)

    # Saldos acumulados (por lote y de toda la bodega). La eliminacion saca
    # todo lo que el lote tenia en ese momento.
    saldo_lote, saldo_bodega = {}, 0.0
    col_saldo_lote, col_saldo_bodega = [], []
    for i, fila in kardex.iterrows():
        rid = fila["recepcion_id"]
        if fila["movimiento"] == "Eliminación de recepción":
            quedaba = saldo_lote.get(rid, 0.0)
            kardex.at[i, "salida"] = quedaba
            kardex.at[i, "costo_total"] = quedaba * costo_lote.get(rid, 0)
        neto = kardex.at[i, "ingreso"] - kardex.at[i, "salida"]
        saldo_lote[rid] = saldo_lote.get(rid, 0.0) + neto
        saldo_bodega += neto
        col_saldo_lote.append(saldo_lote[rid])
        col_saldo_bodega.append(saldo_bodega)
    kardex["saldo_lote"] = col_saldo_lote
    kardex["saldo_bodega"] = col_saldo_bodega

    proveedor_lote = dict(zip(recepciones["recepcion_id"], recepciones["origen_id"].astype(str)))
    prov = db.get_df("proveedores")
    nombres_prov = (
        dict(zip(prov["proveedor_id"].astype(str), prov["nombre"]))
        if not prov.empty and "proveedor_id" in prov.columns else {}
    )
    kardex["origen_id"] = kardex["recepcion_id"].map(proveedor_lote).fillna("")
    kardex["proveedor"] = kardex["origen_id"].map(lambda o: nombres_prov.get(o, o))
    lotes_eliminados = set(recepciones.loc[recepciones["eliminada"], "recepcion_id"])
    kardex["lote_eliminado"] = kardex["recepcion_id"].isin(lotes_eliminados)
    return kardex


def _render_historial(db, rol):
    """Historial de la bodega de huevo: cada movimiento con su ingreso,
    salida, saldo y costo total, filtrado por periodo."""
    kardex = _kardex_mp(db)
    if kardex.empty:
        st.info("Todavía no hay movimientos registrados en la bodega.")
        return

    c1, c2 = st.columns(2)
    desde = c1.date_input(
        "Desde", value=datetime.date.today() - datetime.timedelta(days=30), key="mp_hist_desde",
    )
    hasta = c2.date_input("Hasta", value=datetime.date.today(), key="mp_hist_hasta")
    if desde > hasta:
        st.error("La fecha 'Desde' no puede ser posterior a 'Hasta'.")
        return

    sub_graficas, sub_movimientos = st.tabs(["📊 Gráficas", "📋 Movimientos"])
    with sub_graficas:
        _render_graficas_mp(db, kardex, desde, hasta, rol)
    with sub_movimientos:
        _render_movimientos_mp(db, kardex, desde, hasta, rol)


def _render_movimientos_mp(db, kardex, desde, hasta, rol):
    """Tabla de movimientos del Historial, con filtro de proveedor y lote."""
    c3, c4 = st.columns(2)
    proveedores = sorted(kardex["proveedor"].dropna().astype(str).unique())
    prov_sel = c3.selectbox("Proveedor", ["Todos"] + proveedores, key="mp_hist_prov")
    lotes = kardex if prov_sel == "Todos" else kardex[kardex["proveedor"] == prov_sel]
    lote_sel = c4.selectbox(
        "Lote", ["Todos"] + sorted(lotes["recepcion_id"].unique(), reverse=True), key="mp_hist_lote",
    )

    periodo = kardex[(kardex["fecha"].dt.date >= desde) & (kardex["fecha"].dt.date <= hasta)]
    if prov_sel != "Todos":
        periodo = periodo[periodo["proveedor"] == prov_sel]
    if lote_sel != "Todos":
        periodo = periodo[periodo["recepcion_id"] == lote_sel]
    if periodo.empty:
        st.info("No hay movimientos en ese período.")
        return

    m = st.columns(4 if ve_costos(rol) else 2)
    m[0].metric("Ingresos (cub.)", f"{periodo['ingreso'].sum():,.2f}")
    m[1].metric("Salidas (cub.)", f"{periodo['salida'].sum():,.2f}")
    if ve_costos(rol):
        m[2].metric("Costo de ingresos ($)", f"{periodo.loc[periodo['ingreso'] > 0, 'costo_total'].sum():,.2f}")
        m[3].metric("Costo de salidas ($)", f"{periodo.loc[periodo['salida'] > 0, 'costo_total'].sum():,.2f}")

    tabla = periodo.copy()
    tabla["fecha"] = tabla["fecha"].dt.date
    columnas = [
        "fecha", "recepcion_id", "proveedor", "movimiento",
        "ingreso", "salida", "saldo_lote", "saldo_bodega",
    ]
    if ve_costos(rol):
        columnas.append("costo_total")
    columnas += ["detalle", "usuario"]
    st.dataframe(
        tabla[columnas].rename(columns={
            "fecha": "Fecha", "recepcion_id": "Lote", "proveedor": "Proveedor",
            "movimiento": "Movimiento", "ingreso": "Ingreso (cub.)", "salida": "Salida (cub.)",
            "saldo_lote": "Saldo lote (cub.)", "saldo_bodega": "Saldo bodega (cub.)",
            "costo_total": "Costo total ($)", "detalle": "Detalle", "usuario": "Usuario",
        }),
        use_container_width=True, hide_index=True,
        column_config={
            "Fecha": st.column_config.DateColumn(format="YYYY-MM-DD"),
            "Ingreso (cub.)": st.column_config.NumberColumn(format="%.2f"),
            "Salida (cub.)": st.column_config.NumberColumn(format="%.2f"),
            "Saldo lote (cub.)": st.column_config.NumberColumn(format="%.2f"),
            "Saldo bodega (cub.)": st.column_config.NumberColumn(format="%.2f"),
            "Costo total ($)": st.column_config.NumberColumn(format="%.2f"),
        },
    )
    st.caption(
        "**Saldo lote** = cubetas que le quedaban a ese lote después del movimiento. "
        "**Saldo bodega** = cubetas en toda la bodega en ese momento. Ambos se calculan con "
        "todo el histórico. La recepción muestra lo recibido originalmente; las correcciones "
        "y eliminaciones hechas después aparecen como movimientos propios. Los lotes de antes "
        "de separar por turno pueden tener varias entregas del mismo día juntas."
    )

    with st.expander("Ver tabla de recepciones (incluye eliminadas)"):
        df = db.get_df("recepciones_mp")
        if "estado" in df.columns:
            df["estado"] = df["estado"].fillna("activo").astype(str).str.strip().str.lower()
            df.loc[df["estado"] == "", "estado"] = "activo"
        if not ve_costos(rol):
            df = df.drop(columns=[c for c in ["costo_cubeta", "costo_total"] if c in df.columns])
        st.dataframe(df, use_container_width=True, hide_index=True)


# ============================ GRÁFICAS DEL HISTORIAL ============================
# Colores fijos por entidad (paleta categórica validada): la misma entidad
# lleva siempre el mismo color en todas las gráficas.
COLOR_CENTRAL = "#2a78d6"    # azul    — Planta central (La Libertad)
COLOR_SUCURSAL = "#eb6834"   # naranja — Planta sucursal (Rumipamba)
COLOR_COMPRAS = "#1baf7a"    # aqua    — compras a proveedores externos
COLOR_CONSUMO = "#6250d6"    # violeta — consumo en producción
COLOR_AJUSTE_MAS = "#2a78d6"   # azul — ajustes que suman (polo positivo)
COLOR_AJUSTE_MENOS = "#e34948" # rojo — ajustes que restan (polo negativo)
GRANULARIDAD = {"Día": "D", "Semana": "W-SUN", "Mes": "M"}


def _origen_mp(db):
    """Clasifica cada proveedor (proveedor_id) en 'central', 'sucursal' o
    'compra'. Planta central = prefijo PC (Galpones La Libertad), planta
    sucursal = prefijo PSU (Galpones Rumipamba); el resto de proveedores
    externos son compras."""
    prov = db.get_df("proveedores")
    clases = {}
    if prov.empty or "proveedor_id" not in prov.columns:
        return clases
    for _, p in prov.iterrows():
        prefijo = str(p.get("prefijo", "") or "").strip().upper()
        nombre = str(p.get("nombre", "") or "").lower()
        tipo = str(p.get("tipo", "") or "").strip().lower()
        if prefijo == "PC" or "libertad" in nombre:
            clases[str(p["proveedor_id"])] = "central"
        elif prefijo == "PSU" or "rumipamba" in nombre:
            clases[str(p["proveedor_id"])] = "sucursal"
        elif tipo == "interno":
            clases[str(p["proveedor_id"])] = "interno_otro"
        else:
            clases[str(p["proveedor_id"])] = "compra"
    return clases


def _serie_por_periodo(df, columna, desde, hasta, freq):
    """Suma 'columna' por periodo (dia/semana/mes) incluyendo los periodos
    sin movimiento en cero, para que la grafica y las estadisticas muestren
    tambien los dias en que no paso nada."""
    rango = pd.period_range(pd.Timestamp(desde), pd.Timestamp(hasta), freq=freq)
    if df.empty:
        return pd.Series(0.0, index=rango)
    sumas = df.groupby(df["fecha"].dt.to_period(freq))[columna].sum()
    return sumas.reindex(rango, fill_value=0.0).astype(float)


def _etiqueta_periodo(periodo, freq):
    if freq == "D":
        return periodo.start_time.strftime("%d/%m")
    if freq.startswith("W"):
        return "Sem " + periodo.start_time.strftime("%d/%m")
    meses = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
    return f"{meses[periodo.start_time.month - 1]} {periodo.start_time.year}"


def _layout_base(fig, alto=300, leyenda=False):
    fig.update_layout(
        height=alto, margin=dict(l=10, r=10, t=10, b=10),
        paper_bgcolor="rgba(0,0,0,0)", plot_bgcolor="rgba(0,0,0,0)",
        bargap=0.35, showlegend=leyenda,
        legend=dict(orientation="h", yanchor="bottom", y=1.0, x=0),
        hoverlabel=dict(bgcolor="white", font_size=12),
        xaxis=dict(showgrid=False, linecolor="#d9d8d3"),
        yaxis=dict(gridcolor="rgba(128,128,128,0.15)", zerolinecolor="#b5b4ae", title="Cubetas"),
    )
    try:
        fig.update_traces(marker_cornerradius=4)  # extremo redondeado (plotly >= 5.19)
    except (ValueError, TypeError):
        pass
    return fig


def _cuadro_html(celdas):
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


def _cuadro_estadistico(serie, freq_nombre, etiquetas):
    """Pequeño cuadro con el análisis estadístico de una serie por periodo."""
    import numpy as np
    unidad = {"Día": "día", "Semana": "semana", "Mes": "mes"}[freq_nombre]
    total = float(serie.sum())
    n = len(serie)
    con_mov = int((serie > 0).sum())
    promedio = total / n if n else 0.0
    prom_activos = float(serie[serie > 0].mean()) if con_mov else 0.0
    desv = float(serie.std(ddof=1)) if n > 1 else 0.0
    cv = (desv / promedio * 100) if promedio else 0.0
    if con_mov:
        i_max = int(np.argmax(serie.values))
        maximo = f"{serie.iloc[i_max]:,.0f} ({etiquetas[i_max]})"
    else:
        maximo = "—"
    if n >= 3 and con_mov >= 3:
        pendiente = float(np.polyfit(np.arange(n), serie.values, 1)[0])
        if abs(pendiente) < 0.05 * max(promedio, 1e-9):
            tendencia = "→ estable"
        else:
            tendencia = f"{'↑ sube' if pendiente > 0 else '↓ baja'} {abs(pendiente):,.1f} cub./{unidad}"
    else:
        tendencia = "—"
    _cuadro_html([
        ("Total", f"<b>{total:,.0f}</b> cub."),
        (f"Promedio/{unidad}", f"{promedio:,.1f} cub."),
        (f"{unidad.capitalize()}s con mov.", f"{con_mov} de {n} (prom. {prom_activos:,.1f})"),
        ("Máximo", maximo),
        ("Variabilidad", f"±{desv:,.1f} (CV {cv:,.0f}%)"),
        ("Tendencia", tendencia),
    ])


def _grafica_serie(titulo, df, columna, color, desde, hasta, freq, freq_nombre, vacio):
    import plotly.graph_objects as go
    st.markdown(f"##### {titulo}")
    serie = _serie_por_periodo(df, columna, desde, hasta, freq)
    etiquetas = [_etiqueta_periodo(p, freq) for p in serie.index]
    if serie.sum() == 0:
        st.info(vacio)
        return
    fig = go.Figure(go.Bar(
        x=etiquetas, y=serie.values, marker_color=color,
        hovertemplate="%{x}<br><b>%{y:,.2f}</b> cubetas<extra></extra>",
    ))
    # etiqueta solo en el valor más alto (no un número en cada barra)
    i_max = int(serie.values.argmax())
    fig.add_annotation(
        x=etiquetas[i_max], y=serie.iloc[i_max], text=f"{serie.iloc[i_max]:,.0f}",
        showarrow=False, yshift=10, font=dict(size=11, color="#52514e"),
    )
    st.plotly_chart(_layout_base(fig), use_container_width=True)
    _cuadro_estadistico(serie, freq_nombre, etiquetas)


def _render_graficas_mp(db, kardex, desde, hasta, rol):
    """Panel de gráficas del Historial: consumo, ingresos de cada planta,
    compras (totales y por proveedor) y ajustes, con su análisis."""
    import plotly.graph_objects as go

    dias = (hasta - desde).days + 1
    sugerida = "Día" if dias <= 45 else ("Semana" if dias <= 200 else "Mes")
    freq_nombre = st.radio(
        "Agrupar por", list(GRANULARIDAD), index=list(GRANULARIDAD).index(sugerida),
        horizontal=True, key="mp_graf_freq",
    )
    freq = GRANULARIDAD[freq_nombre]

    # Las recepciones eliminadas fueron errores de registro: no cuentan.
    k = kardex[
        ~kardex["lote_eliminado"]
        & (kardex["fecha"].dt.date >= desde) & (kardex["fecha"].dt.date <= hasta)
    ].copy()
    clases = _origen_mp(db)
    k["origen"] = k["origen_id"].astype(str).map(clases).fillna("compra")
    recep = k[k["movimiento"].str.startswith("Recepción")]
    consumo = k[k["movimiento"] == "Consumo en producción"]
    ajustes = k[k["movimiento"].isin(["Ajuste de inventario", "Corrección de recepción"])]
    compras = recep[recep["origen"] == "compra"]

    # ---- resumen rápido ----
    r = st.columns(4)
    r[0].metric("Consumo en producción", f"{consumo['salida'].sum():,.0f} cub.")
    r[1].metric("Planta central", f"{recep.loc[recep['origen'] == 'central', 'ingreso'].sum():,.0f} cub.")
    r[2].metric("Planta sucursal", f"{recep.loc[recep['origen'] == 'sucursal', 'ingreso'].sum():,.0f} cub.")
    r[3].metric("Compras", f"{compras['ingreso'].sum():,.0f} cub.")

    g1, g2 = st.columns(2)
    with g1:
        _grafica_serie(
            "🏭 Consumo de huevo en producción", consumo, "salida", COLOR_CONSUMO,
            desde, hasta, freq, freq_nombre, "No hubo consumo en producción en este período.",
        )
    with g2:
        _grafica_serie(
            "🛒 Compras totales (proveedores externos)", compras, "ingreso", COLOR_COMPRAS,
            desde, hasta, freq, freq_nombre, "No hubo compras a proveedores externos en este período.",
        )
    g3, g4 = st.columns(2)
    with g3:
        _grafica_serie(
            "🐔 Ingresos Planta central (La Libertad)", recep[recep["origen"] == "central"], "ingreso",
            COLOR_CENTRAL, desde, hasta, freq, freq_nombre,
            "No hubo ingresos de Planta central en este período.",
        )
    with g4:
        _grafica_serie(
            "🐔 Ingresos Planta sucursal (Rumipamba)", recep[recep["origen"] == "sucursal"], "ingreso",
            COLOR_SUCURSAL, desde, hasta, freq, freq_nombre,
            "No hubo ingresos de Planta sucursal en este período.",
        )

    g5, g6 = st.columns(2)
    # ---- compras por proveedor ----
    with g5:
        st.markdown("##### 🛒 Compras por proveedor")
        if compras.empty:
            st.info("No hubo compras a proveedores externos en este período.")
        else:
            por_prov = compras.groupby("proveedor").agg(
                cubetas=("ingreso", "sum"), costo=("costo_total", "sum"), entregas=("ingreso", "count"),
            ).sort_values("cubetas")
            por_prov["costo_cub"] = por_prov["costo"] / por_prov["cubetas"].where(por_prov["cubetas"] > 0)
            hover = "%{y}<br><b>%{x:,.0f}</b> cubetas en %{customdata[0]} entregas"
            if ve_costos(rol):
                hover += "<br>Costo: $%{customdata[1]:,.2f} ($%{customdata[2]:,.2f}/cub.)"
            fig = go.Figure(go.Bar(
                x=por_prov["cubetas"], y=por_prov.index, orientation="h", marker_color=COLOR_COMPRAS,
                customdata=por_prov[["entregas", "costo", "costo_cub"]].values,
                text=por_prov["cubetas"].map("{:,.0f}".format), textposition="outside",
                textfont=dict(color="#52514e"), cliponaxis=False,
                hovertemplate=hover + "<extra></extra>",
            ))
            fig = _layout_base(fig, alto=max(220, 50 * len(por_prov) + 60))
            fig.update_layout(xaxis=dict(title="Cubetas", showgrid=True,
                                         gridcolor="rgba(128,128,128,0.15)"),
                              yaxis=dict(title=None, gridcolor="rgba(0,0,0,0)"),
                              margin=dict(l=10, r=50, t=10, b=10))
            st.plotly_chart(fig, use_container_width=True)
            total = por_prov["cubetas"].sum()
            principal = por_prov["cubetas"].idxmax()
            celdas = [
                ("Total comprado", f"<b>{total:,.0f}</b> cub."),
                ("Proveedores", f"{len(por_prov)}"),
                ("Entregas", f"{int(por_prov['entregas'].sum())}"),
                ("Principal", f"{escape(str(principal))} ({por_prov.loc[principal, 'cubetas'] / total * 100:,.0f}%)"),
                ("Prom./entrega", f"{total / por_prov['entregas'].sum():,.1f} cub."),
            ]
            if ve_costos(rol):
                costo_total = por_prov["costo"].sum()
                celdas += [
                    ("Costo total", f"${costo_total:,.2f}"),
                    ("Costo prom./cub.", f"${costo_total / total:,.2f}" if total else "—"),
                ]
            _cuadro_html(celdas)

    # ---- ajustes ----
    with g6:
        st.markdown("##### ⚖️ Ajustes de inventario y correcciones")
        if ajustes.empty:
            st.info("No hubo ajustes ni correcciones en este período.")
        else:
            mas = _serie_por_periodo(ajustes, "ingreso", desde, hasta, freq)
            menos = _serie_por_periodo(ajustes, "salida", desde, hasta, freq)
            etiquetas = [_etiqueta_periodo(p, freq) for p in mas.index]
            fig = go.Figure([
                go.Bar(x=etiquetas, y=mas.values, name="Agregadas", marker_color=COLOR_AJUSTE_MAS,
                       hovertemplate="%{x}<br>Agregadas: <b>%{y:,.2f}</b> cub.<extra></extra>"),
                go.Bar(x=etiquetas, y=-menos.values, name="Quitadas", marker_color=COLOR_AJUSTE_MENOS,
                       customdata=menos.values,
                       hovertemplate="%{x}<br>Quitadas: <b>%{customdata:,.2f}</b> cub.<extra></extra>"),
            ])
            fig = _layout_base(fig, leyenda=True)
            fig.update_layout(barmode="relative")
            st.plotly_chart(fig, use_container_width=True)
            agregadas, quitadas = float(mas.sum()), float(menos.sum())
            ingresado = float(recep["ingreso"].sum())
            neto = agregadas - quitadas
            pct = f"{abs(neto) / ingresado * 100:,.1f}%" if ingresado else "—"
            motivos = ajustes.loc[ajustes["movimiento"] == "Ajuste de inventario", "detalle"].astype(str)
            motivo_top = motivos.str.split(" — ").str[0].mode()
            motivo_txt = motivo_top.iloc[0] if not motivo_top.empty else "Corrección de recepción"
            _cuadro_html([
                ("Registros", f"{len(ajustes)}"),
                ("Agregadas", f"+{agregadas:,.0f} cub."),
                ("Quitadas", f"−{quitadas:,.0f} cub."),
                ("Neto", f"<b>{neto:+,.0f}</b> cub."),
                ("Neto vs. ingresos", pct),
                ("Motivo más frecuente", escape(str(motivo_txt))),
            ])

    st.caption(
        "Las gráficas usan solo el período Desde/Hasta. Planta central = proveedor con prefijo PC "
        "(La Libertad); Planta sucursal = prefijo PSU (Rumipamba); compras = proveedores externos. "
        "Las recepciones eliminadas no se cuentan. **Variabilidad** = desviación estándar por "
        "período (CV = qué tanto varía respecto al promedio). **Tendencia** = pendiente de la "
        "recta de mejor ajuste."
    )
