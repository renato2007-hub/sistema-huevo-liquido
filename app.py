import base64
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
    supervision,
    solicitud_compra,
    pedidos,
    plan_produccion,
    energia,
    personal_turnos,
    dashboard,
    trazabilidad,
    catalogos,
)

st.set_page_config(page_title="Sistema de producción — Ovoproductos", layout="wide", page_icon="🥚")

db = get_db()
username, rol = login(db)


@st.cache_data(show_spinner=False)
def _logo_base64():
    with open("assets/ovomas_logo.jpeg", "rb") as f:
        return base64.b64encode(f.read()).decode()


# ============================== BARRA LATERAL ==============================
st.sidebar.markdown(
    f"""
    <div style="background: #FFFFFF; border: 1px solid #EEE3D6; padding: 14px 16px;
                border-radius: 12px; margin-bottom: 14px; text-align: center;">
        <img src="data:image/jpeg;base64,{_logo_base64()}" style="max-width: 100%; height: auto;">
        <div style="color: #777; font-size: 11px; margin-top: 6px; letter-spacing: 0.3px;">
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

if puede_ver_modulo(rol, "Solicitud MP e Insumos") or puede_ver_modulo(rol, "Recepción de pedidos"):
    _categoria("📋&nbsp;&nbsp;PLANIFICACIÓN", "#2D6CA2")
    _boton_modulo("Solicitud MP e Insumos", "📑")
    _boton_modulo("Recepción de pedidos", "🧾")
    _boton_modulo("Plan de producción", "📅")

_categoria("🏭&nbsp;&nbsp;PRODUCCIÓN", "#D9740C")
_boton_modulo("Bodega de materia prima", "🥚")
_boton_modulo("Bodega de envases e insumos", "📦")
_boton_modulo("Producción de semielaborados", "⚗️")
_boton_modulo("Pasteurización y envasado", "🧪")
_boton_modulo("Cuarto frío", "❄️")

if puede_ver_modulo(rol, "Limpieza y desinfección") or puede_ver_modulo(rol, "Trazabilidad") or puede_ver_modulo(rol, "Supervisión y calidad"):
    _categoria("🧽&nbsp;&nbsp;OPERACIONES", "#0E8A8A")
    _boton_modulo("Limpieza y desinfección", "🧽")
    _boton_modulo("Supervisión y calidad", "👔")
    _boton_modulo("Trazabilidad", "📄")
    _boton_modulo("Personal y turnos", "👥")
    _boton_modulo("Energía", "⚡")

if puede_ver_modulo(rol, "Dashboard") or puede_ver_modulo(rol, "Catálogos y configuración"):
    _categoria("📊&nbsp;&nbsp;GESTIÓN", "#6D3FA8")
    _boton_modulo("Dashboard", "📊")
    _boton_modulo("Catálogos y configuración", "⚙️")

st.sidebar.markdown(
    """
    <div style="margin-top: 28px; padding-top: 10px; border-top: 1px solid #EEE;
                color: #999; font-size: 10.5px; line-height: 1.4;">
        Desarrollado por:<br>Renato Pérez — COO y Analista de Datos
    </div>
    """,
    unsafe_allow_html=True,
)

modulo = st.session_state["modulo_actual"]

# ============================== CONTENIDO ==============================
if modulo == "Inicio":
    st.title("Panel general")
    st.write(f"Bienvenido/a, **{username}**. Selecciona un módulo en el menú lateral para empezar.")

    st.markdown("### 🚀 Primeros pasos para empezar a ingresar datos")

    pasos = [
        (
            "1️⃣ Configura los catálogos base",
            "Ve a **Catálogos y configuración** y crea, en este orden: galpones y/o "
            "proveedores → categorías de huevo (con su rendimiento teórico) → insumos "
            "de limpieza → presentaciones de envase → personal → clientes → vehículos "
            "→ áreas de limpieza. Sin esto, los demás módulos no van a tener qué ofrecerte "
            "en sus listas desplegables.",
        ),
        (
            "2️⃣ Registra una recepción de huevo",
            "En **Bodega de materia prima**, registra el ingreso de huevo (galpón propio "
            "o proveedor), con sus cubetas y costo.",
        ),
        (
            "3️⃣ Registra una producción de semielaborados",
            "En **Producción de semielaborados**, elige cuántas cubetas procesar — el "
            "sistema sugiere automáticamente de qué lote tomarlas. Registra insumos, "
            "personal, agua, y al final los valores reales obtenidos.",
        ),
        (
            "4️⃣ Pasteuriza y envasa",
            "En **Pasteurización y envasado**, toma kg del tanque de semielaborado y "
            "conviértelos en producto terminado en la presentación que corresponda.",
        ),
        (
            "5️⃣ Ingresa a cuarto frío y despacha",
            "En **Cuarto frío**, registra el ingreso del producto envasado, y luego "
            "arma los despachos a clientes organizados por vehículo.",
        ),
        (
            "6️⃣ Revisa el panorama completo",
            "En **Dashboard** puedes ver, por período, costos, rendimientos, inventarios "
            "y mermas — y en **Trazabilidad** puedes generar el informe PDF completo de "
            "cualquier lote.",
        ),
    ]
    for titulo, texto in pasos:
        with st.container(border=True):
            st.markdown(f"**{titulo}**")
            st.write(texto)

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
elif modulo == "Supervisión y calidad":
    supervision.render(db, username, rol)
elif modulo == "Energía":
    energia.render(db, username, rol)
elif modulo == "Trazabilidad":
    trazabilidad.render(db, username, rol)
elif modulo == "Personal y turnos":
    personal_turnos.render(db, username, rol)
elif modulo == "Plan de producción":
    plan_produccion.render(db, username, rol)
elif modulo == "Solicitud MP e Insumos":
    solicitud_compra.render(db, username, rol)
elif modulo == "Recepción de pedidos":
    pedidos.render(db, username, rol)
elif modulo == "Catálogos y configuración":
    catalogos.render(db, username, rol)
