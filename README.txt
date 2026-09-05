SORPREZZ ASSET MANAGER V1.7.0
================================

OBJETIVO
--------
Sorprezz Asset Manager organiza bibliotecas grandes de imágenes, carpetas y recursos. La V1.7 permite incorporar material tanto desde Google Drive como manualmente, sin duplicar herramientas.

ARQUITECTURA FINAL
------------------
1. Inicio
   Resumen y actividad reciente.

2. Agregar material
   Una sola entrada para incorporar contenido a Biblioteca.
   FUENTES:
   - Google Drive: una carpeta o varias por lote.
   - Manual: crea una carpeta/recurso vacío directamente en Biblioteca.
   En ambos casos puedes definir nombre, categoría, subcategoría, etiquetas y colección opcional.

3. Biblioteca
   Centro principal de administración de archivos.
   - Recursos descargados y manuales.
   - Fuente visible: Google Drive o Manual.
   - Navegación por carpetas.
   - Galería y lista.
   - Crear carpetas.
   - Importar archivos desde la computadora a la carpeta actual.
   - Abrir la ubicación física en Windows.
   - Renombrar, copiar, mover, eliminar, etiquetar y comprimir.
   - Editar nombre, categoría, subcategoría y etiquetas de un recurso.
   - Buscar recursos e imágenes.
   - Sincronizar cambios hechos manualmente en Windows.

4. Colecciones
   Reúne copias de imágenes de diferentes partes de Biblioteca sin alterar los originales.
   - Crear colección vacía.
   - Crear desde selección.
   - Crear desde etiqueta.
   - Agregar material después.
   - Abrir carpeta física y exportar ZIP.

5. Organización
   Solo contiene herramientas que aportan al flujo:
   - Categorías y subcategorías.
   - Etiquetas.
   Se elimina Plantillas porque no era necesaria para el uso real.

6. Configuración
   Ubicación principal de Biblioteca y utilidades técnicas.

SINCRONIZACIÓN
---------------
La opción “Sincronizar todo” está siempre visible en la barra lateral y también en el encabezado.
Si agregas, borras, mueves o renombras archivos directamente desde Windows, la sincronización vuelve a indexar todos los recursos locales.
También puedes sincronizar un recurso individual desde Biblioteca.

IMPORTACIÓN LOCAL
-----------------
Dentro de cualquier carpeta de Biblioteca puedes usar “Importar archivos”.
Se abre el selector nativo de Windows, eliges uno o varios archivos y Sorprezz los copia a la carpeta actual, los indexa y actualiza conteos.

CLASIFICACIÓN MANUAL
--------------------
Los recursos pueden crearse sin enlace. Se guardan con Fuente: Manual.
Posteriormente se puede editar nombre, categoría, subcategoría y etiquetas. Si cambia la clasificación, Sorprezz mueve la carpeta física a la ubicación correspondiente dentro de Biblioteca.

COMPATIBILIDAD
--------------
La V1.7 puede instalarse encima de V1.6 y versiones anteriores.
No desinstales antes de actualizar.
La base de datos está en:
%LOCALAPPDATA%\SorprezzAssetManager\data

Las tablas antiguas de funciones retiradas pueden conservarse internamente para evitar pérdida de datos durante la migración, pero ya no aparecen en la interfaz.

GOOGLE DRIVE
------------
El motor actual sigue utilizando enlaces compartidos mediante gdown. La V1.7 mejora la incorporación, organización manual, importación local y sincronización, pero todavía no integra OAuth oficial de Google Drive.
