"""
Supervisión y calidad — ahora delega el registro de horas al módulo
centralizado "Personal y turnos". Este módulo queda para registrar
observaciones de calidad del turno (temperatura, pH, incidentes, etc.)
"""
import datetime
import streamlit as st
import pandas as pd


def render(db, username, rol):
    st.title("📋 Supervisión y calidad")
    st.info(
        "ℹ️ El registro de horas del personal de supervisión ahora se hace en "
        "**👥 Personal y turnos** en el menú lateral — se enlaza por fecha y turno."
    )
    st.markdown("### 📝 Observaciones de calidad del turno")
    st.caption("Registra incidencias, temperaturas, pH u otras observaciones del turno de supervisión.")

    c1, c2 = st.columns(2)
    fecha = c1.date_input("Fecha", value=datetime.date.today())
    turno = c2.text_input("Turno / responsable")

    observacion = st.text_area("Observación del turno", height=150)
    if st.button("💾 Guardar observación", type="primary"):
        if not observacion.strip():
            st.error("Escribe al menos una observación antes de guardar.")
        else:
            db.append_row("supervision_diaria", {
                "registro_id": db.siguiente_id("supervision_diaria", "SUP", fecha),
                "fecha": fecha.isoformat(),
                "personal_id": turno,
                "hora_entrada": "",
                "hora_salida": "",
                "horas": 0,
                "horas_nocturnas": 0,
                "costo_calculado": 0,
                "usuario": username,
                "observaciones": observacion,
            })
            st.success("✅ Observación guardada.")
            st.rerun()

    st.divider()
    st.markdown("### 📋 Historial de observaciones")
    superv = db.get_df("supervision_diaria")
    if superv.empty:
        st.info("No hay observaciones registradas todavía.")
    else:
        superv_con_obs = superv[superv["observaciones"].astype(str).str.strip() != ""]
        if superv_con_obs.empty:
            st.info("No hay observaciones de calidad registradas todavía.")
        else:
            st.dataframe(
                superv_con_obs[["fecha","personal_id","observaciones"]].sort_values("fecha", ascending=False),
                use_container_width=True, hide_index=True,
            )
