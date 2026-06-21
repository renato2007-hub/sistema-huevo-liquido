"""
Carga de saldo inicial -- cuarto frio.

Crea, SIN tocar bodega de materia prima ni bodega de envases/insumos:
  1. Los 3 lotes de origen (SR190626, R190626, TK190626) directo en
     produccion_semielaborados, con costos referenciales.
  2. Las 6 pasteurizaciones (cada lote x presentacion) que consumen esos
     lotes por completo.
  3. Las entradas a cuarto frio, una por cada reserva de cliente.

Se corre UNA SOLA VEZ, localmente, con tus credenciales reales:

    cd ~/Documents/sistema_huevo_liquido
    source venv/bin/activate
    python3 carga_saldo_inicial.py

Primero hace un DRY RUN (no escribe nada) y te muestra exactamente que va
a crear. Solo escribe en el Sheet si respondes "SI" a la confirmacion.
"""
import datetime
from utils.sheets_client import get_db

# ============================== DATOS A CARGAR ==============================
FECHA = datetime.date(2026, 6, 19)  # la fecha en el codigo SR/R/TK 190626
VENCIMIENTO = datetime.date(2026, 7, 10)  # 21 dias despues, ajusta si quieres otra

USUARIO = "saldo_inicial"  # queda registrado como quien cargo estos datos

LOTES = {
    "SR190626": {"tipo_producto": "Huevo entero", "costo_unitario_kg": 4.50},
    "R190626": {"tipo_producto": "Clara", "costo_unitario_kg": 5.50},
    "TK190626": {"tipo_producto": "Yema", "costo_unitario_kg": 8.00},
}

# (lote, palabra clave de la presentacion, unidades, {cliente: unidades})
RESERVAS = [
    ("SR190626", "3.8", {"UIO_KFC": 160, "UIO_DULCAFE": 30, "UIO_MODERNA": 82}),
    ("R190626", "3.8", {"UIO_KFC": 4, "UIO_DULCAFE": 4}),
    ("TK190626", "3.8", {"UIO_DULCAFE": 5}),
    ("SR190626", "funda", {"UIO_MIDGO": 27, "UIO_INDHA": 12}),
    ("R190626", "funda", {"UIO_INDHA": 16}),
    ("TK190626", "funda", {"UIO_INDHA": 8}),
]

# ============================== CONEXION Y CATALOGOS ==============================
print("Conectando a Google Sheets...")
db = get_db()

presentaciones = db.get_df("presentaciones")
clientes = db.get_df("clientes")

if presentaciones.empty:
    raise SystemExit("❌ No hay presentaciones configuradas en Catálogos. Crea 'Envase 3.8Kg' y la funda de 5kg primero.")
if clientes.empty:
    raise SystemExit("❌ No hay clientes configurados en Catálogos. Crea los clientes primero.")


def buscar_presentacion(palabra_clave):
    candidatas = presentaciones[presentaciones["nombre"].str.lower().str.contains(palabra_clave.lower())]
    if candidatas.empty:
        raise SystemExit(f"❌ No encontré ninguna presentación que contenga '{palabra_clave}' en el nombre. Revisa Catálogos → Presentaciones.")
    if len(candidatas) > 1:
        raise SystemExit(f"❌ Encontré más de una presentación que contiene '{palabra_clave}': {list(candidatas['nombre'])}. Ajusta la palabra clave en el script.")
    return candidatas.iloc[0]


def buscar_cliente(cliente_id_buscado):
    fila = clientes[clientes["cliente_id"].astype(str).str.upper() == cliente_id_buscado.upper()]
    if fila.empty:
        raise SystemExit(f"❌ No encontré el cliente_id '{cliente_id_buscado}' en Catálogos → Clientes. Revisa el código exacto.")
    return fila.iloc[0]


# ============================== PLAN (DRY RUN) ==============================
print("\n" + "=" * 70)
print("ESTO ES LO QUE SE VA A CREAR (todavía no se ha escrito nada):")
print("=" * 70)

plan_lotes = {}
for codigo, datos in LOTES.items():
    plan_lotes[codigo] = {**datos, "kg_total": 0.0}

plan_pasteurizaciones = []
plan_entradas = []

for i, (lote, palabra_clave, reservas_cliente) in enumerate(RESERVAS, start=1):
    pres = buscar_presentacion(palabra_clave)
    kg_nominal = float(pres["kg_nominal"])
    unidades_totales = sum(reservas_cliente.values())
    kg_usado = unidades_totales * kg_nominal
    plan_lotes[lote]["kg_total"] += kg_usado

    lote_producto_id = f"PROD-SALDOINI-{i:02d}"
    costo_unit_kg = LOTES[lote]["costo_unitario_kg"]
    costo_semielaborado = kg_usado * costo_unit_kg
    costo_envases = unidades_totales * float(pres["costo_envase_unitario"])
    costo_total = costo_semielaborado + costo_envases
    costo_unitario = costo_total / unidades_totales if unidades_totales else 0

    print(f"\n📦 Pasteurización {lote_producto_id}: {lote} → {pres['nombre']}")
    print(f"   {unidades_totales} unidades, {kg_usado:.1f} kg, costo unitario {costo_unitario:.3f}")

    plan_pasteurizaciones.append({
        "lote_producto_id": lote_producto_id, "lote": lote, "presentacion_id": pres["presentacion_id"],
        "kg_usado": kg_usado, "unidades": unidades_totales,
        "costo_semielaborado": costo_semielaborado, "costo_envases": costo_envases,
        "costo_total": costo_total, "costo_unitario": costo_unitario,
    })

    for cliente_nombre, cantidad in reservas_cliente.items():
        cli = buscar_cliente(cliente_nombre)
        print(f"   ❄️  Reservado para {cli['nombre']}: {cantidad} unidades")
        plan_entradas.append({
            "lote_producto_id": lote_producto_id, "presentacion_id": pres["presentacion_id"],
            "cantidad": cantidad, "costo_unitario": costo_unitario,
            "cliente_nombre": cli["nombre"],
        })

print("\n" + "-" * 70)
print("Resumen de los 3 lotes de origen:")
for codigo, datos in plan_lotes.items():
    print(f"  {codigo} ({datos['tipo_producto']}): {datos['kg_total']:.1f} kg, a {datos['costo_unitario_kg']:.2f}/kg")
print("-" * 70)
print(f"\nTotal: {len(plan_lotes)} lotes, {len(plan_pasteurizaciones)} pasteurizaciones, {len(plan_entradas)} entradas a cuarto frío.")

# ============================== CONFIRMACION ==============================
respuesta = input("\n¿Escribir esto en el Google Sheet? Escribe SI para continuar: ")
if respuesta.strip().upper() != "SI":
    print("Cancelado -- no se escribió nada.")
    raise SystemExit()

# ============================== ESCRITURA ==============================
print("\nEscribiendo...")

for codigo, datos in plan_lotes.items():
    kg_total = datos["kg_total"]
    tipo = datos["tipo_producto"]
    fila = {
        "lote_semielaborado_id": codigo,
        "fecha": FECHA.isoformat(),
        "orden_produccion": "SALDO INICIAL",
        "tipo_producto": tipo,
        "categoria_id": "",
        "cubetas_totales": 0,
        "kg_teorico_bruto": kg_total,
        "kg_liquido_teorico": kg_total,
        "kg_real": kg_total,
        "clara_teorica_kg": kg_total if tipo == "Clara" else 0,
        "clara_real_kg": kg_total if tipo == "Clara" else 0,
        "yema_teorica_kg": kg_total if tipo == "Yema" else 0,
        "yema_real_kg": kg_total if tipo == "Yema" else 0,
        "cascara_teorica_kg": 0,
        "cascara_real_kg": 0,
        "agua_litros": 0,
        "costo_huevo": kg_total * datos["costo_unitario_kg"],
        "costo_insumos": 0,
        "costo_mano_obra": 0,
        "costo_total": kg_total * datos["costo_unitario_kg"],
        "costo_unitario_kg": datos["costo_unitario_kg"],
        "kg_saldo": 0,  # ya se consume por completo en las pasteurizaciones de abajo
        "balance_masa_pct": 100,
        "turno": "",
        "usuario": USUARIO,
        "observaciones": "Saldo inicial -- carga de arranque del sistema, no representa una produccion real registrada paso a paso.",
    }
    db.append_row("produccion_semielaborados", fila)
    print(f"  ✅ Lote {codigo} creado.")

for p in plan_pasteurizaciones:
    fila = {
        "lote_producto_id": p["lote_producto_id"],
        "fecha": FECHA.isoformat(),
        "lote_semielaborado_id": p["lote"],
        "presentacion_id": p["presentacion_id"],
        "kg_usado": p["kg_usado"],
        "unidades_teoricas": p["unidades"],
        "unidades_reales": p["unidades"],
        "pasteurizado": True,
        "costo_semielaborado": p["costo_semielaborado"],
        "costo_envases": p["costo_envases"],
        "tapa_id": "", "costo_tapas": 0,
        "etiqueta_id": "", "costo_etiquetas": 0,
        "carton_id": "", "cantidad_cartones": 0, "costo_cartones": 0,
        "liner_id": "", "costo_liners": 0,
        "costo_total": p["costo_total"],
        "costo_unitario": p["costo_unitario"],
        "unidades_saldo": 0,  # todo se mueve de una vez a cuarto frio, abajo
        "turno": "",
        "usuario": USUARIO,
        "observaciones": "Saldo inicial -- carga de arranque del sistema.",
    }
    db.append_row("pasteurizacion_envasado", fila)
    print(f"  ✅ Pasteurización {p['lote_producto_id']} creada.")

for e in plan_entradas:
    entrada_id = db.siguiente_id("cuarto_frio_entradas", "CF", FECHA)
    fila = {
        "entrada_id": entrada_id,
        "fecha": FECHA.isoformat(),
        "lote_producto_id": e["lote_producto_id"],
        "presentacion_id": e["presentacion_id"],
        "cantidad": e["cantidad"],
        "costo_unitario": e["costo_unitario"],
        "fecha_vencimiento": VENCIMIENTO.isoformat(),
        "saldo": e["cantidad"],
        "usuario": USUARIO,
    }
    db.append_row("cuarto_frio_entradas", fila)
    print(f"  ✅ Entrada {entrada_id}: {e['cantidad']} u. reservadas para {e['cliente_nombre']}.")

print("\n🎉 Listo. Ve a Cuarto frío → Inventario actual para revisar.")
