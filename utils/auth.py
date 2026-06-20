"""
Autenticacion simple por usuario y contrasena, contra la pestana 'usuarios'
del Google Sheet, mas el rol de cada usuario (admin / gerencia / jefe_planta
/ supervisor) que define que modulos ve y si ve costos -- ver utils/permisos.py.

Nota: para un entorno de produccion mas exigente, reemplaza el hash sha256
por bcrypt o argon2. Aqui se deja simple para que el esqueleto funcione sin
dependencias adicionales.
"""
from __future__ import annotations
import hashlib
import streamlit as st
from utils.permisos import ROLES_DISPONIBLES, NOMBRES_ROL, rol_normalizado


def hash_password(password: str) -> str:
    return hashlib.sha256(password.encode("utf-8")).hexdigest()


def login(db):
    """
    Muestra el formulario de login (o de creacion del primer usuario, si
    'usuarios' esta vacio -- ese primero siempre se crea como admin). Si ya
    hay sesion activa devuelve (username, rol); si no, detiene la ejecucion
    de la app hasta que el usuario inicie sesion.
    """
    if "username" in st.session_state:
        return st.session_state["username"], st.session_state.get("rol", "admin")

    st.title("Sistema de producción — Huevo líquido")

    df_usuarios = db.get_df("usuarios")

    if df_usuarios.empty:
        st.info("Todavía no hay usuarios creados. Crea el primer usuario administrador.")
        with st.form("primer_usuario"):
            username = st.text_input("Usuario")
            nombre = st.text_input("Nombre completo")
            password = st.text_input("Contraseña", type="password")
            crear = st.form_submit_button("Crear usuario y entrar")
        if crear:
            if not username or not password:
                st.error("Usuario y contraseña son obligatorios.")
                st.stop()
            db.append_row("usuarios", {
                "username": username,
                "password_hash": hash_password(password),
                "nombre": nombre,
                "rol": "admin",
                "activo": True,
            })
            st.session_state["username"] = username
            st.session_state["rol"] = "admin"
            st.rerun()
        st.stop()

    with st.form("login_form"):
        username = st.text_input("Usuario")
        password = st.text_input("Contraseña", type="password")
        enviado = st.form_submit_button("Ingresar")

    if enviado:
        fila = df_usuarios[df_usuarios["username"] == username]
        if fila.empty:
            st.error("Usuario no encontrado.")
        elif str(fila.iloc[0]["activo"]).upper() not in ("TRUE", "1", "SI", "SÍ"):
            st.error("Usuario inactivo. Contacta al administrador.")
        elif fila.iloc[0]["password_hash"] != hash_password(password):
            st.error("Contraseña incorrecta.")
        else:
            st.session_state["username"] = username
            st.session_state["rol"] = rol_normalizado(fila.iloc[0].get("rol", ""))
            st.rerun()

    st.stop()
