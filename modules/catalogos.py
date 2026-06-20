"""
Catalogos y configuracion: galpones, proveedores, categorias de huevo (con
su rendimiento teorico -- el cual puede variar por tamano y por edad de
parvada), insumos, presentaciones, personal, clientes y usuarios del
sistema. Esta es la base que usan todos los demas modulos.

Cada catalogo tiene dos pestanas: "Agregar nuevo" y "Editar / eliminar
existente". Editar un registro NO cambia retroactivamente lo ya guardado
en otros modulos (por ejemplo, una produccion ya registrada conserva el
rendimiento teorico que estaba vigente cuando se guardo) -- solo afecta lo
que se registre de ahi en adelante.
"""
import streamlit as st
from utils.auth import hash_password
from utils.permisos import es_admin, ROLES_DISPONIBLES, NOMBRES_ROL


def _valor_default_numero(valor_actual):
    try:
        return float(valor_actual) if str(valor_actual) != "" else 0.0
    except (TypeError, ValueError):
        return 0.0


def _valor_default_bool(valor_actual):
    return str(valor_actual).strip().upper() not in ("FALSE", "0", "NO", "")


def _seccion_simple(db, nombre_sheet, titulo, campos):
    """campos: lista de (nombre_columna, tipo) donde tipo es 'texto', 'numero',
    'bool', 'fecha', o una lista/tupla de opciones fijas (selectbox).
    El primer campo de la lista se trata como identificador unico (id)."""
    st.subheader(titulo)
    id_col = campos[0][0]
    df = db.get_df(nombre_sheet)
    st.dataframe(df, use_container_width=True)

    tab_nuevo, tab_editar = st.tabs(["➕ Agregar nuevo", "✏️ Editar / eliminar existente"])

    with tab_nuevo:
        with st.form(f"form_nuevo_{nombre_sheet}"):
            valores = {}
            for col, tipo in campos:
                if isinstance(tipo, (list, tuple)):
                    valores[col] = st.selectbox(col, tipo, key=f"nuevo_{nombre_sheet}_{col}")
                elif tipo == "numero":
                    valores[col] = st.number_input(col, step=0.01, key=f"nuevo_{nombre_sheet}_{col}")
                elif tipo == "bool":
                    valores[col] = st.checkbox(col, value=True, key=f"nuevo_{nombre_sheet}_{col}")
                elif tipo == "fecha":
                    valores[col] = st.date_input(col, key=f"nuevo_{nombre_sheet}_{col}").isoformat()
                else:
                    valores[col] = st.text_input(col, key=f"nuevo_{nombre_sheet}_{col}")
            guardar = st.form_submit_button("Agregar")

        if guardar:
            if not valores[id_col]:
                st.error(f"El campo '{id_col}' es obligatorio.")
            elif not df.empty and id_col in df.columns and str(valores[id_col]) in df[id_col].astype(str).values:
                st.error(
                    f"Ya existe un registro con {id_col} = '{valores[id_col]}'. "
                    f"Usa la pestaña 'Editar / eliminar' si quieres modificarlo."
                )
            else:
                db.append_row(nombre_sheet, valores)
                st.success("Registro agregado.")
                st.rerun()

    with tab_editar:
        if df.empty:
            st.info("Todavía no hay registros para editar.")
            return

        id_seleccionado = st.selectbox(
            f"Selecciona el registro ({id_col})", df[id_col].astype(str),
            key=f"editar_select_{nombre_sheet}",
        )
        fila_actual = df[df[id_col].astype(str) == id_seleccionado].iloc[0]

        with st.form(f"form_editar_{nombre_sheet}_{id_seleccionado}"):
            nuevos_valores = {}
            for col, tipo in campos:
                valor_actual = fila_actual[col]
                if col == id_col:
                    st.text_input(
                        col, value=str(valor_actual), disabled=True,
                        key=f"editar_id_{nombre_sheet}_{id_seleccionado}",
                    )
                    continue
                if tipo == "numero":
                    nuevos_valores[col] = st.number_input(
                        col, value=_valor_default_numero(valor_actual), step=0.01,
                        key=f"editar_{nombre_sheet}_{col}_{id_seleccionado}",
                    )
                elif tipo == "bool":
                    nuevos_valores[col] = st.checkbox(
                        col, value=_valor_default_bool(valor_actual),
                        key=f"editar_{nombre_sheet}_{col}_{id_seleccionado}",
                    )
                elif isinstance(tipo, (list, tuple)):
                    indice = tipo.index(valor_actual) if valor_actual in tipo else 0
                    nuevos_valores[col] = st.selectbox(
                        col, tipo, index=indice,
                        key=f"editar_{nombre_sheet}_{col}_{id_seleccionado}",
                    )
                elif tipo == "fecha":
                    try:
                        import datetime as _dt
                        valor_fecha = _dt.date.fromisoformat(str(valor_actual))
                    except ValueError:
                        valor_fecha = None
                    nuevos_valores[col] = st.date_input(
                        col, value=valor_fecha,
                        key=f"editar_{nombre_sheet}_{col}_{id_seleccionado}",
                    ).isoformat()
                else:
                    nuevos_valores[col] = st.text_input(
                        col, value=str(valor_actual),
                        key=f"editar_{nombre_sheet}_{col}_{id_seleccionado}",
                    )
            col_a, col_b = st.columns(2)
            guardar_cambios = col_a.form_submit_button("💾 Guardar cambios")
            confirmar_eliminar = col_b.checkbox(
                "Confirmo que quiero eliminar este registro",
                key=f"confirmar_del_{nombre_sheet}_{id_seleccionado}",
            )
            eliminar = col_b.form_submit_button("🗑️ Eliminar")

        if guardar_cambios:
            db.update_row(nombre_sheet, id_col, id_seleccionado, nuevos_valores)
            st.success(f"'{id_seleccionado}' actualizado.")
            st.rerun()

        if eliminar:
            if not confirmar_eliminar:
                st.error("Marca la casilla de confirmación antes de eliminar.")
            else:
                db.delete_row(nombre_sheet, id_col, id_seleccionado)
                st.success(f"'{id_seleccionado}' eliminado.")
                st.rerun()

        st.caption(
            "Nota: editar valores aquí no cambia los registros que ya se guardaron "
            "en otros módulos (por ejemplo, producciones pasadas conservan los "
            "valores que estaban vigentes cuando se registraron)."
        )


def render(db, username, rol):
    st.title("Catálogos y configuración")

    if not es_admin(rol):
        st.error("🔒 Esta sección está disponible solo para el administrador.")
        return

    seccion = st.selectbox(
        "Catálogo a administrar",
        [
            "Galpones", "Proveedores", "Categorías de huevo (rendimientos)",
            "Insumos", "Presentaciones de envase", "Personal", "Clientes",
            "Vehículos", "Áreas de limpieza", "Turnos", "Feriados", "Usuarios",
        ],
    )

    if seccion == "Galpones":
        _seccion_simple(db, "galpones", "Galpones propios", [
            ("galpon_id", "texto"), ("nombre", "texto"), ("ubicacion", "texto"), ("activo", "bool"),
        ])
    elif seccion == "Proveedores":
        _seccion_simple(db, "proveedores", "Proveedores calificados", [
            ("proveedor_id", "texto"), ("nombre", "texto"), ("contacto", "texto"),
            ("calificacion", "texto"), ("activo", "bool"),
        ])
    elif seccion == "Categorías de huevo (rendimientos)":
        st.caption(
            "El rendimiento teórico varía según el tamaño del huevo y la edad de "
            "la parvada — crea una categoría por cada combinación que manejes "
            "(ej. parvada joven, madura, final) con sus propios porcentajes. "
            "kg_promedio_cubeta es el peso BRUTO del huevo entero (con cáscara); "
            "pct_clara + pct_yema + pct_cascara deberían sumar 100."
        )
        _seccion_simple(db, "categorias_huevo", "Categorías y rendimiento teórico", [
            ("categoria_id", "texto"), ("nombre", "texto"),
            ("kg_promedio_cubeta", "numero"), ("pct_clara", "numero"),
            ("pct_yema", "numero"), ("pct_cascara", "numero"), ("notas", "texto"),
        ])
    elif seccion == "Insumos":
        _seccion_simple(db, "insumos", "Insumos de limpieza", [
            ("insumo_id", "texto"), ("nombre", "texto"), ("tipo", "texto"),
            ("unidad", "texto"), ("costo_unitario", "numero"), ("activo", "bool"),
        ])
    elif seccion == "Presentaciones de envase":
        _seccion_simple(db, "presentaciones", "Presentaciones", [
            ("presentacion_id", "texto"), ("nombre", "texto"),
            ("kg_nominal", "numero"), ("costo_envase_unitario", "numero"), ("activo", "bool"),
        ])
    elif seccion == "Personal":
        _seccion_simple(db, "personal", "Personal de producción", [
            ("personal_id", "texto"), ("nombre", "texto"), ("cargo", "texto"),
            ("tipo_personal", ["Fijo", "Ocasional"]),
            ("costo_hora", "numero"), ("activo", "bool"),
        ])
    elif seccion == "Clientes":
        _seccion_simple(db, "clientes", "Clientes", [
            ("cliente_id", "texto"), ("nombre", "texto"), ("contacto", "texto"), ("activo", "bool"),
        ])
    elif seccion == "Vehículos":
        _seccion_simple(db, "vehiculos", "Vehículos de reparto", [
            ("vehiculo_id", "texto"), ("placa", "texto"), ("descripcion", "texto"),
            ("conductor", "texto"), ("activo", "bool"),
        ])
    elif seccion == "Áreas de limpieza":
        st.caption(
            "Sugerencia: Lavado de huevos, Pasteurizador, Equipos de envasado, "
            "Gavetas y cubetas, Pisos y paredes, Tanques de almacenamiento, Otros."
        )
        _seccion_simple(db, "areas_limpieza", "Áreas / equipos a limpiar", [
            ("area_id", "texto"), ("nombre", "texto"), ("activo", "bool"),
        ])
    elif seccion == "Turnos":
        st.caption("Ej. Turno 1 (06:00-14:00), Turno 2 (14:00-22:00) — ajusta a tus horarios reales.")
        _seccion_simple(db, "turnos", "Turnos de trabajo", [
            ("turno_id", "texto"), ("nombre", "texto"),
            ("hora_inicio", "texto"), ("hora_fin", "texto"), ("activo", "bool"),
        ])
    elif seccion == "Feriados":
        st.caption(
            "Marca aquí los días feriados — el Dashboard los usa para calcular "
            "horas dobles automáticamente (todas las horas trabajadas ese día "
            "cuentan como dobles, salvo que la persona haya compensado con "
            "descanso — ver más abajo)."
        )
        _seccion_simple(db, "feriados", "Feriados", [
            ("fecha", "fecha"), ("nombre", "texto"), ("activo", "bool"),
        ])

        st.divider()
        st.subheader("Compensaciones (trabajó feriado, descansó otro día)")
        st.caption(
            "Si acordaste con alguien que trabaje un feriado a cambio de "
            "descansar otro día normal (en vez de cobrar doble), regístralo "
            "aquí — esas horas dejan de contar como 'dobles' en el Dashboard "
            "y pasan a 'compensadas'."
        )
        personal_comp = db.get_df("personal")
        if personal_comp.empty:
            st.info("Configura personal en Catálogos → Personal antes de registrar compensaciones.")
        else:
            compensaciones = db.get_df("compensaciones_feriado")
            st.dataframe(compensaciones, use_container_width=True, hide_index=True)
            with st.form("form_compensacion"):
                fecha_comp = st.date_input("Fecha del feriado trabajado")
                personal_id_comp = st.selectbox(
                    "Persona", personal_comp["personal_id"],
                    format_func=lambda x: personal_comp.set_index("personal_id").loc[x, "nombre"],
                )
                obs_comp = st.text_input("Observaciones (opcional, ej. 'descansó el 2 de junio')", "")
                guardar_comp = st.form_submit_button("Registrar compensación")
            if guardar_comp:
                comp_id = db.siguiente_id("compensaciones_feriado", "COMP", fecha_comp)
                db.append_row("compensaciones_feriado", {
                    "compensacion_id": comp_id,
                    "fecha": fecha_comp.isoformat(),
                    "personal_id": personal_id_comp,
                    "observaciones": obs_comp,
                    "usuario": username,
                })
                st.success(f"Compensación {comp_id} registrada.")
                st.rerun()
    elif seccion == "Usuarios":
        st.subheader("Usuarios del sistema")
        df = db.get_df("usuarios")
        st.dataframe(df[["username", "nombre", "rol", "activo"]] if not df.empty else df, use_container_width=True)

        tab_nuevo, tab_editar = st.tabs(["➕ Crear usuario", "✏️ Editar / desactivar existente"])

        with tab_nuevo:
            with st.form("form_usuario_nuevo"):
                username_nuevo = st.text_input("Usuario (sin espacios)")
                nombre = st.text_input("Nombre completo")
                password = st.text_input("Contraseña", type="password")
                rol_nuevo = st.selectbox(
                    "Rol", ROLES_DISPONIBLES, index=ROLES_DISPONIBLES.index("supervisor"),
                    format_func=lambda r: NOMBRES_ROL[r],
                )
                activo = st.checkbox("Activo", value=True)
                guardar = st.form_submit_button("Crear usuario")
            if guardar:
                if not username_nuevo or not password:
                    st.error("Usuario y contraseña son obligatorios.")
                elif not df.empty and username_nuevo in df["username"].astype(str).values:
                    st.error("Ese nombre de usuario ya existe.")
                else:
                    db.append_row("usuarios", {
                        "username": username_nuevo,
                        "password_hash": hash_password(password),
                        "nombre": nombre,
                        "rol": rol_nuevo,
                        "activo": activo,
                    })
                    st.success(f"Usuario {username_nuevo} creado como {NOMBRES_ROL[rol_nuevo]}.")
                    st.rerun()

        with tab_editar:
            if df.empty:
                st.info("Todavía no hay usuarios para editar.")
            else:
                username_sel = st.selectbox("Selecciona el usuario", df["username"], key="editar_user_select")
                fila_usuario = df[df["username"] == username_sel].iloc[0]
                rol_actual = str(fila_usuario.get("rol", "")) or "admin"
                if rol_actual not in ROLES_DISPONIBLES:
                    rol_actual = "admin"
                with st.form(f"form_usuario_editar_{username_sel}"):
                    nombre_nuevo = st.text_input(
                        "Nombre completo", value=str(fila_usuario["nombre"]),
                        key=f"editar_user_nombre_{username_sel}",
                    )
                    rol_nuevo = st.selectbox(
                        "Rol", ROLES_DISPONIBLES, index=ROLES_DISPONIBLES.index(rol_actual),
                        format_func=lambda r: NOMBRES_ROL[r],
                        key=f"editar_user_rol_{username_sel}",
                    )
                    activo_nuevo = st.checkbox(
                        "Activo", value=_valor_default_bool(fila_usuario["activo"]),
                        key=f"editar_user_activo_{username_sel}",
                    )
                    nueva_password = st.text_input(
                        "Nueva contraseña (déjalo vacío para no cambiarla)", type="password",
                        key=f"editar_user_password_{username_sel}",
                    )
                    guardar_usuario = st.form_submit_button("💾 Guardar cambios")
                if guardar_usuario:
                    cambios = {"nombre": nombre_nuevo, "rol": rol_nuevo, "activo": activo_nuevo}
                    if nueva_password:
                        cambios["password_hash"] = hash_password(nueva_password)
                    db.update_row("usuarios", "username", username_sel, cambios)
                    st.success(f"Usuario {username_sel} actualizado.")
                    st.rerun()
                st.caption(
                    "Por seguridad no hay botón de eliminar usuarios — si alguien ya no "
                    "debe entrar, desmarca 'Activo' en vez de borrarlo (así se conserva "
                    "quién registró qué en el historial)."
                )
