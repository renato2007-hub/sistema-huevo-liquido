"""
MIGRACION ONE-SHOT: convierte los galpones propios de Ovomas en proveedores
internos con prefijo (PC=Planta Central, PSU=Planta Sucursal), y actualiza
las recepciones historicas para que apunten a los nuevos proveedor_id.

Cambios que hace:
  1. En la tabla 'proveedores' crea (si no existen) dos registros:
       PROV-PC  — Galpones La Libertad   (Planta Central,  prefijo=PC,  tipo=interno)
       PROV-PSU — Galpones Rumipamba     (Planta Sucursal, prefijo=PSU, tipo=interno)
  2. Si la tabla 'proveedores' no tiene las columnas 'prefijo', 'tipo',
     'ubicacion' — las agrega vacias.
  3. En 'recepciones_mp': para las filas con origen_tipo="Galpón propio",
     cambia origen_id="GAL-PC" -> "PROV-PC" y origen_id="GAL-PSU" -> "PROV-PSU",
     y origen_tipo -> "Proveedor".
  4. Agrega la columna 'estado' a 'recepciones_mp' si no existe, con valor
     "activo" en todas las filas existentes.
  5. Reporta al final los proveedores externos que aun no tienen prefijo
     asignado (el admin debe completarlos manualmente en Catalogos antes
     de recibir cubetas de ellos).

Ejecutar UNA SOLA VEZ desde el directorio raiz del proyecto:
    python3 scripts/migracion_galpones_a_proveedores.py

Es idempotente: si ya se corrio antes, no crea duplicados ni rompe nada;
solo reporta lo que ya esta en su lugar.
"""
import sys
import os

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from utils.db import Database


MAPEO_GALPONES = {
    # galpon_id viejo : (proveedor_id nuevo, nombre a conservar, ubicacion, prefijo)
    "GAL-PC":  ("PROV-PC",  "Galpones La Libertad",  "Planta Central",  "PC"),
    "GAL-PSU": ("PROV-PSU", "Galpones Rumipamba",    "Planta Sucursal", "PSU"),
}


def main():
    print("=" * 70)
    print("MIGRACION: Galpones propios -> Proveedores internos")
    print("=" * 70)

    db = Database()
    cambios = 0

    # ---- 1) Asegurar columnas necesarias en 'proveedores' ----
    print("\n[1/4] Verificando estructura de la tabla 'proveedores'...")
    proveedores = db.get_df("proveedores")
    columnas_necesarias = ["proveedor_id", "nombre", "prefijo", "tipo",
                           "ubicacion", "contacto", "calificacion", "activo"]
    faltantes = [c for c in columnas_necesarias if c not in proveedores.columns]
    if faltantes:
        print(f"  ⚠  Faltan columnas en 'proveedores': {faltantes}")
        print(f"     Agrega estas columnas MANUALMENTE en el Google Sheet")
        print(f"     antes de continuar (Streamlit no crea columnas nuevas).")
        return
    print("  ✔  Todas las columnas estan presentes.")

    # ---- 2) Crear los dos proveedores internos ----
    print("\n[2/4] Creando proveedores internos (si faltan)...")
    for galpon_id, (prov_id, nombre, ubic, prefijo) in MAPEO_GALPONES.items():
        existe = (
            not proveedores.empty
            and prov_id in proveedores["proveedor_id"].astype(str).values
        )
        if existe:
            print(f"  •  {prov_id} ({nombre}) ya existe, no se toca.")
            continue
        db.append_row("proveedores", {
            "proveedor_id": prov_id,
            "nombre": nombre,
            "prefijo": prefijo,
            "tipo": "interno",
            "ubicacion": ubic,
            "contacto": "",
            "calificacion": "Interno (planta propia)",
            "activo": True,
        })
        print(f"  ✔  {prov_id} creado — {nombre} ({ubic}, prefijo {prefijo}).")
        cambios += 1

    # ---- 3) Reasignar origen_id en recepciones historicas ----
    print("\n[3/4] Reasignando recepciones historicas (galpon_id -> proveedor_id)...")
    recepciones = db.get_df("recepciones_mp")
    if recepciones.empty:
        print("  •  No hay recepciones registradas todavia.")
    else:
        reasignaciones = 0
        for galpon_id, (prov_id, *_rest) in MAPEO_GALPONES.items():
            filas = recepciones[recepciones["origen_id"].astype(str) == galpon_id]
            for _, fila in filas.iterrows():
                db.update_row("recepciones_mp", "recepcion_id", fila["recepcion_id"], {
                    "origen_id": prov_id,
                    "origen_tipo": "Proveedor",
                })
                reasignaciones += 1
        if reasignaciones:
            print(f"  ✔  {reasignaciones} recepcion(es) historica(s) reasignadas.")
            cambios += reasignaciones
        else:
            print("  •  Ninguna recepcion apuntaba a los galpones viejos (nada que hacer).")

    # ---- 4) Agregar estado='activo' a recepciones sin ese campo ----
    print("\n[4/4] Marcando estado='activo' en recepciones existentes...")
    if "estado" not in recepciones.columns:
        print("  ⚠  La columna 'estado' NO existe en la sheet 'recepciones_mp'.")
        print("     Agregala MANUALMENTE en el Google Sheet como ultima columna,")
        print("     y vuelve a correr este script para marcar todo como 'activo'.")
    else:
        recepciones_actualizadas = db.get_df("recepciones_mp")
        sin_estado = recepciones_actualizadas[
            recepciones_actualizadas["estado"].isna()
            | (recepciones_actualizadas["estado"].astype(str).str.strip() == "")
        ]
        for _, fila in sin_estado.iterrows():
            db.update_row("recepciones_mp", "recepcion_id", fila["recepcion_id"], {
                "estado": "activo",
            })
        if len(sin_estado):
            print(f"  ✔  {len(sin_estado)} recepcion(es) marcadas como 'activo'.")
            cambios += len(sin_estado)
        else:
            print("  •  Todas las recepciones ya tienen estado.")

    # ---- Reporte final ----
    print("\n" + "=" * 70)
    print(f"MIGRACION COMPLETADA — {cambios} cambio(s) aplicado(s).")
    print("=" * 70)

    # Recordatorio: proveedores externos sin prefijo
    proveedores = db.get_df("proveedores")
    if not proveedores.empty and "prefijo" in proveedores.columns:
        activos = proveedores[
            proveedores.get("activo", "TRUE").astype(str).str.upper() != "FALSE"
        ]
        sin_pref = activos[
            activos["prefijo"].isna()
            | (activos["prefijo"].astype(str).str.strip() == "")
        ]
        if not sin_pref.empty:
            print(f"\n⚠  ATENCION: {len(sin_pref)} proveedor(es) activo(s) EXTERNO(S) NO tienen prefijo.")
            print("   Antes de registrar recepciones de ellos, ve a")
            print("   Catalogos -> Proveedores y asignales un prefijo unico (2-4 letras):")
            for _, p in sin_pref.iterrows():
                print(f"     - {p['proveedor_id']}: {p['nombre']}")


if __name__ == "__main__":
    main()
