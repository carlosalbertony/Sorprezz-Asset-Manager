SORPREZZ ASSET MANAGER V1.9.1

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
Instalar V1.9.1 encima de la versión anterior. No desinstalar ni borrar la Biblioteca.
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
