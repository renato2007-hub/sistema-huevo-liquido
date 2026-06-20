# Sistema de producción — huevo líquido pasteurizado

Esqueleto funcional en Streamlit con almacenamiento en Google Sheets (vía
Google Drive/Sheets API), con los 5 módulos: Bodega de materia prima,
Bodega de envases e insumos, Producción de semielaborados, Pasteurización
y envasado, y Cuarto frío — más un módulo de Catálogos y configuración.

## 1. Crear el Google Sheet que servirá de base de datos

1. Ve a [sheets.google.com](https://sheets.google.com) y crea una hoja de
   cálculo nueva en blanco (el nombre no importa).
2. Cópiala desde la URL el **ID del documento**: es la parte larga entre
   `/d/` y `/edit`, por ejemplo:
   `https://docs.google.com/spreadsheets/d/ESTE_ES_EL_ID/edit`
3. Guarda ese ID, lo necesitarás en el paso 4.

No hace falta crear las pestañas (tabs) manualmente — el sistema las crea
solo la primera vez que se conecta, con los encabezados correctos.

## 2. Crear el proyecto y la cuenta de servicio en Google Cloud

1. Entra a [console.cloud.google.com](https://console.cloud.google.com) con
   tu cuenta de Google y crea un proyecto nuevo (o usa uno existente).
2. En el buscador superior escribe **"Google Sheets API"**, ábrela y pulsa
   **Habilitar**. Repite lo mismo para **"Google Drive API"**.
3. Ve a **IAM y administración → Cuentas de servicio** (menú lateral) y
   pulsa **Crear cuenta de servicio**.
4. Ponle un nombre (ej. `huevo-liquido-sheets`), pulsa **Crear y continuar**,
   deja el rol vacío (no hace falta ningún rol de proyecto) y pulsa **Listo**.
5. Entra a la cuenta de servicio recién creada → pestaña **Claves** →
   **Agregar clave → Crear clave nueva → JSON**. Esto descarga un archivo
   `.json` a tu computadora — guárdalo en un lugar seguro, no lo compartas.
6. Abre ese archivo JSON: contiene los campos `client_email`,
   `private_key`, `project_id`, etc. — son los que vas a copiar al archivo
   de secretos en el paso 4.

## 3. Compartir el Google Sheet con la cuenta de servicio

1. Abre el archivo JSON descargado y copia el valor de `client_email`
   (se ve como `algo@tu-proyecto.iam.gserviceaccount.com`).
2. Abre tu Google Sheet (el del paso 1) → botón **Compartir** → pega ese
   correo y dale permiso de **Editor**.

Sin este paso la cuenta de servicio no podrá leer ni escribir en tu hoja,
aunque las credenciales sean correctas.

## 4. Configurar los secretos de Streamlit

1. Copia `.streamlit/secrets_example.toml` a `.streamlit/secrets.toml`.
2. Reemplaza `spreadsheet_id` con el ID que guardaste en el paso 1.
3. Copia cada campo del archivo JSON descargado al bloque
   `[gcp_service_account]` (mismos nombres de campo). El campo
   `private_key` debe conservar los `\n` tal como vienen en el JSON.
4. Si vas a desplegar en Streamlit Community Cloud, pega el mismo
   contenido en **Settings → Secrets** de tu app en vez de usar el archivo
   local.

## 5. Instalar y correr

```bash
pip install -r requirements.txt
streamlit run app.py
```

La primera vez que abras la app no habrá usuarios — el sistema te dejará
crear el primer usuario administrador directamente desde la pantalla de
inicio. Después, entra a **Catálogos y configuración** y carga, en este
orden, antes de registrar movimientos:

1. Galpones propios y proveedores
2. Categorías de huevo (con su rendimiento teórico — kg por cubeta, %
   clara, % yema, % cáscara; crea una categoría por cada tamaño que
   manejen, porque el rendimiento varía según el tamaño del huevo)
3. Insumos de limpieza
4. Presentaciones de envase (0.5, 1, 2, 3.8, 5 kg, etc.)
5. Personal de producción
6. Clientes

## Notas de diseño (para cuando profundicemos cada módulo)

- **Costeo**: cada lote de huevo conserva su propio costo de compra. El
  "promedio ponderado" se calcula en el momento del consumo, cuando una
  producción combina varios lotes — así no se pierde la trazabilidad
  lote por lote.
- **FEFO editable**: al registrar una producción, el sistema sugiere qué
  lotes de huevo usar (los que vencen antes primero) pero la tabla es
  editable — pueden cambiar las cantidades o agregar/quitar lotes.
- **Autenticación**: simple, por usuario y contraseña, sin roles por
  módulo. El hash de contraseña usado (SHA-256) es suficiente para este
  esqueleto; si lo llevan a producción real, conviene migrar a bcrypt o
  argon2.
- **Siguiente paso natural**: agregar reportes (costos por lote, mermas
  acumuladas, valor de inventario por módulo) y, más adelante, los
  modelos de predicción de rendimiento/demanda sobre estos mismos datos,
  ya que quedan estructurados y trazables desde el día uno.
