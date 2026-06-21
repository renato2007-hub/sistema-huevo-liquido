"""
Supervision y calidad: registro diario de horas del Jefe de planta, Jefe de
control de calidad, u otro personal de supervision que NO se dedica a un
solo lote sino que supervisa la jornada completa. A diferencia del personal
de "Produccion de semielaborados", este costo NO se reparte entre los lotes
-- se contabiliza aparte, como costo de supervision del dia, y se muestra
por separado en el Dashboard (no se mezcla en el costo/kg de cada lote).
Sus horas si se incluyen en el reporte de horas normales/extras/dobles/
nocturnas del Dashboard, junto con el resto del personal.
"""
import datetime
import streamlit as st
import pandas as pd
from utils.horas_trabajo import calcular_horas_sesion


def render(db, username, rol):
    st.title("👔 Supervisión y calidad")
    st.caption(
        "Para personal que supervisa la jornada completa (Jefe de planta, "
        "Jefe de control de calidad, etc.), no un lote específico. Su costo "
        "se contabiliza aparte, no se mezcla en el costo por kg de cada lote."
    )
    tab_nueva, tab_historial = st.tabs(["➕ Registrar jornada", "📋 Historial"])

    personal = db.get_df("personal")

    with tab_nueva:
        if personal.empty:
            st.warning("Configura al menos una persona en Catálogos → Personal antes de registrar.")
            return

        fecha = st.date_input("Fecha", value=datetime.date.today(), key="superv_fecha")
        opciones_personal_nombres = list(personal["nombre"])
        mapa_nombre_a_personal_id = dict(zip(personal["nombre"], personal["personal_id"]))

        personas_seleccionadas = st.multiselect(
            "Personas que trabajaron esta jornada", opciones_personal_nombres, key="personas_seleccionadas_superv",
        )
        filas_personal_horas = []
        for nombre_persona in personas_seleccionadas:
            c1, c2, c3 = st.columns([2, 1, 1])
            c1.markdown(f"**{nombre_persona}**")
            entrada_p = c2.time_input("Hora entrada", value=None, key=f"superv_entrada_{nombre_persona}")
            salida_p = c3.time_input("Hora salida", value=None, key=f"superv_salida_{nombre_persona}")
            filas_personal_horas.append({"nombre": nombre_persona, "hora_entrada": entrada_p, "hora_salida": salida_p})

        observaciones = st.text_area("Observaciones", "", key="superv_obs")

        if st.button("💾 Guardar jornada de supervisión"):
            registros_guardados = []
            for fila in filas_personal_horas:
                nombre_seleccionado = fila["nombre"]
                personal_id_real = mapa_nombre_a_personal_id.get(nombre_seleccionado, nombre_seleccionado)
                if fila["hora_entrada"] is None or fila["hora_salida"] is None:
                    st.error(f"Falta hora de entrada o salida para {nombre_seleccionado}. Completa ambas.")
                    return
                costo_hora = float(personal.set_index("personal_id").loc[personal_id_real, "costo_hora"])
                horas, horas_nocturnas = calcular_horas_sesion(fila["hora_entrada"], fila["hora_salida"], fecha)
                registro_id = db.siguiente_id("supervision_diaria", "SUP", fecha)
                db.append_row("supervision_diaria", {
                    "registro_id": registro_id,
                    "fecha": fecha.isoformat(),
                    "personal_id": personal_id_real,
                    "hora_entrada": fila["hora_entrada"].strftime("%H:%M"),
                    "hora_salida": fila["hora_salida"].strftime("%H:%M"),
                    "horas": horas,
                    "horas_nocturnas": horas_nocturnas,
                    "costo_calculado": costo_hora * horas,
                    "usuario": username,
                    "observaciones": observaciones,
                })
                registros_guardados.append(registro_id)

            if not registros_guardados:
                st.error("Agrega al menos una persona con su hora de entrada y salida.")
            else:
                st.success(f"{len(registros_guardados)} registro(s) guardado(s): {', '.join(registros_guardados)}")
                st.rerun()

    with tab_historial:
        df = db.get_df("supervision_diaria")
        if df.empty:
            st.info("No hay registros todavía.")
        else:
            if not personal.empty:
                df = df.merge(
                    personal[["personal_id", "nombre", "cargo"]], on="personal_id", how="left",
                )
            st.dataframe(
                df[[c for c in [
                    "fecha", "nombre", "cargo", "hora_entrada", "hora_salida",
                    "horas", "horas_nocturnas", "costo_calculado", "observaciones",
                ] if c in df.columns]].sort_values("fecha", ascending=False),
                use_container_width=True, hide_index=True,
            )
