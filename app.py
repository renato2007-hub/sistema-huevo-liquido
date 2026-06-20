import streamlit as st
from utils.sheets_client import get_db
from utils.auth import login
from utils.permisos import puede_ver_modulo, NOMBRES_ROL, rol_normalizado
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

st.set_page_config(page_title="Sistema de producción — Huevo líquido", layout="wide", page_icon="🥚")

db = get_db()
username, rol = login(db)

# ============================== BARRA LATERAL ==============================
st.sidebar.markdown(
    """
    <div style="background: linear-gradient(135deg, #0B6E4F 0%, #119873 100%);
                padding: 18px 16px; border-radius: 12px; margin-bottom: 14px;">
        <div style="font-size: 30px; line-height: 1;">🥚</div>
        <div style="color: white; font-size: 20px; font-weight: 800; margin-top: 6px;">
            Huevo Líquido
        </div>
        <div style="color: #CFF3E4; font-size: 12px; margin-top: 2px;">
            Sistema integral de producción
        </div>
    </div>
    """,
    unsafe_allow_html=True,
)

with st.sidebar.container(border=True):
    st.markdown(f"👤 **{username}**")
    st.caption(NOMBRES_ROL.get(rol_normalizado(rol), rol))
    if st.button("🚪 Cerrar sesión", use_container_width=True):
        del st.session_state["username"]
        del st.session_state["rol"]
        st.rerun()

st.sidebar.write("")

if "modulo_actual" not in st.session_state:
    st.session_state["modulo_actual"] = "Inicio"
if not puede_ver_modulo(rol, st.session_state["modulo_actual"]):
    st.session_state["modulo_actual"] = "Inicio"


def _categoria(titulo, color):
    st.sidebar.markdown(
        f"""<div style="background:{color}; color:white; font-size:11px;
             font-weight:700; letter-spacing:0.6px; padding:6px 10px;
             border-radius:6px; margin:16px 0 6px 0;">{titulo}</div>""",
        unsafe_allow_html=True,
    )


def _boton_modulo(nombre, icono):
    if not puede_ver_modulo(rol, nombre):
        return
    activo = st.session_state["modulo_actual"] == nombre
    if st.sidebar.button(
        f"{icono}  {nombre}", key=f"nav_{nombre}", use_container_width=True,
        type="primary" if activo else "secondary",
    ):
        st.session_state["modulo_actual"] = nombre
        st.rerun()


_boton_modulo("Inicio", "🏠")

_categoria("🏭&nbsp;&nbsp;PRODUCCIÓN", "#D9740C")
_boton_modulo("Bodega de materia prima", "🥚")
_boton_modulo("Bodega de envases e insumos", "📦")
_boton_modulo("Producción de semielaborados", "⚗️")
_boton_modulo("Pasteurización y envasado", "🧪")
_boton_modulo("Cuarto frío", "❄️")

if puede_ver_modulo(rol, "Limpieza y desinfección") or puede_ver_modulo(rol, "Trazabilidad"):
    _categoria("🧽&nbsp;&nbsp;OPERACIONES", "#0E8A8A")
    _boton_modulo("Limpieza y desinfección", "🧽")
    _boton_modulo("Trazabilidad", "📄")

if puede_ver_modulo(rol, "Dashboard") or puede_ver_modulo(rol, "Catálogos y configuración"):
    _categoria("📊&nbsp;&nbsp;GESTIÓN", "#6D3FA8")
    _boton_modulo("Dashboard", "📊")
    _boton_modulo("Catálogos y configuración", "⚙️")

modulo = st.session_state["modulo_actual"]

# ============================== CONTENIDO ==============================
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
    dashboard.render(db, username, rol)
elif modulo == "Bodega de materia prima":
    bodega_mp.render(db, username, rol)
elif modulo == "Bodega de envases e insumos":
    bodega_envases_insumos.render(db, username, rol)
elif modulo == "Producción de semielaborados":
    produccion_semielaborados.render(db, username, rol)
elif modulo == "Pasteurización y envasado":
    pasteurizacion_envasado.render(db, username, rol)
elif modulo == "Cuarto frío":
    cuarto_frio.render(db, username, rol)
elif modulo == "Limpieza y desinfección":
    limpieza_desinfeccion.render(db, username, rol)
elif modulo == "Trazabilidad":
    trazabilidad.render(db, username, rol)
elif modulo == "Catálogos y configuración":
    catalogos.render(db, username, rol)
