import streamlit as st
from config import MODULOS
from utils.sheets_client import get_db
from utils.auth import login
from modules import (
    bodega_mp,
    bodega_envases_insumos,
    produccion_semielaborados,
    pasteurizacion_envasado,
    cuarto_frio,
    limpieza_desinfeccion,
    dashboard,
    trazabilidad,
    catalogos,
)

st.set_page_config(page_title="Sistema de producción — Huevo líquido", layout="wide")

db = get_db()
username = login(db)

st.sidebar.title("Huevo líquido")
st.sidebar.caption(f"Conectado como **{username}**")
if st.sidebar.button("Cerrar sesión"):
    del st.session_state["username"]
    st.rerun()

modulo = st.sidebar.radio("Módulo", MODULOS)

if modulo == "Inicio":
    st.title("Panel general")
    st.write(
        "Selecciona un módulo en el menú lateral para registrar movimientos "
        "del día o revisar el inventario."
    )
    st.info(
        "Si es la primera vez que usas el sistema: ve primero a "
        "'Catálogos y configuración' y crea galpones, proveedores, "
        "categorías de huevo, insumos, presentaciones, personal y clientes "
        "antes de registrar movimientos en los demás módulos."
    )
elif modulo == "Dashboard":
    dashboard.render(db, username)
elif modulo == "Bodega de materia prima":
    bodega_mp.render(db, username)
elif modulo == "Bodega de envases e insumos":
    bodega_envases_insumos.render(db, username)
elif modulo == "Producción de semielaborados":
    produccion_semielaborados.render(db, username)
elif modulo == "Pasteurización y envasado":
    pasteurizacion_envasado.render(db, username)
elif modulo == "Cuarto frío":
    cuarto_frio.render(db, username)
elif modulo == "Limpieza y desinfección":
    limpieza_desinfeccion.render(db, username)
elif modulo == "Trazabilidad":
    trazabilidad.render(db, username)
elif modulo == "Catálogos y configuración":
    catalogos.render(db, username)
