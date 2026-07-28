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


def _buscar_lote_del_dia(db, prefijo, fecha):
    """Busca si ya existe una recepcion activa con el mismo lote del dia
    (para consolidarla en lugar de crear una nueva). Devuelve la fila o None."""
    lote_esperado = _generar_lote(prefijo, fecha)
    activas = _recepciones_activas(db)
    if activas.empty or "recepcion_id" not in activas.columns:
        return None
    match = activas[activas["recepcion_id"].astype(str) == lote_esperado]
    if match.empty:
        return None
    return match.iloc[0]


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

    tab_recepcion, tab_inventario, tab_historial, tab_perdida, tab_corregir = st.tabs(
        ["Registrar recepción", "Inventario actual", "Historial",
         "⚠️ Registrar pérdida/daño", "✏️ Corregir / eliminar recepción"]
    )

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
                "Solo elige proveedor, cubetas y costo — el sistema genera el "
                "código de lote automáticamente (prefijo+fecha, ej. **PC270726**). "
                "Si el mismo proveedor entrega dos veces el mismo día, las "
                "cubetas se consolidan en un solo lote (con costo promedio "
                "ponderado)."
            )

            # Preview del lote que se va a generar (fuera del form para que
            # se actualice en vivo al cambiar proveedor o fecha)
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
            fecha = st.date_input("Fecha de recepción", value=datetime.date.today(), key="rec_fecha")

            fila_prov = con_prefijo.set_index("proveedor_id").loc[proveedor_id]
            prefijo_sel = str(fila_prov["prefijo"]).strip().upper()
            lote_preview = _generar_lote(prefijo_sel, fecha)
            existente = _buscar_lote_del_dia(db, prefijo_sel, fecha)

            if existente is not None:
                cub_prev = float(pd.to_numeric(existente.get("cubetas", 0), errors="coerce") or 0)
                costo_prev = float(pd.to_numeric(existente.get("costo_cubeta", 0), errors="coerce") or 0)
                st.info(
                    f"📦 Ya existe el lote **{lote_preview}** con **{cub_prev:.0f} cubetas** "
                    f"a costo ${costo_prev:.2f}/cub. "
                    f"Las cubetas que ingreses se **sumarán a este lote** y el costo "
                    f"se recalculará como promedio ponderado."
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
                guardar = st.form_submit_button("Registrar recepción")

            if guardar:
                if cubetas <= 0:
                    st.error("Ingresa una cantidad mayor a cero.")
                else:
                    costo_total = cubetas * costo_cubeta
                    if existente is not None:
                        # Consolidacion: sumar cubetas al lote existente y
                        # recalcular costo promedio ponderado.
                        cub_prev = float(pd.to_numeric(existente.get("cubetas", 0), errors="coerce") or 0)
                        saldo_prev = float(pd.to_numeric(existente.get("cubetas_saldo", 0), errors="coerce") or 0)
                        costo_prev = float(pd.to_numeric(existente.get("costo_cubeta", 0), errors="coerce") or 0)
                        cub_total = cub_prev + cubetas
                        saldo_total = saldo_prev + cubetas
                        costo_prom = (
                            (cub_prev * costo_prev + cubetas * costo_cubeta) / cub_total
                            if cub_total > 0 else costo_cubeta
                        )
                        obs_prev = str(existente.get("observaciones", "") or "")
                        obs_consolidada = (
                            (obs_prev + " | " if obs_prev else "")
                            + f"Consolidacion {fecha.isoformat()}: +{cubetas:.0f} cub. @ ${costo_cubeta:.2f} "
                            + f"por {username}. "
                            + (observaciones or "").strip()
                        ).strip(" |")
                        db.update_row("recepciones_mp", "recepcion_id", lote_preview, {
                            "cubetas": cub_total,
                            "cubetas_saldo": saldo_total,
                            "costo_cubeta": round(costo_prom, 4),
                            "costo_total": round(cub_total * costo_prom, 2),
                            "observaciones": obs_consolidada,
                        })
                        st.success(
                            f"✅ Lote **{lote_preview}** consolidado: total {cub_total:.0f} cub. "
                            f"@ ${costo_prom:.4f}/cub. (promedio ponderado)"
                        )
                    else:
                        # Recepcion nueva.
                        db.append_row("recepciones_mp", {
                            "recepcion_id": lote_preview,
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
                        })
                        st.success(
                            f"✅ Recepción **{lote_preview}** registrada — "
                            f"{cubetas:.0f} cub. @ ${costo_cubeta:.2f}/cub. "
                            f"(total ${costo_total:,.2f})"
                        )
                    st.rerun()

    # ======================== INVENTARIO ACTUAL ========================
    with tab_inventario:
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

                columnas_inv = ["recepcion_id", "fecha", "proveedor_nombre", "cubetas_saldo"]
                if "tipo" in inventario.columns:
                    columnas_inv.insert(3, "tipo")
                if ve_costos(rol):
                    columnas_inv += ["costo_cubeta", "valor_saldo"]
                columnas_inv = [c for c in columnas_inv if c in inventario.columns]
                st.dataframe(inventario[columnas_inv], use_container_width=True, hide_index=True)

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
        df = db.get_df("recepciones_mp")
        if df.empty:
            st.info("Sin recepciones registradas.")
        else:
            # Marcar eliminadas visualmente
            if "estado" in df.columns:
                df["estado"] = df["estado"].fillna("activo").astype(str).str.strip().str.lower()
                df.loc[df["estado"] == "", "estado"] = "activo"
            if not ve_costos(rol):
                df = df.drop(columns=[c for c in ["costo_cubeta", "costo_total"] if c in df.columns])
            st.dataframe(df, use_container_width=True, hide_index=True)

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
                        db.update_row("recepciones_mp", "recepcion_id", recepcion_id, {
                            "cubetas_saldo": cubetas_saldo_lote - cubetas_equivalentes,
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
