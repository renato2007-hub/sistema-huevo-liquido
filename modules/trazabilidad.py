"""
Trazabilidad: genera un informe PDF descargable que sigue un lote de origen
(SR = huevo entero, R = clara, TK = yema) desde que el huevo entra a bodega
de materia prima hasta el despacho a clientes, incluyendo saneamiento
relacionado y el balance de masa de distribucion. No incluye ningun costo
-- es un documento de trazabilidad/inocuidad, no de costos.
"""
import streamlit as st
from utils.trazabilidad import construir_arbol_trazabilidad
from utils.pdf_trazabilidad import generar_pdf_trazabilidad

NOMBRES_TABLAS = [
    "recepciones_mp", "consumo_mp_produccion", "produccion_semielaborados",
    "limpieza_desinfeccion", "areas_limpieza", "pasteurizacion_envasado",
    "presentaciones", "cuarto_frio_entradas", "cuarto_frio_salidas",
    "clientes", "vehiculos", "galpones", "proveedores", "categorias_huevo",
]


def render(db, username, rol):
    st.title("📄 Trazabilidad")
    st.caption(
        "Elige el lote de origen (SR = huevo entero, R = clara, TK = yema) y "
        "genera un informe PDF con todo el recorrido hacia adelante: "
        "producción, saneamiento relacionado, pasteurización/envasado, "
        "ingreso a cuarto frío, despacho a clientes y el balance de masa de "
        "distribución. El informe no incluye costos."
    )

    produccion = db.get_df("produccion_semielaborados")
    if produccion.empty:
        st.info("No hay lotes de origen (SR/R/TK) registrados todavía.")
        return

    lote_id = st.selectbox(
        "Lote de origen (SR/R/TK)", produccion["lote_semielaborado_id"].sort_values(ascending=False),
    )

    if st.button("📄 Generar informe PDF"):
        with st.spinner("Construyendo la trazabilidad completa..."):
            tablas = {nombre: db.get_df(nombre) for nombre in NOMBRES_TABLAS}
            arbol = construir_arbol_trazabilidad(tablas, "semielaborado", lote_id)
            pdf_bytes = generar_pdf_trazabilidad(arbol, "semielaborado", lote_id)

        if not arbol:
            st.warning(
                "No se encontró ningún dato de trazabilidad para este lote "
                "(igual se generó un PDF indicándolo)."
            )
        else:
            total_producciones = sum(len(n["producciones"]) for n in arbol)
            total_despachos = sum(
                len(e["despachos"])
                for n in arbol for p in n["producciones"] for pa in p["pasteurizaciones"] for e in pa["entradas_cf"]
            )
            st.success(
                f"Informe generado: {len(arbol)} recepción(es) de origen, "
                f"{total_producciones} producción(es) derivada(s), {total_despachos} despacho(s)."
            )

        st.download_button(
            "⬇️ Descargar PDF",
            data=pdf_bytes,
            file_name=f"trazabilidad_{lote_id}.pdf",
            mime="application/pdf",
        )
