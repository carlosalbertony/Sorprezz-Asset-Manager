SORPREZZ ASSET MANAGER V1.8.0
=============================

OBJETIVO
--------
Sorprezz Asset Manager organiza bibliotecas grandes de imágenes y carpetas. El programa no es un catálogo comercial: su función es incorporar material, clasificarlo, visualizarlo, etiquetarlo, agruparlo en colecciones y mantener sincronizados los archivos físicos con la aplicación.

ARQUITECTURA DE USO
-------------------
Inicio -> Agregar material -> Biblioteca -> Colecciones -> Organización -> Configuración

1. AGREGAR MATERIAL
- Google Drive: una carpeta o varias carpetas por lote.
- Manual: crea un recurso físico vacío dentro de Biblioteca.
- Selección o creación rápida de categoría y subcategoría.
- Selección o creación de etiquetas.
- Colección opcional.
- Sugerencias de nombres existentes.
- Opción para mantener la misma clasificación en cargas consecutivas.

2. BIBLIOTECA
- Recursos descargados y recursos manuales en un mismo lugar.
- Navegación visual por carpetas y subcarpetas.
- Galería y lista.
- Búsqueda global por nombre, carpeta, recurso, categoría y etiqueta.
- Crear carpetas.
- Renombrar archivos y carpetas.
- Copiar y mover selecciones.
- Eliminar elementos físicos con confirmación.
- Abrir archivos o carpetas directamente en Windows.
- ZIP de recursos y selecciones.
- Importar archivos desde la computadora a cualquier carpeta.
- Importar una carpeta completa conservando su estructura interna.

3. IMPORTACIÓN LOCAL V1.8
- El botón Importar archivos usa el selector estándar de Windows/navegador embebido.
- Ya no depende de que pywebview exponga choose_files en el momento exacto.
- Se admite selección múltiple.
- La carga se envía por localhost mediante multipart y se copia en la carpeta actual.
- El botón Importar carpeta permite seleccionar una carpeta local completa y conservar subcarpetas.
- Después de importar, Sorprezz reindexa automáticamente el recurso y las nuevas imágenes aparecen en la galería.

4. ETIQUETAS
- Etiquetas en recursos, carpetas e imágenes individuales.
- Etiquetado masivo de imágenes seleccionadas.
- Al etiquetar un recurso o carpeta puede aplicarse la misma etiqueta a todas sus imágenes.
- Organización > Etiquetas muestra conteos reales y hasta cuatro miniaturas de ejemplo.
- Ver imágenes abre Biblioteca filtrada por la etiqueta.
- Las rutas se normalizan para Windows y la sincronización conserva asociaciones de etiquetas.
- Antes de una sincronización global se crea una copia de seguridad de la base de datos.

5. SINCRONIZACIÓN
- Botón Sincronizar visible permanentemente.
- Sincronizar recurso para cambios en una sola carpeta.
- Sincronizar todo para cambios hechos directamente en Windows.
- Reindexa archivos, cantidades y tamaños sin borrar etiquetas.
- La respuesta de sincronización verifica los vínculos de etiquetas antes y después.

6. COLECCIONES
- Reúnen copias de imágenes de distintos recursos sin modificar originales.
- Crear desde una selección o desde una etiqueta.
- Agregar nuevas imágenes a una colección existente.
- Carpeta física independiente dentro de Colecciones_Web.
- Exportación ZIP.

7. ORGANIZACIÓN
- Categorías y subcategorías.
- Etiquetas.
- No existen Plantillas ni Catálogo en la interfaz de V1.8.

DATOS DEL USUARIO
-----------------
Base de datos:
%LOCALAPPDATA%\SorprezzAssetManager\data\sorprezz.db

Biblioteca:
la carpeta definida por el usuario, normalmente Documentos\SorprezzLibrary.

Las actualizaciones de la aplicación no deben borrar la Biblioteca ni la base de datos.

ACTUALIZACIÓN
-------------
V1.8.0 puede instalarse encima de V1.7.x o versiones anteriores. No desinstalar primero.

Para compilar mediante GitHub Actions, subir a la raíz del repositorio los archivos actualizados, incluyendo desktop.py y requirements.txt. El workflow actual puede mantenerse.

NOTA GOOGLE DRIVE
-----------------
El motor de Google Drive sigue basado en enlaces compartidos/gdown. La gestión local, importación manual, etiquetas, colecciones y sincronización son independientes de esa limitación. Una integración OAuth oficial de Google Drive sería una mejora futura para verificar descargas remotas de forma exhaustiva.
