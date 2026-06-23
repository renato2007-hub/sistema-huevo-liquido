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
    "presentaciones": ["presentacion_id", "nombre", "kg_nominal", "tipo_envase", "costo_envase_unitario", "activo"],
    "tapas": ["tapa_id", "color", "costo_unitario", "activo"],
    "etiquetas": ["etiqueta_id", "nombre", "origen", "costo_unitario", "activo"],
    "cartones": ["carton_id", "nombre", "capacidad", "costo_unitario", "activo"],
    "liners": ["liner_id", "nombre", "costo_unitario", "activo"],

    "solicitudes_compra": [
        "solicitud_id", "numero_oc", "fecha_solicitud", "fecha_maxima_recepcion",
        "proveedor_recomendado", "recibido", "usuario", "observaciones",
    ],
    "solicitud_compra_items": [
        "detalle_id", "solicitud_id", "categoria", "nombre_item", "cantidad", "unidad",
    ],

    "verificacion_cargas": [
        "verificacion_id", "fecha", "vehiculo_id", "correcto", "despachador",
        "descripcion_error", "usuario", "observaciones",
    ],

    "stock_a_granel": [
        "stock_id", "fecha_entrada", "lote_origen", "tipo_producto",
        "kg_inicial", "kg_saldo", "usuario", "observaciones",
    ],

    "pedidos": [
        "pedido_id", "pedido_cliente_ref", "cliente_id", "medio_recepcion", "ciudad",
        "tipo_producto", "presentacion_id", "unidades_solicitadas", "cantidad_kg",
        "fecha_pedido", "fecha_produccion", "fecha_entrega",
        "producido", "usuario", "observaciones",
    ],
    "personal": ["personal_id", "nombre", "cargo", "tipo_personal", "costo_hora", "activo"],
    "clientes": ["cliente_id", "nombre", "contacto", "activo"],
    "vehiculos": ["vehiculo_id", "placa", "descripcion", "conductor", "activo"],
    "areas_limpieza": ["area_id", "nombre", "activo"],
    "materiales_limpieza": ["material_id", "nombre", "unidad", "activo"],
    "turnos": ["turno_id", "nombre", "hora_inicio", "hora_fin", "activo"],
    "feriados": ["fecha", "nombre", "activo"],
    "compensaciones_feriado": ["compensacion_id", "fecha", "personal_id", "observaciones", "usuario"],

    # ---------- Supervision y calidad (overhead diario, no se reparte por lote) ----------
    "mermas_semielaborado": [
        "merma_id", "fecha", "lote_semielaborado_id", "kg_desechado",
        "causa", "costo_estimado", "usuario", "observaciones",
    ],

    "supervision_diaria": [
        "registro_id", "fecha", "personal_id", "hora_entrada", "hora_salida",
        "horas", "horas_nocturnas", "costo_calculado", "usuario", "observaciones",
    ],

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
        "detalle_id", "lote_semielaborado_id", "personal_id",
        "hora_entrada", "hora_salida", "horas", "horas_nocturnas", "costo_calculado",
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
        "costo_unitario_kg", "kg_saldo", "balance_masa_pct", "turno", "usuario", "observaciones",
    ],

    # ---------- Pasteurizacion y envasado ----------
    "pasteurizacion_envasado": [
        "lote_producto_id", "fecha", "lote_semielaborado_id", "presentacion_id",
        "kg_usado", "unidades_teoricas", "unidades_reales", "pasteurizado",
        "costo_semielaborado", "costo_envases", "tapa_id", "costo_tapas",
        "etiqueta_id", "costo_etiquetas",
        "carton_id", "cantidad_cartones", "costo_cartones",
        "liner_id", "costo_liners",
        "costo_total", "costo_unitario",
        "unidades_saldo", "turno", "usuario", "observaciones",
    ],

    # ---------- Cuarto frio ----------
    "cuarto_frio_entradas": [
        "entrada_id", "fecha", "lote_producto_id", "presentacion_id",
        "cantidad", "costo_unitario", "fecha_vencimiento", "saldo", "usuario",
    ],
    "cuarto_frio_salidas": [
        "salida_id", "fecha", "entrada_id", "cliente_id", "cantidad", "vehiculo_id",
        "despachador", "pedido_ref", "usuario", "observaciones",
    ],

    # ---------- Limpieza y desinfeccion ----------
    "limpieza_desinfeccion": [
        "limpieza_id", "fecha", "area_id", "tipo_limpieza",
        "agua_litros", "costo_insumos", "costo_total",
        "personal_id", "turno", "verificado", "usuario", "observaciones",
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
