SORPREZZ ASSET MANAGER V1.1
===========================

OBJETIVO DE ESTA VERSIÓN
Esta versión está preparada para convertirse en un instalador real de Windows:

  Sorprezz-Asset-Manager-Setup-v1.1.0.exe

El usuario final NO necesita instalar Python, FastAPI, gdown ni abrir una consola.
Todas las dependencias quedan empaquetadas dentro de la aplicación.

CAMBIO PRINCIPAL RESPECTO A V1.0
- La interfaz se abre en su propia ventana de Windows, no como una pestaña normal del navegador.
- Incluye selector nativo de carpeta para elegir la biblioteca.
- Incluye configuración de PyInstaller.
- Incluye instalador Inno Setup.
- Incluye flujo de GitHub Actions para compilar automáticamente en Windows.

FUNCIONES
- Categorías y subcategorías.
- Registro de enlaces de Google Drive.
- Detección de enlaces duplicados.
- Descarga de carpetas y subcarpetas compartidas.
- Organización Biblioteca/Categoría/Subcategoría/Nombre.
- Historial y estados de descarga.
- Buscador.
- Indexación por tipo y tamaño de archivo.
- Abrir carpeta local.
- Reindexar.
- Crear ZIP.
- Elegir ubicación de la biblioteca.

DATOS DEL USUARIO
La base de datos se guarda fuera de la carpeta de instalación en:
  %LOCALAPPDATA%\SorprezzAssetManager\data

La biblioteca se guarda donde el usuario decida, por ejemplo:
  D:\SORPREZZ_LIBRARY

Por lo tanto, actualizar o reinstalar la aplicación no debería borrar la biblioteca ni la base de datos.

COMPILAR AUTOMÁTICAMENTE CON GITHUB ACTIONS
1. Crear un repositorio nuevo en GitHub.
2. Subir el contenido de esta carpeta a la raíz del repositorio.
3. Entrar en Actions -> Crear instalador de Windows.
4. Pulsar Run workflow.
5. Cuando termine, descargar el artefacto:
   Sorprezz-Asset-Manager-Windows-Installer
6. Dentro estará el instalador .exe.

COMPILAR MANUALMENTE EN WINDOWS
Requisitos solo para la computadora del desarrollador:
- Python 3.12
- Inno Setup 6

Ejecutar:
  build_windows.bat

El instalador resultante aparecerá en:
  release\Sorprezz-Asset-Manager-Setup-v1.1.0.exe

NOTA SOBRE GOOGLE DRIVE
V1.1 utiliza gdown para enlaces compartidos. La futura V2 deberá integrar OAuth oficial de Google Drive para carpetas privadas, mejor manejo de límites y descargas más robustas.

CAMBIOS V1.2.0
- Corrige falsos estados "Con error" cuando Google Drive sí dejó archivos descargados.
- La app ahora indexa siempre el contenido local aunque gdown termine con una excepción.
- Nuevo estado "Descargado con aviso" para carpetas utilizables con posibles archivos pendientes.
- Al abrir la V1.2 se reparan automáticamente registros antiguos con 0 archivos si su carpeta local contiene material.
- "Abrir carpeta" reindexa automáticamente el contenido para mantener conteo y tamaño sincronizados.
- Reintentar descarga conserva e indexa lo que ya existe.
