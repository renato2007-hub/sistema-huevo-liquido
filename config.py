"""
Configuracion central del sistema: nombres de hojas (tabs) en Google Sheets
y las columnas (encabezado) de cada una. Si necesitas agregar un campo nuevo
a cualquier modulo, agregalo aqui y el sistema crea la columna automaticamente
la primera vez que corre (si la pestana es nueva).
"""

# Nombre de cada pestana (tab) del Google Sheet -> lista de columnas (encabezado)
SHEET_SCHEMAS = {
    # ---------- Catalogos ----------
    "usuarios": ["username", "password_hash", "nombre", "rol", "activo"],
    "galpones": ["galpon_id", "nombre", "ubicacion", "activo"],
    "proveedores": ["proveedor_id", "nombre", "contacto", "calificacion", "activo"],
    "categorias_huevo": [
        "categoria_id", "nombre", "kg_promedio_cubeta",
        "pct_clara", "pct_yema", "pct_cascara", "notas",
    ],
    "insumos": ["insumo_id", "nombre", "tipo", "unidad", "costo_unitario", "activo"],
    "presentaciones": ["presentacion_id", "nombre", "kg_nominal", "costo_envase_unitario", "activo"],
    "personal": ["personal_id", "nombre", "cargo", "costo_hora", "activo"],
    "clientes": ["cliente_id", "nombre", "contacto", "activo"],
    "vehiculos": ["vehiculo_id", "placa", "descripcion", "conductor", "activo"],
    "areas_limpieza": ["area_id", "nombre", "activo"],

    # ---------- Bodega de materia prima ----------
    "recepciones_mp": [
        "recepcion_id", "fecha", "origen_tipo", "origen_id", "categoria_id",
        "cubetas", "costo_cubeta", "costo_total", "cubetas_saldo",
        "fecha_vencimiento", "usuario", "observaciones",
    ],

    # ---------- Bodega de envases e insumos ----------
    "movimientos_envases_insumos": [
        "movimiento_id", "fecha", "item_tipo", "item_id", "tipo_movimiento", "causa",
        "cantidad", "costo_unitario", "costo_total", "proveedor", "modulo_destino",
        "usuario", "observaciones",
    ],

    # ---------- Mermas / danos de materia prima en bodega (antes del proceso) ----------
    "mermas_mp": [
        "merma_id", "fecha", "recepcion_id", "causa", "huevos_danados",
        "cubetas_equivalentes", "costo_estimado", "usuario", "observaciones",
    ],

    # ---------- Produccion de semielaborados ----------
    "consumo_mp_produccion": [
        "consumo_id", "fecha", "recepcion_id", "lote_semielaborado_id",
        "cubetas_usadas", "costo_unitario_aplicado", "costo_total_aplicado", "usuario",
    ],
    "produccion_insumos": [
        "detalle_id", "lote_semielaborado_id", "insumo_id", "cantidad", "costo_calculado",
    ],
    "produccion_personal": [
        "detalle_id", "lote_semielaborado_id", "personal_id", "horas", "costo_calculado",
    ],
    "produccion_semielaborados": [
        "lote_semielaborado_id", "fecha", "orden_produccion", "tipo_producto",
        "categoria_id", "cubetas_totales",
        "kg_teorico_bruto", "kg_liquido_teorico", "kg_real",
        "clara_teorica_kg", "clara_real_kg",
        "yema_teorica_kg", "yema_real_kg",
        "cascara_teorica_kg", "cascara_real_kg",
        "agua_litros",
        "costo_huevo", "costo_insumos", "costo_mano_obra", "costo_total",
        "costo_unitario_kg", "kg_saldo", "balance_masa_pct", "usuario", "observaciones",
    ],

    # ---------- Pasteurizacion y envasado ----------
    "pasteurizacion_envasado": [
        "lote_producto_id", "fecha", "lote_semielaborado_id", "presentacion_id",
        "kg_usado", "unidades_teoricas", "unidades_reales",
        "costo_semielaborado", "costo_envases", "costo_total", "costo_unitario",
        "unidades_saldo", "usuario", "observaciones",
    ],

    # ---------- Cuarto frio ----------
    "cuarto_frio_entradas": [
        "entrada_id", "fecha", "lote_producto_id", "presentacion_id",
        "cantidad", "costo_unitario", "fecha_vencimiento", "saldo", "usuario",
    ],
    "cuarto_frio_salidas": [
        "salida_id", "fecha", "entrada_id", "cliente_id", "cantidad", "vehiculo_id",
        "usuario", "observaciones",
    ],

    # ---------- Limpieza y desinfeccion ----------
    "limpieza_desinfeccion": [
        "limpieza_id", "fecha", "area_id", "tipo_limpieza",
        "agua_litros", "costo_insumos", "costo_total",
        "personal_id", "verificado", "usuario", "observaciones",
    ],
    "limpieza_insumos": [
        "detalle_id", "limpieza_id", "insumo_id", "cantidad", "costo_calculado",
    ],
}

MODULOS = [
    "Inicio",
    "Dashboard",
    "Bodega de materia prima",
    "Bodega de envases e insumos",
    "Producción de semielaborados",
    "Pasteurización y envasado",
    "Cuarto frío",
    "Limpieza y desinfección",
    "Trazabilidad",
    "Catálogos y configuración",
]
