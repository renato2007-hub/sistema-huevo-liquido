"""
Permisos por rol.

Roles:
- admin: acceso total (Renato).
- gerencia / jefe_planta: todos los módulos operativos + Dashboard + Trazabilidad.
- supervisor: módulos de registro diario, sin costos.
- despachador: solo Cuarto frío, Recepción de pedidos (lectura) y Trazabilidad.
  No ve costos en ninguna parte.
"""

ROLES_DISPONIBLES = ["admin", "gerencia", "jefe_planta", "supervisor", "despachador"]

NOMBRES_ROL = {
    "admin": "Administrador",
    "gerencia": "Gerencia",
    "jefe_planta": "Jefe de planta",
    "supervisor": "Supervisor",
    "despachador": "Despachador",
}

_MODULOS_OPERATIVOS = {
    "Inicio",
    "Bodega de materia prima",
    "Bodega de envases e insumos",
    "Producción de semielaborados",
    "Pasteurización y envasado",
    "Cuarto frío",
    "Limpieza y desinfección", "Energía",
    "Cáscara (subproducto)",
}

MODULOS_PERMITIDOS = {
    "admin": _MODULOS_OPERATIVOS | {
        "Dashboard", "Trazabilidad", "Supervisión y calidad",
        "Solicitud MP e Insumos", "Recepción de pedidos",
        "Plan de producción", "Personal y turnos", "Catálogos y configuración", "🔧 Migración MP", "Bitácora", "Orden de salida"
    },
    "gerencia": _MODULOS_OPERATIVOS | {
        "Dashboard", "Trazabilidad", "Supervisión y calidad",
        "Solicitud MP e Insumos", "Recepción de pedidos",
        "Plan de producción", "Personal y turnos", "Bitácora", "Orden de salida"
    },
    "jefe_planta": _MODULOS_OPERATIVOS | {
        "Dashboard", "Trazabilidad", "Supervisión y calidad",
        "Solicitud MP e Insumos", "Recepción de pedidos",
        "Plan de producción", "Personal y turnos", "Orden de salida",
    },
    "supervisor": set(_MODULOS_OPERATIVOS),
    "despachador": {
        "Inicio",
        "Cuarto frío",
        "Recepción de pedidos",
    },
}


def rol_normalizado(rol) -> str:
    rol = str(rol or "").strip()
    return rol if rol in ROLES_DISPONIBLES else "admin"


def puede_ver_modulo(rol, modulo: str) -> bool:
    rol = rol_normalizado(rol)
    return modulo in MODULOS_PERMITIDOS.get(rol, set())


def ve_costos(rol) -> bool:
    """Despachador y supervisor no ven costos."""
    return rol_normalizado(rol) not in ("supervisor", "despachador")


def es_admin(rol) -> bool:
    return rol_normalizado(rol) == "admin"


def es_admin_o_jefe_planta(rol) -> bool:
    return rol_normalizado(rol) in ("admin", "jefe_planta")


def puede_corregir_produccion(rol) -> bool:
    """Admin, jefe de planta y supervisor: son quienes registran la
    producción diaria, así que también pueden corregir sus propios errores
    de ingreso."""
    return rol_normalizado(rol) in ("admin", "jefe_planta", "supervisor")


def puede_editar_pedidos(rol) -> bool:
    return rol_normalizado(rol) in ("admin", "gerencia")


def es_despachador(rol) -> bool:
    return rol_normalizado(rol) == "despachador"
