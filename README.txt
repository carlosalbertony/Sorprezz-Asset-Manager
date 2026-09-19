SORPREZZ ASSET MANAGER V1.9.2

V1.9.2 - COLECCIONES COMPARTIDAS Y CAMBIOS DESDE WINDOWS
-----------------------------------------------------
- OneDrive no es necesario para compartir la aplicación. El servidor sigue siendo
  la computadora principal, y todos usan su misma biblioteca a través del navegador.
- La ubicación del ejecutable no decide qué imágenes se ven: cada colección usa su
  carpeta física registrada. Sus detalles y el explorador ahora muestran esa ruta.
- Las colecciones detectan archivos copiados, movidos, renombrados, reemplazados o
  eliminados desde Windows, incluyendo subcarpetas. No hace falta volver a importarlos.
- Las ventanas de colecciones abiertas se actualizan cada 3 segundos mientras la
  pestaña está visible; las confirmaciones y los formularios permanecen protegidos.
- Sincronizar todo también revisa las colecciones. Cada colección tiene además un
  botón propio para sincronizar y muestra cualquier error de lectura de su carpeta.
- Abrir en Windows abre la carpeta en la computadora principal cuando la solicitud
  proviene de ella. Desde otra computadora, Explorar carpeta abre una vista navegable
  dentro del programa con subcarpetas, imágenes y descarga de archivos.
- No se vuelve a crear automáticamente una copia que se haya quitado desde Windows.
  Las eliminaciones hechas desde Sorprezz siguen pasando por la Papelera compartida.
- El instalador actualizado se llama Sorprezz-Asset-Manager-Setup-v1.9.2.exe.

PAPELERA COMPARTIDA Y CONFIRMACIONES
----------------------------------
- El cierre de las ventanas tiene su propia fila. Enviar una colección o recurso
  a la Papelera se hace desde una zona separada al pie de sus detalles.
- Cada eliminación de contenido pide confirmación con Cancelar como opción inicial.
- La sección Papelera permite buscar, restaurar o eliminar definitivamente recursos,
  carpetas, archivos, colecciones, copias de colección, etiquetas y listas de carpetas.
- Se guardan archivos y metadatos en el servidor; todos los usuarios ven la misma
  Papelera. Los datos persisten al reiniciar y no se borran automáticamente.
- Restaurar conserva etiquetas y clasificación. Nunca sobrescribe un archivo o
  registro nuevo: si hay un conflicto, el elemento permanece en la Papelera.
- Si se eliminaron un hijo y su recurso/colección por separado, restaura primero
  el recurso/colección. Para purgar el padre, resuelve primero sus hijos separados.
- Eliminar definitivamente exige otra confirmación y no afecta a los originales
  de Biblioteca al borrar copias de colecciones. Sincronizar no vuelve a agregar
  automáticamente una copia retirada, incluso si ya se vació de la Papelera.
- Las operaciones de escritura se coordinan para evitar que dos usuarios borren,
  importen o restauren el mismo contenido a la vez. Una descarga activa debe terminar
  antes de enviar su recurso o sus archivos a la Papelera.
- Los archivos retirados se guardan en:
  %LOCALAPPDATA%\SorprezzAssetManager\data\Papelera
  Conserva esta carpeta junto con sorprezz.db al respaldar los datos del servidor.
- La Papelera recoge eliminaciones realizadas desde Sorprezz. No recupera borrados
  definitivos anteriores ni intercepta eliminaciones hechas directamente en Windows.

CORRECCIÓN DE SINCRONIZACIÓN EN RED
---------------------------------
- La aplicación instalada y los navegadores deben conectarse al mismo servidor.
- Las carpetas abiertas y la vista de imágenes consultan los cambios cada 3 segundos
  mientras la pestaña está visible y no hay un diálogo abierto.
- Las importaciones realizadas dentro de Sorprezz quedan indexadas al terminar.
- Las imágenes copiadas desde Windows dentro de un recurso existente se ven al
  actualizar su carpeta. Usa Sincronizar recurso o Sincronizar todo para actualizar
  también el índice de imágenes, los conteos y las etiquetas asociadas.
- Sincronizar conserva la subcarpeta abierta y la selección; muestra progreso y
  cualquier carpeta que no pudo procesarse. No descarga cambios nuevos de Drive.
- Se reutilizan huellas de archivos sin cambios y se serializan las indexaciones
  concurrentes. Las conexiones de base de datos se cierran al terminar cada consulta.
- Para aplicar una actualización del programa instalado, compila/instala la nueva
  versión en la computadora que sirve la biblioteca y recarga los navegadores.

Pruebas de regresión (datos temporales, sin tocar la biblioteca personal):
  python -m pip install -r requirements.txt httpx
  python -m unittest discover -s tests -v
  node --test tests/sync_ui.test.js

HOTFIX V1.9.1 - ARRANQUE ESTABLE
- La reindexación automática ya no bloquea el inicio de la interfaz.
- El tiempo de espera del servicio local aumenta de 12 a 60 segundos.
- Si el servidor interno falla, se guarda diagnóstico en %LOCALAPPDATA%\SorprezzAssetManager\data\startup.log.
- No borra Biblioteca, etiquetas, colecciones, cuentas OAuth ni configuración.

=============================

OBJETIVO
--------
Sorprezz Asset Manager organiza grandes bibliotecas de imágenes y recursos:
- Google Drive (API oficial + enlace público de respaldo)
- material creado manualmente
- importación de archivos y carpetas desde Windows
- categorías, subcategorías y etiquetas
- colecciones físicas de imágenes seleccionadas
- búsqueda visual y sincronización con cambios hechos en Windows

NOVEDADES V1.9.1 - GOOGLE DRIVE API OFICIAL
-------------------------------------------
1. Configuración > Google Drive permite cargar localmente el JSON OAuth de tipo Aplicación de escritorio.
2. El JSON se guarda fuera de la carpeta del programa en:
   %LOCALAPPDATA%\SorprezzAssetManager\data\google_oauth_client.json
   No debe subirse a GitHub.
3. Se pueden conectar varias cuentas de Google. Cada cuenta conserva su token local.
4. Se puede elegir una cuenta concreta al agregar material o dejar "Automático".
5. Analizar enlace ahora puede mostrar antes de descargar:
   - nombre real de la carpeta/archivo
   - cuenta que tiene acceso
   - cantidad de archivos
   - cantidad de subcarpetas
   - tamaño conocido
6. Descarga recursiva mediante Google Drive API conservando subcarpetas.
7. Verificación de la descarga: Sorprezz registra cuántos archivos remotos encontró y cuántos pudo descargar.
8. Nuevo estado "Incompleto" cuando realmente quedan elementos pendientes u omitidos.
9. Soporte básico de exportación para Google Docs, Sheets, Slides y Drawings.
10. Accesos directos de Drive se resuelven cuando es posible y se evitan ciclos de carpetas.
11. La descarga pública con gdown permanece como respaldo para enlaces públicos cuando no hay acceso OAuth automático.
12. El permiso usado es drive.readonly: Sorprezz puede ver y descargar Drive, pero no modificar ni borrar contenido remoto.

CONFIGURACIÓN INICIAL DE GOOGLE DRIVE
-------------------------------------
A. En Google Cloud:
- habilitar Google Drive API
- crear pantalla OAuth externa
- agregar usuarios de prueba mientras la app esté en modo Prueba
- crear cliente OAuth de tipo Aplicación de escritorio
- agregar el scope:
  https://www.googleapis.com/auth/drive.readonly
- descargar el JSON del cliente OAuth

B. En Sorprezz:
1. Ir a Configuración > Google Drive.
2. Pulsar "Seleccionar JSON" y elegir el archivo descargado de Google Cloud.
3. Pulsar "+ Conectar cuenta Google".
4. El navegador se abrirá para autorizar la cuenta.
5. Repetir para una segunda cuenta si se necesita.
6. Marcar cuál será la cuenta activa o usar "Automático" al agregar material.

SEGURIDAD
---------
- El JSON OAuth y los tokens se guardan únicamente en los datos locales del usuario.
- .gitignore incluye patrones para evitar subir credenciales accidentalmente.
- Google Drive se usa en solo lectura.
- Biblioteca local sigue siendo el espacio donde Sorprezz copia, mueve, renombra o elimina archivos.

ARQUITECTURA DE LA APP
----------------------
Inicio
Agregar material
Biblioteca
Colecciones
Organización
Configuración

No existe Catálogo ni Plantillas. La aplicación está enfocada en organizar imágenes y carpetas.

DATOS DEL USUARIO
-----------------
Base de datos:
%LOCALAPPDATA%\SorprezzAssetManager\data\sorprezz.db

Credenciales OAuth importadas:
%LOCALAPPDATA%\SorprezzAssetManager\data\google_oauth_client.json

Tokens de cuentas Google:
%LOCALAPPDATA%\SorprezzAssetManager\data\google_tokens\

La Biblioteca permanece en la ubicación elegida por el usuario, por ejemplo:
Documents\SorprezzLibrary

ACTUALIZAR DESDE V1.8.x
-----------------------
Instalar V1.9.2 encima de la versión anterior. No desinstalar ni borrar la Biblioteca.
La migración agrega de forma no destructiva los campos de Google Drive API y la tabla de cuentas.

ARCHIVOS QUE CAMBIAN EN V1.9.1
------------------------------
app.py
desktop.py
requirements.txt
README.txt
.gitignore
build_windows.bat
.github/workflows/build-windows.yml
web/app.js
web/index.html
web/styles.css
installer/SorprezzAssetManager.iss

COMPILACIÓN
-----------
GitHub Actions instala requirements-build.txt, que incluye requirements.txt.
V1.9 agrega google-api-python-client, google-auth-oauthlib y google-auth-httplib2.
El workflow incluye recolección explícita de los submódulos Google necesarios para PyInstaller.

NOTA SOBRE OAUTH EN MODO PRUEBA
-------------------------------
Mientras Google Auth Platform permanezca en estado "Prueba", solo los usuarios de prueba configurados podrán autorizar la aplicación. Google puede exigir reconexiones periódicas y, si la app se distribuye públicamente con scopes restringidos, puede ser necesario completar su proceso de verificación.
