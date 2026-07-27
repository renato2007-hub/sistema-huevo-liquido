"""
MIGRACION TEMPORAL — usar UNA SOLA VEZ, luego quitar del menu.

Convierte los galpones propios (Galpones La Libertad = Planta Central,
Galpones Rumipamba = Planta Sucursal) en proveedores internos con prefijo
PC / PSU, y actualiza las recepciones historicas para que apunten a los
nuevos proveedor_id.

Idempotente: si detecta que ya se corrio, no crea duplicados ni rompe nada.
Puedes correrla varias veces sin problema.

INSTRUCCIONES DE USO:
  1. Agrega este modulo al menu principal (temporalmente), como cualquier otro
     modulo.
  2. Antes de ejecutar, verifica que en el Google Sheet ya agregaste las
     columnas nuevas ('prefijo', 'tipo', 'ubicacion' en 'proveedores', y
     'estado' en 'recepciones_mp'). Si no las agregaste, el modulo te avisa.
  3. Entra al modulo y presiona "Ejecutar migracion".
  4. Cuando el reporte diga "Migracion completada", quita este modulo del
     menu (o simplemente ignoralo — no hara nada nuevo si vuelves a correrlo).
"""
import streamlit as st
import pandas as pd


MAPEO_GALPONES = {
    # galpon_id viejo : (proveedor_id nuevo, nombre a conservar, ubicacion, prefijo)
    "GAL-PC":  ("PROV-PC",  "Galpones La Libertad",  "Planta Central",  "PC"),
    "GAL-PSU": ("PROV-PSU", "Galpones Rumipamba",    "Planta Sucursal", "PSU"),
}


def render(db, username, rol):
    st.title("🔧 Migración one-shot: Galpones → Proveedores internos")
    st.warning(
        "⚠️ **Este módulo se usa una sola vez.** Convierte los galpones propios "
        "en proveedores internos con prefijo PC/PSU y actualiza las recepciones "
        "históricas. Es seguro correrlo más de una vez — es idempotente."
    )

    st.markdown("### Estado actual del Google Sheet")

    proveedores = db.get_df("proveedores")
    recepciones = db.get_df("recepciones_mp")

    # Chequear que las columnas nuevas esten en el sheet
    cols_prov_necesarias = ["prefijo", "tipo", "ubicacion"]
    faltantes_prov = [c for c in cols_prov_necesarias if c not in proveedores.columns]
    col_estado_falta = "estado" not in recepciones.columns

    if faltantes_prov:
        st.error(
            f"❌ En la pestaña **`proveedores`** del Google Sheet faltan estas columnas: "
            f"**{', '.join(faltantes_prov)}**. Agrégalas manualmente (déjalas vacías) y "
            f"vuelve a abrir esta página."
        )
    else:
        st.success("✅ La pestaña `proveedores` tiene las columnas nuevas.")

    if col_estado_falta:
        st.error(
            "❌ En la pestaña **`recepciones_mp`** del Google Sheet falta la columna "
            "**`estado`**. Agrégala como última columna (déjala vacía) y vuelve a abrir esta página."
        )
    else:
        st.success("✅ La pestaña `recepciones_mp` tiene la columna `estado`.")

    if faltantes_prov or col_estado_falta:
        st.stop()

    # Preview de lo que se va a hacer
    st.markdown("### Cambios que se van a aplicar")

    # 1) Proveedores a crear
    for prov_id, nombre, ubic, prefijo in [
        ("PROV-PC",  "Galpones La Libertad",  "Planta Central",  "PC"),
        ("PROV-PSU", "Galpones Rumipamba",    "Planta Sucursal", "PSU"),
    ]:
        existe = (
            not proveedores.empty
            and prov_id in proveedores["proveedor_id"].astype(str).values
        )
        if existe:
            st.markdown(f"- `{prov_id}` (**{nombre}**, prefijo `{prefijo}`) — ya existe, no se toca")
        else:
            st.markdown(f"- `{prov_id}` (**{nombre}**, prefijo `{prefijo}`, tipo interno) — **se creará**")

    # 2) Recepciones a reasignar
    if not recepciones.empty and "origen_id" in recepciones.columns:
        gal_pc = recepciones[recepciones["origen_id"].astype(str) == "GAL-PC"]
        gal_psu = recepciones[recepciones["origen_id"].astype(str) == "GAL-PSU"]
        total_reasignar = len(gal_pc) + len(gal_psu)
        st.markdown(
            f"- **{len(gal_pc)}** recepción(es) con origen `GAL-PC` → se cambiarán a `PROV-PC`"
        )
        st.markdown(
            f"- **{len(gal_psu)}** recepción(es) con origen `GAL-PSU` → se cambiarán a `PROV-PSU`"
        )
    else:
        total_reasignar = 0
        st.markdown("- Sin recepciones históricas por reasignar.")

    # 3) Estado='activo' en recepciones sin ese campo
    if "estado" in recepciones.columns:
        sin_estado = recepciones[
            recepciones["estado"].isna()
            | (recepciones["estado"].astype(str).str.strip() == "")
        ]
        st.markdown(
            f"- **{len(sin_estado)}** recepción(es) sin campo `estado` → se marcarán como `activo`"
        )
    else:
        sin_estado = pd.DataFrame()

    # 4) Recordar proveedores externos sin prefijo
    activos = proveedores[
        proveedores.get("activo", "TRUE").astype(str).str.upper() != "FALSE"
    ] if not proveedores.empty else pd.DataFrame()
    externos_sin_pref = pd.DataFrame()
    if not activos.empty and "prefijo" in activos.columns:
        externos_sin_pref = activos[
            (activos["prefijo"].isna() | (activos["prefijo"].astype(str).str.strip() == ""))
            & ~activos["proveedor_id"].astype(str).isin(["PROV-PC", "PROV-PSU"])
        ]
    if not externos_sin_pref.empty:
        st.info(
            f"ℹ️ Después de esta migración, quedarán **{len(externos_sin_pref)}** proveedor(es) "
            f"externo(s) sin prefijo asignado. Deberás asignarles un prefijo en "
            f"**Catálogos → Proveedores** antes de poder recibir cubetas de ellos."
        )
        with st.expander("Ver proveedores externos sin prefijo"):
            st.dataframe(
                externos_sin_pref[["proveedor_id", "nombre"]] if "proveedor_id" in externos_sin_pref.columns else externos_sin_pref,
                use_container_width=True, hide_index=True,
            )

    st.divider()

    # Boton para ejecutar
    if st.button("🚀 Ejecutar migración", type="primary"):
        log = []
        cambios = 0

        # 1) Crear proveedores internos
        for galpon_id, (prov_id, nombre, ubic, prefijo) in MAPEO_GALPONES.items():
            existe = (
                not proveedores.empty
                and prov_id in proveedores["proveedor_id"].astype(str).values
            )
            if existe:
                log.append(f"• {prov_id} ({nombre}) ya existía, no se tocó.")
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
            log.append(f"✔ Creado: {prov_id} — {nombre} ({ubic}, prefijo {prefijo})")
            cambios += 1

        # 2) Reasignar origen_id en recepciones
        recepciones = db.get_df("recepciones_mp")  # recargar por si cambio
        reasignaciones = 0
        if not recepciones.empty:
            for galpon_id, (prov_id, *_rest) in MAPEO_GALPONES.items():
                filas = recepciones[recepciones["origen_id"].astype(str) == galpon_id]
                for _, fila in filas.iterrows():
                    db.update_row("recepciones_mp", "recepcion_id", fila["recepcion_id"], {
                        "origen_id": prov_id,
                        "origen_tipo": "Proveedor",
                    })
                    reasignaciones += 1
        if reasignaciones:
            log.append(f"✔ {reasignaciones} recepción(es) histórica(s) reasignada(s).")
            cambios += reasignaciones
        else:
            log.append("• Ninguna recepción apuntaba a los galpones viejos (nada que hacer).")

        # 3) Marcar estado='activo' en recepciones sin ese campo
        recepciones = db.get_df("recepciones_mp")  # recargar
        sin_estado = recepciones[
            recepciones["estado"].isna()
            | (recepciones["estado"].astype(str).str.strip() == "")
        ]
        for _, fila in sin_estado.iterrows():
            db.update_row("recepciones_mp", "recepcion_id", fila["recepcion_id"], {
                "estado": "activo",
            })
        if len(sin_estado):
            log.append(f"✔ {len(sin_estado)} recepción(es) marcadas como 'activo'.")
            cambios += len(sin_estado)
        else:
            log.append("• Todas las recepciones ya tenían estado.")

        st.success(f"✅ Migración completada — {cambios} cambio(s) aplicado(s).")
        st.markdown("### Log de la migración")
        for linea in log:
            st.markdown(f"- {linea}")

        st.info(
            "**Ya puedes quitar este módulo del menú.** Si vuelves a entrar, "
            "no hará nada nuevo (es idempotente)."
        )
