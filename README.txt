SORPREZZ ASSET MANAGER V1.6.0
================================

OBJETIVO
--------
Sorprezz Asset Manager organiza grandes bibliotecas de imágenes y carpetas descargadas desde enlaces compartidos.
La V1.6 simplifica la arquitectura para evitar funciones duplicadas.

MENÚ PRINCIPAL
--------------
1. Inicio
   Resumen, actividad reciente y accesos rápidos.

2. Descargas
   Registra enlaces y clasifica el material antes de descargarlo.
   - Una carpeta o varias carpetas por lote.
   - Nombre nuevo o sugerido desde nombres ya registrados.
   - Categoría existente o creación rápida.
   - Subcategoría existente o creación rápida.
   - Etiquetas existentes o nuevas.
   - Colección opcional existente o nueva.
   - Prevención de enlaces duplicados.
   - Opción de mantener la clasificación para la siguiente descarga.
   - Descarga y descarga + agregar otro.
   La estructura original y la indexación se conservan automáticamente porque son necesarias para Biblioteca.

3. Biblioteca
   Fusiona las antiguas secciones Biblioteca y Explorador.
   - Buscar recursos por nombre, categoría, subcategoría, etiqueta o enlace.
   - Buscar imágenes globalmente por nombre, carpeta, recurso o etiqueta.
   - Navegar estructura física de carpetas.
   - Vista Galería y Lista.
   - Crear y renombrar carpetas.
   - Etiquetar recursos, carpetas e imágenes.
   - Selección múltiple.
   - Copiar, mover y descargar copias.
   - Crear ZIP de una selección.
   - Crear colecciones a partir de selecciones.
   - Abrir archivos o carpetas en Windows.
   - Reindexar y sincronizar cambios hechos manualmente en Windows.
   No existe una sección Buscar separada: la búsqueda está integrada en Biblioteca.

4. Colecciones
   Agrupa copias de imágenes provenientes de cualquier parte de Biblioteca.
   - Los originales no se modifican.
   - Crear colección desde una selección.
   - Crear colección desde una etiqueta.
   - Crear colección vacía.
   - Agregar imágenes posteriormente.
   - Abrir la carpeta física.
   - Descargar la colección como ZIP.
   - Buscar colecciones por nombre, categoría o descripción.

5. Organización
   Fusiona Categorías, Etiquetas y Plantillas.
   - Categorías y subcategorías.
   - Etiquetas reutilizables.
   - Plantillas de carpetas.
   - Aplicar una plantilla para crear carpetas físicas sin alterar archivos existentes.

6. Configuración
   Ubicación de la biblioteca y datos técnicos de la aplicación.

CAMBIOS DE ARQUITECTURA
-----------------------
- Se elimina Catálogo del flujo y de la interfaz.
- Se elimina Explorador como sección independiente; sus funciones pasan a Biblioteca.
- Categorías y Etiquetas/Listas pasan a Organización.
- No se crea una sección Buscar independiente.
- La app queda centrada exclusivamente en descargar, buscar, visualizar, clasificar, etiquetar, agrupar y reorganizar imágenes.

COMPATIBILIDAD
--------------
La V1.6 puede instalarse encima de versiones anteriores.
No desinstales antes de actualizar.
La base de datos está en:
%LOCALAPPDATA%\SorprezzAssetManager\data

La biblioteca está en la ubicación elegida por el usuario.
Las tablas y carpetas heredadas de funciones antiguas pueden conservarse internamente para no perder información, pero ya no forman parte del flujo de la V1.6.

GOOGLE DRIVE
------------
El motor de descarga actual sigue usando enlaces compartidos mediante gdown.
La V1.6 optimiza la organización y el flujo de trabajo, pero no incorpora todavía OAuth oficial de Google Drive.
