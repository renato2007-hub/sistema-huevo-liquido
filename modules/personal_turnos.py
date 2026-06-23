"""
Módulo centralizado de Personal y turnos.
Registra la jornada de todo el personal (operarios, calidad, jefe de planta)
por fecha y turno. La trazabilidad se enlaza por fecha+turno, no por lote.
"""
import datetime
import streamlit as st
import pandas as pd

from utils.horas_trabajo import calcular_horas_sesion, clasificar_horas_por_dia, feriados_como_set, compensaciones_como_set
from utils.pdf_horas_personal import generar_pdf_horas_personal
from utils.permisos import ve_costos


def render(db, username, rol):
    st.title("👥 Personal y turnos")

    tab_registrar, tab_historial, tab_reporte = st.tabs([
        "📋 Registrar jornada", "📊 Historial de horas", "📄 Reporte PDF",
    ])

    personal = db.get_df("personal")
    turnos   = db.get_df("turnos")
    feriados = feriados_como_set(db.get_df("feriados"))
    compensaciones = compensaciones_como_set(db.get_df("compensaciones_feriado"))

    # ======================== REGISTRAR JORNADA ========================
    with tab_registrar:
        if personal.empty:
            st.warning("Configura el personal en Catálogos → Personal antes de registrar jornadas.")
        else:
            with st.container(border=True):
                st.markdown("##### 📅 Datos del turno")
                c1, c2 = st.columns(2)
                fecha = c1.date_input("Fecha", value=datetime.date.today(), key="pt_fecha")
                turno_id = c2.selectbox(
                    "Turno",
                    turnos["turno_id"] if not turnos.empty else ["Sin turno"],
                    format_func=lambda x: turnos.set_index("turno_id").loc[x, "nombre"] if not turnos.empty and x in list(turnos["turno_id"]) else x,
                    key="pt_turno",
                )
                observaciones = st.text_input("Observaciones (opcional)", "", key="pt_obs")

            st.markdown("##### 👷 Personal que trabajó esta jornada")
            st.caption("Selecciona a las personas que trabajaron, ingresa su hora de entrada y salida.")

            # Agrupar personal por cargo para mayor claridad
            if "cargo" in personal.columns:
                cargos = sorted(personal["cargo"].dropna().unique().tolist())
            else:
                cargos = ["Personal"]

            opciones_nombres = list(personal["nombre"])
            mapa_nombre_a_id = dict(zip(personal["nombre"], personal["personal_id"]))
            mapa_id_a_costo  = dict(zip(personal["personal_id"], pd.to_numeric(personal["costo_hora"], errors="coerce").fillna(0)))

            personas_sel = st.multiselect(
                "Personas que trabajaron esta jornada",
                opciones_nombres, key="pt_personas",
            )

            filas_horas = []
            for nombre in personas_sel:
                c1, c2, c3 = st.columns([2, 1, 1])
                c1.markdown(f"**{nombre}**")
                entrada = c2.time_input("Entrada", value=None, key=f"pt_entrada_{nombre}")
                salida  = c3.time_input("Salida",  value=None, key=f"pt_salida_{nombre}")
                filas_horas.append({"nombre": nombre, "hora_entrada": entrada, "hora_salida": salida})

            if personas_sel:
                if st.button("💾 Guardar jornada", type="primary", use_container_width=True):
                    errores = [f["nombre"] for f in filas_horas if not f["hora_entrada"] or not f["hora_salida"]]
                    if errores:
                        st.error(f"Faltan horas para: {', '.join(errores)}")
                    else:
                        guardados = []
                        for fila in filas_horas:
                            personal_id = mapa_nombre_a_id.get(fila["nombre"], fila["nombre"])
                            costo_hora  = mapa_id_a_costo.get(personal_id, 0)
                            horas, horas_noct = calcular_horas_sesion(fila["hora_entrada"], fila["hora_salida"], fecha)
                            jornada_id = db.siguiente_id("jornadas_personal", "JP", fecha)
                            db.append_row("jornadas_personal", {
                                "jornada_id":      jornada_id,
                                "fecha":           fecha.isoformat(),
                                "turno_id":        turno_id,
                                "personal_id":     personal_id,
                                "hora_entrada":    fila["hora_entrada"].strftime("%H:%M"),
                                "hora_salida":     fila["hora_salida"].strftime("%H:%M"),
                                "horas":           horas,
                                "horas_nocturnas": horas_noct,
                                "costo_calculado": costo_hora * horas,
                                "usuario":         username,
                                "observaciones":   observaciones,
                            })
                            guardados.append(fila["nombre"])
                        st.success(f"✅ Jornada guardada para {len(guardados)} persona(s) — fecha {fecha}, turno {turno_id}.")
                        st.rerun()

    # ======================== HISTORIAL DE HORAS ========================
    with tab_historial:
        jornadas = db.get_df("jornadas_personal")
        if jornadas.empty:
            st.info("No hay jornadas registradas todavía.")
        else:
            jornadas["horas"]            = pd.to_numeric(jornadas["horas"], errors="coerce").fillna(0)
            jornadas["horas_nocturnas"]  = pd.to_numeric(jornadas["horas_nocturnas"], errors="coerce").fillna(0)
            jornadas["costo_calculado"]  = pd.to_numeric(jornadas["costo_calculado"], errors="coerce").fillna(0)

            # Join con nombres de personal
            if not personal.empty:
                jornadas = jornadas.merge(
                    personal[["personal_id", "nombre", "cargo"]],
                    on="personal_id", how="left",
                )
                jornadas["nombre"] = jornadas["nombre"].fillna(jornadas["personal_id"])
            else:
                jornadas["nombre"] = jornadas["personal_id"]
                jornadas["cargo"]  = ""

            # Join con nombre de turno
            if not turnos.empty:
                jornadas = jornadas.merge(
                    turnos[["turno_id", "nombre"]].rename(columns={"nombre": "turno_nombre"}),
                    on="turno_id", how="left",
                )
            else:
                jornadas["turno_nombre"] = jornadas["turno_id"]

            hoy = datetime.date.today()
            c1, c2, c3 = st.columns(3)
            desde = c1.date_input("Desde", value=hoy - datetime.timedelta(days=30), key="hist_desde")
            hasta = c2.date_input("Hasta", value=hoy, key="hist_hasta")
            personas_filtro = sorted(jornadas["nombre"].dropna().unique().tolist())
            filtro_persona = c3.selectbox("Persona", ["Todas"] + personas_filtro, key="hist_persona")

            df_filtrado = jornadas[
                (jornadas["fecha"].astype(str) >= desde.isoformat()) &
                (jornadas["fecha"].astype(str) <= hasta.isoformat())
            ]
            if filtro_persona != "Todas":
                df_filtrado = df_filtrado[df_filtrado["nombre"] == filtro_persona]

            if df_filtrado.empty:
                st.info("No hay registros en ese período.")
            else:
                # Clasificar horas
                por_dia = df_filtrado.groupby(["personal_id","fecha"])["horas"].sum().reset_index()
                por_dia = clasificar_horas_por_dia(por_dia, feriados, compensaciones)
                resumen = por_dia.groupby("personal_id").agg(
                    h_normales=("horas_normales","sum"),
                    h_extras=("horas_extras","sum"),
                    h_dobles=("horas_dobles","sum"),
                    h_compensadas=("horas_compensadas","sum"),
                ).reset_index()

                # Métricas resumen
                total_horas   = df_filtrado["horas"].sum()
                total_noct    = df_filtrado["horas_nocturnas"].sum()
                total_costo   = df_filtrado["costo_calculado"].sum()
                m1, m2, m3 = st.columns(3)
                m1.metric("Total horas", f"{total_horas:,.1f} h")
                m2.metric("Horas nocturnas", f"{total_noct:,.1f} h")
                if ve_costos(rol):
                    m3.metric("Costo mano de obra", f"${total_costo:,.2f}")

                st.write("")
                cols_mostrar = ["fecha", "nombre", "cargo", "turno_nombre",
                                "hora_entrada", "hora_salida", "horas", "horas_nocturnas"]
                if ve_costos(rol):
                    cols_mostrar.append("costo_calculado")
                st.dataframe(
                    df_filtrado[[c for c in cols_mostrar if c in df_filtrado.columns]].sort_values(["fecha","nombre"]),
                    use_container_width=True, hide_index=True,
                )

    # ======================== REPORTE PDF ========================
    with tab_reporte:
        jornadas_pdf = db.get_df("jornadas_personal")
        if jornadas_pdf.empty:
            st.info("No hay jornadas registradas todavía.")
        else:
            jornadas_pdf["horas"]           = pd.to_numeric(jornadas_pdf["horas"], errors="coerce").fillna(0)
            jornadas_pdf["horas_nocturnas"] = pd.to_numeric(jornadas_pdf["horas_nocturnas"], errors="coerce").fillna(0)
            jornadas_pdf["costo_calculado"] = pd.to_numeric(jornadas_pdf["costo_calculado"], errors="coerce").fillna(0)
            if not personal.empty:
                jornadas_pdf = jornadas_pdf.merge(
                    personal[["personal_id","nombre","cargo"]], on="personal_id", how="left",
                )
                jornadas_pdf["nombre"] = jornadas_pdf["nombre"].fillna(jornadas_pdf["personal_id"])

            hoy = datetime.date.today()
            c1, c2 = st.columns(2)
            desde_pdf = c1.date_input("Desde", value=hoy - datetime.timedelta(days=6), key="pdf_desde")
            hasta_pdf = c2.date_input("Hasta", value=hoy, key="pdf_hasta")

            df_pdf = jornadas_pdf[
                (jornadas_pdf["fecha"].astype(str) >= desde_pdf.isoformat()) &
                (jornadas_pdf["fecha"].astype(str) <= hasta_pdf.isoformat())
            ]

            if not personal.empty:
                activos = personal.copy()
                activos["nombre"] = activos["nombre"].astype(str)
                per_pdf = activos.set_index("personal_id")
                if not df_pdf.empty:
                    horas_por_id = df_pdf.groupby("personal_id")["horas"].sum()
                    costo_por_id = df_pdf.groupby("personal_id")["costo_calculado"].sum()
                    por_dia_pdf  = df_pdf.groupby(["personal_id","fecha"])["horas"].sum().reset_index()
                    por_dia_pdf  = clasificar_horas_por_dia(por_dia_pdf, feriados, compensaciones)
                    clas_id      = por_dia_pdf.groupby("personal_id").agg(
                        horas_normales=("horas_normales","sum"), horas_extras=("horas_extras","sum"),
                        horas_dobles=("horas_dobles","sum"), horas_compensadas=("horas_compensadas","sum"),
                    )
                    per_pdf = per_pdf.join(clas_id, how="left")
                    per_pdf["horas_totales"]    = per_pdf.index.map(horas_por_id).fillna(0)
                    per_pdf["horas_nocturnas"]  = per_pdf.index.map(df_pdf.groupby("personal_id")["horas_nocturnas"].sum()).fillna(0)
                    per_pdf["costo"]            = per_pdf.index.map(costo_por_id).fillna(0)
                else:
                    for col in ["horas_normales","horas_extras","horas_dobles","horas_compensadas","horas_totales","horas_nocturnas","costo"]:
                        per_pdf[col] = 0
                for col in ["horas_normales","horas_extras","horas_dobles","horas_compensadas","horas_totales","horas_nocturnas","costo"]:
                    if col not in per_pdf.columns:
                        per_pdf[col] = 0
                    per_pdf[col] = per_pdf[col].fillna(0)
                per_pdf["trabajo"] = per_pdf["horas_totales"] > 0
                registros = per_pdf.reset_index().to_dict("records")
                pdf_bytes = generar_pdf_horas_personal(registros, desde_pdf, hasta_pdf)
                st.download_button(
                    "📄 Descargar reporte PDF",
                    data=pdf_bytes,
                    file_name=f"horas_personal_{desde_pdf.isoformat()}_{hasta_pdf.isoformat()}.pdf",
                    mime="application/pdf",
                    use_container_width=True,
                )
            else:
                st.warning("Configura el personal en Catálogos para generar el reporte.")
