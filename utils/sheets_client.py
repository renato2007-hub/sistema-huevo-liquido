"""
Capa de acceso a datos: todo el sistema lee y escribe en Google Sheets a
traves de esta clase. Si en el futuro migran a otra base de datos (Postgres,
BigQuery, etc.), solo hay que reescribir este archivo -- el resto del
sistema (los modulos) no deberia cambiar porque solo conoce estos metodos:
get_df, append_row, update_row, siguiente_id.

IMPORTANTE sobre la cuota de la API de Google Sheets: el plan gratuito
permite 60 lecturas y 60 escrituras por minuto POR USUARIO. Streamlit
vuelve a ejecutar todo el script en cada clic, asi que si no cacheamos
nada, cada clic dispara de nuevo la conexion y la lectura de las ~16
pestanas -- y se agota la cuota en segundos. Por eso:
  1. get_db() (al final de este archivo) cachea la instancia de SheetsDB
     con @st.cache_resource, para que la conexion y la verificacion de
     esquema corran UNA sola vez por sesion del servidor, no en cada clic.
  2. get_df() guarda los datos en memoria un rato corto (CACHE_TTL) y los
     reusa si se piden de nuevo antes de que expire.
  3. Cada escritura (append_row / update_row) invalida solo el cache de
     esa pestana puntual, para que la siguiente lectura traiga el dato
     fresco sin tener que re-leer las demas 15 pestanas.

IMPORTANTE sobre numeros y el idioma (locale) de la hoja de calculo: le
pedimos a la API los valores SIN FORMATEAR (UNFORMATTED_VALUE) en lugar de
dejar que gspread los "numerice" a partir del texto mostrado. La razon:
gspread trae por defecto el valor ya formateado como lo veria una persona
(por ejemplo "1,65" en una hoja en espanol) y despues intenta convertirlo a
numero el mismo asumiendo la convencion de EE.UU. (coma = separador de
miles) -- por lo que "1,65" terminaba leyendose como 165. Pedir el valor
sin formatear evita ese problema de raiz, porque la API devuelve el numero
real (1.65) directamente, sin pasar por texto.

IMPORTANTE sobre cortes de red: el uso diario va a tener wifi imperfecto
(planta, oficina, etc.). _con_reintentos() reintenta automaticamente ante
cortes momentaneos de conexion antes de mostrar un error real al usuario.
"""
import time
import streamlit as st
import gspread
import requests
from gspread.utils import rowcol_to_a1, ValueInputOption, ValueRenderOption
import pandas as pd
from google.oauth2.service_account import Credentials
from config import SHEET_SCHEMAS

SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/drive",
]

CACHE_TTL = 20  # segundos que se reusa una lectura antes de pedirla de nuevo
REINTENTOS = 3
ESPERA_ENTRE_REINTENTOS = 2  # segundos


def _a_tipo_nativo(v):
    """Convierte tipos numericos de numpy/pandas (numpy.int64, numpy.float64,
    numpy.bool_, etc.) a su equivalente nativo de Python. Estos tipos salen
    seguido de calculos con pandas (sumas, promedios) y la libreria que
    manda los datos a Google no sabe convertirlos a JSON por su cuenta."""
    if hasattr(v, "item") and not isinstance(v, (str, bytes)):
        try:
            return v.item()
        except (ValueError, AttributeError):
            return v
    return v


def _con_reintentos(funcion, *args, **kwargs):
    """Ejecuta funcion(*args, **kwargs); si falla por un problema de red
    transitorio (wifi cortado, timeout, etc.) reintenta unas pocas veces
    antes de rendirse y dejar pasar el error real."""
    ultimo_error = None
    for intento in range(1, REINTENTOS + 1):
        try:
            return funcion(*args, **kwargs)
        except (requests.exceptions.ConnectionError, requests.exceptions.Timeout) as e:
            ultimo_error = e
            if intento < REINTENTOS:
                time.sleep(ESPERA_ENTRE_REINTENTOS)
    raise ultimo_error


@st.cache_resource(show_spinner=False)
def _conectar():
    creds_info = dict(st.secrets["gcp_service_account"])
    creds = Credentials.from_service_account_info(creds_info, scopes=SCOPES)
    cliente = gspread.authorize(creds)
    spreadsheet_id = st.secrets["spreadsheet_id"]
    return _con_reintentos(cliente.open_by_key, spreadsheet_id)


class SheetsDB:
    def __init__(self):
        self.sh = _conectar()
        self._ws_cache = {}     # nombre de pestana -> objeto Worksheet (evita relistar)
        self._data_cache = {}   # nombre de pestana -> (timestamp, DataFrame)
        self._asegurar_esquema()

    def _asegurar_esquema(self):
        """Crea las pestanas y encabezados que falten. Nunca borra datos existentes.
        Solo corre una vez gracias a get_db() + st.cache_resource."""
        existentes = {ws.title: ws for ws in _con_reintentos(self.sh.worksheets)}  # 1 sola lectura
        self._ws_cache.update(existentes)
        for nombre, columnas in SHEET_SCHEMAS.items():
            if nombre not in existentes:
                ws = _con_reintentos(self.sh.add_worksheet, title=nombre, rows=2000, cols=len(columnas) + 2)
                _con_reintentos(ws.append_row, columnas)
                self._ws_cache[nombre] = ws
            else:
                ws = existentes[nombre]
                primer_fila = _con_reintentos(ws.row_values, 1)
                if not primer_fila:
                    _con_reintentos(ws.append_row, columnas)

    def _ws(self, nombre):
        if nombre not in self._ws_cache:
            self._ws_cache[nombre] = _con_reintentos(self.sh.worksheet, nombre)
        return self._ws_cache[nombre]

    def get_df(self, nombre, forzar_actualizacion: bool = False) -> pd.DataFrame:
        ahora = time.time()
        if not forzar_actualizacion and nombre in self._data_cache:
            ts, df_cacheado = self._data_cache[nombre]
            if ahora - ts < CACHE_TTL:
                return df_cacheado.copy()

        ws = self._ws(nombre)
        registros = _con_reintentos(ws.get_all_records, value_render_option=ValueRenderOption.unformatted)
        columnas = SHEET_SCHEMAS[nombre]
        df = pd.DataFrame(columns=columnas) if not registros else pd.DataFrame(registros)
        self._data_cache[nombre] = (ahora, df)
        return df.copy()

    def _invalidar(self, nombre):
        self._data_cache.pop(nombre, None)

    def append_row(self, nombre, fila: dict):
        columnas = SHEET_SCHEMAS[nombre]
        valores = []
        for c in columnas:
            v = _a_tipo_nativo(fila.get(c, ""))
            if isinstance(v, bool):
                v = "TRUE" if v else "FALSE"
            valores.append(v)
        _con_reintentos(self._ws(nombre).append_row, valores, value_input_option=ValueInputOption.raw)
        self._invalidar(nombre)

    def update_row(self, nombre, id_col: str, id_valor, cambios: dict):
        """Actualiza la primera fila donde id_col == id_valor. Devuelve True si la encontro.
        Usa ws.update() (no update_cell) porque update_cell de gspread fuerza
        USER_ENTERED internamente, sin poder cambiarlo -- y eso es lo que
        causaba que los decimales se guardaran mal en hojas con locale en
        espanol. ws.update() si permite forzar RAW."""
        ws = self._ws(nombre)
        columnas = SHEET_SCHEMAS[nombre]
        idx_id = columnas.index(id_col)
        celdas = _con_reintentos(ws.col_values, idx_id + 1)
        for fila_num, valor in enumerate(celdas[1:], start=2):  # fila 1 = encabezado
            if str(valor) == str(id_valor):
                for campo, nuevo_valor in cambios.items():
                    idx_campo = columnas.index(campo)
                    rango = rowcol_to_a1(fila_num, idx_campo + 1)
                    _con_reintentos(ws.update, values=[[_a_tipo_nativo(nuevo_valor)]], range_name=rango, raw=True)
                self._invalidar(nombre)
                return True
        return False

    def delete_row(self, nombre, id_col: str, id_valor) -> bool:
        """Elimina la primera fila donde id_col == id_valor. Devuelve True si la encontro."""
        ws = self._ws(nombre)
        columnas = SHEET_SCHEMAS[nombre]
        idx_id = columnas.index(id_col)
        celdas = _con_reintentos(ws.col_values, idx_id + 1)
        for fila_num, valor in enumerate(celdas[1:], start=2):  # fila 1 = encabezado
            if str(valor) == str(id_valor):
                _con_reintentos(ws.delete_rows, fila_num)
                self._invalidar(nombre)
                return True
        return False

    def delete_rows_where(self, nombre, columna: str, valor) -> int:
        """Elimina TODAS las filas donde columna == valor (ej. todas las filas
        de detalle ligadas a un lote que se esta corrigiendo/eliminando).
        Devuelve cuantas filas elimino."""
        ws = self._ws(nombre)
        columnas = SHEET_SCHEMAS[nombre]
        idx_col = columnas.index(columna)
        celdas = _con_reintentos(ws.col_values, idx_col + 1)
        filas_a_borrar = [
            fila_num for fila_num, valor_celda in enumerate(celdas[1:], start=2)
            if str(valor_celda) == str(valor)
        ]
        # borrar de abajo hacia arriba para que no se corran los numeros de fila
        for fila_num in reversed(filas_a_borrar):
            _con_reintentos(ws.delete_rows, fila_num)
        if filas_a_borrar:
            self._invalidar(nombre)
        return len(filas_a_borrar)

    def siguiente_id(self, nombre, prefijo, fecha) -> str:
        """Genera un codigo tipo PREFIJO-AAAAMMDD-001, consecutivo por dia."""
        fecha_str = fecha.strftime("%Y%m%d")
        base = f"{prefijo}-{fecha_str}"
        df = self.get_df(nombre, forzar_actualizacion=True)
        columnas = SHEET_SCHEMAS[nombre]
        id_col = columnas[0]
        if df.empty or id_col not in df.columns:
            n = 1
        else:
            n = int(df[id_col].astype(str).str.startswith(base).sum()) + 1
        return f"{base}-{n:03d}"


@st.cache_resource(show_spinner="Conectando con Google Sheets...")
def get_db() -> "SheetsDB":
    """Punto de entrada unico: devuelve siempre la MISMA instancia de SheetsDB
    durante la vida del proceso de Streamlit, en vez de crear una nueva (y
    re-verificar las 16 pestanas) en cada clic. Usa esta funcion en app.py,
    nunca instancies SheetsDB() directamente."""
    return SheetsDB()
