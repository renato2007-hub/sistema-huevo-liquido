"""
Permisos por rol. Toda la logica de "quien puede ver/hacer que" vive aqui,
para no repetirla (y desincronizarla) en cada modulo.

Roles:
- admin: acceso total (Renato). Unico que entra a Catalogos/Usuarios y a
  las pestanas "Corregir / eliminar" (que revierten movimientos de
  inventario).
- gerencia / jefe_planta: acceso a todos los modulos operativos, Dashboard
  y Trazabilidad, y SI ven costos. No entran a Catalogos ni a
  Corregir/eliminar.
- supervisor: solo los modulos de registro diario, y NO ve costos en
  ninguna parte (ni montos, ni columnas de costo, ni valores de inventario).
"""

ROLES_DISPONIBLES = ["admin", "gerencia", "jefe_planta", "supervisor"]

NOMBRES_ROL = {
    "admin": "Administrador",
    "gerencia": "Gerencia",
    "jefe_planta": "Jefe de planta",
    "supervisor": "Supervisor",
}

_MODULOS_OPERATIVOS = {
    "Inicio",
    "Bodega de materia prima",
    "Bodega de envases e insumos",
    "Producción de semielaborados",
    "Pasteurización y envasado",
    "Cuarto frío",
    "Limpieza y desinfección",
}

MODULOS_PERMITIDOS = {
    "admin": _MODULOS_OPERATIVOS | {"Dashboard", "Trazabilidad", "Supervisión y calidad", "Recepción de pedidos", "Catálogos y configuración"},
    "gerencia": _MODULOS_OPERATIVOS | {"Dashboard", "Trazabilidad", "Supervisión y calidad", "Recepción de pedidos"},
    "jefe_planta": _MODULOS_OPERATIVOS | {"Dashboard", "Trazabilidad", "Supervisión y calidad", "Recepción de pedidos"},
    "supervisor": set(_MODULOS_OPERATIVOS),
}


def rol_normalizado(rol) -> str:
    """Si el rol viene vacio (usuarios creados antes de que existieran los
    roles) se trata como admin, para no dejar a nadie fuera por accidente
    con esta actualizacion."""
    rol = str(rol or "").strip()
    return rol if rol in ROLES_DISPONIBLES else "admin"


def puede_ver_modulo(rol, modulo: str) -> bool:
    rol = rol_normalizado(rol)
    return modulo in MODULOS_PERMITIDOS.get(rol, set())


def ve_costos(rol) -> bool:
    return rol_normalizado(rol) != "supervisor"


def es_admin(rol) -> bool:
    return rol_normalizado(rol) == "admin"


def puede_editar_pedidos(rol) -> bool:
    """Editar/eliminar pedidos queda reservado a admin (Renato) y Gerencia
    -- ni siquiera Jefe de planta, a pedido explicito del negocio."""
    return rol_normalizado(rol) in ("admin", "gerencia")
