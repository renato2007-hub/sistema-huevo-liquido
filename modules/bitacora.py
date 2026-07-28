"""
Bitacora de cambios (solo lectura): visor con filtros por fecha, usuario,
tabla y accion, y exportacion a CSV. Solo admin y gerencia lo ven.
"""
import io
import datetime
import streamlit as st
import pandas as pd


def render(db, username, rol):
    st.title("📜 Bitácora de cambios")
    st.caption(
        "Registro de acciones destructivas (ediciones, eliminaciones, "
        "cancelaciones) hechas por los usuarios en el sistema. Se conserva "
        "de forma indefinida para auditoría."
    )

    df = db.get_df("bitacora_cambios")
    if df.empty:
        st.info("No hay eventos registrados todavía.")
        return

    df = df.copy()
    df["fecha_hora"] = pd.to_datetime(df["fecha_hora"], errors="coerce")
    df = df.sort_values("fecha_hora", ascending=False)

    # ---- Filtros ----
    c1, c2, c3, c4 = st.columns(4)
    hoy = datetime.date.today()
    desde = c1.date_input("Desde", value=hoy - datetime.timedelta(days=30), key="bit_desde")
    hasta = c2.date_input("Hasta", value=hoy, key="bit_hasta")
    usuarios = ["Todos"] + sorted(df["usuario"].dropna().astype(str).unique().tolist())
    filtro_usuario = c3.selectbox("Usuario", usuarios, key="bit_user")
    tablas = ["Todas"] + sorted(df["tabla"].dropna().astype(str).unique().tolist())
    filtro_tabla = c4.selectbox("Tabla / módulo", tablas, key="bit_tabla")

    c5, c6 = st.columns(2)
    acciones = ["Todas"] + sorted(df["accion"].dropna().astype(str).unique().tolist())
    filtro_accion = c5.selectbox("Acción", acciones, key="bit_accion")
    filtro_texto = c6.text_input("Buscar texto libre (ID, motivo, etc.)", "", key="bit_texto")

    # ---- Aplicar filtros ----
    df_v = df[
        (df["fecha_hora"].dt.date >= desde)
        & (df["fecha_hora"].dt.date <= hasta)
    ]
    if filtro_usuario != "Todos":
        df_v = df_v[df_v["usuario"].astype(str) == filtro_usuario]
    if filtro_tabla != "Todas":
        df_v = df_v[df_v["tabla"].astype(str) == filtro_tabla]
    if filtro_accion != "Todas":
        df_v = df_v[df_v["accion"].astype(str) == filtro_accion]
    if filtro_texto.strip():
        pat = filtro_texto.strip().lower()
        df_v = df_v[
            df_v.apply(
                lambda r: any(pat in str(r[c]).lower() for c in
                              ["id_registro", "motivo", "valor_antes",
                               "valor_despues", "campo", "modulo"]),
                axis=1,
            )
        ]

    # ---- Resumen ----
    st.metric("Eventos en el filtro", f"{len(df_v):,}")

    if df_v.empty:
        st.info("No hay eventos con los filtros seleccionados.")
        return

    # ---- Tabla ----
    cols_mostrar = ["fecha_hora", "usuario", "modulo", "tabla",
                    "id_registro", "accion", "campo",
                    "valor_antes", "valor_despues", "motivo"]
    cols_mostrar = [c for c in cols_mostrar if c in df_v.columns]
    st.dataframe(df_v[cols_mostrar], use_container_width=True, hide_index=True)

    # ---- Exportar CSV ----
    csv_buf = io.StringIO()
    df_v[cols_mostrar].to_csv(csv_buf, index=False)
    st.download_button(
        "⬇️ Descargar CSV",
        data=csv_buf.getvalue().encode("utf-8"),
        file_name=f"bitacora_{desde.isoformat()}_a_{hasta.isoformat()}.csv",
        mime="text/csv",
    )
