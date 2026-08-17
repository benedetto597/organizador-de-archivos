# Organizador de archivos

Ordena una carpeta en subcarpetas por categoría, fecha y origen, y aísla las copias
idénticas en una carpeta `BORRAR` **sin eliminar nada**.

Trabaja en dos modos:

- **`media`** — fotos, videos y animaciones en **tipo → año/mes → origen**. Recorre todo el
  árbol y detecta duplicados hasta a nivel de píxel.
- **`archivos`** — documentos, comprimidos, instaladores, código, audio y demás, en
  **categoría → subtipo → año**. Se limita al primer nivel por seguridad.

Es reutilizable: la ruta es un parámetro, así que el mismo script sirve para una carpeta de
fotos, para un disco externo entero o para tu carpeta de Descargas.

> **Lo que protege tu trabajo.** Un organizador que entra en todas las carpetas destruye
> cualquier cosa que sea *una unidad*: un repositorio git, un `node_modules`, un entorno
> virtual, un programa instalado, un módulo de Odoo. Por eso el modo `archivos` **no entra
> en subcarpetas** salvo que se lo pidas, y aun pidiéndoselo se detiene al reconocer la raíz
> de un proyecto. Además se niega a operar sobre carpetas de sistema, sin excepción.

---

## Instalación

Requiere Python 3.10 o superior. Probado en Python 3.13.7 sobre Windows 11.
Con Python 3.12 o superior se aprovecha además la fecha de creación del archivo como
refinamiento del último recurso para la fecha.

```bash
python -m pip install -r requirements.txt
```

La única dependencia obligatoria es **Pillow** (licencia MIT-CMU, software libre). Todo lo
demás —lectura de fechas de video, hashing, paralelismo, CSV, CLI— usa solo la biblioteca
estándar de Python.

---

## Uso en tres pasos

El script **nunca mueve nada por defecto**. El flujo recomendado es simular, revisar y
aplicar.

### 1. Simular

```bash
python organizar.py "D:\Fotos"
```

Calcula el plan completo, escribe un CSV con cada movimiento propuesto y muestra un
resumen. No toca un solo archivo.

### 2. Revisar

Abre el CSV que se indica al final del resumen:

```
D:\Fotos\_ORGANIZADOR\plan-20260813-120000.csv
```

Una fila por archivo, con origen, destino, tipo, fuente, fecha, de dónde salió esa fecha
y el motivo del movimiento. Se abre en Excel directamente (separador `;`, UTF-8 con BOM).

### 3. Aplicar

```bash
python organizar.py "D:\Fotos" --aplicar-plan "D:\Fotos\_ORGANIZADOR\plan-20260813-120000.csv" --ejecutar
```

Ejecuta exactamente el plan revisado, sin repetir el análisis. Esto importa: en un disco
USB el análisis de 57.000 archivos toma cerca de dos horas, y no tiene sentido pagarla dos
veces.

`--ejecutar` es obligatorio aquí igual que en cualquier otra forma de uso: es la única
puerta por la que el programa escribe en disco. `--aplicar-plan` sin `--ejecutar` vuelve a
simular, ahora leyendo el plan del CSV.

También se puede recalcular y aplicar de una sola vez:

```bash
python organizar.py "D:\Fotos" --ejecutar
```

---

## Resultado

```
D:\Fotos\
├── Fotos\
│   ├── 2019\2019-06\WhatsApp\IMG-20190606-WA0044.jpg
│   ├── 2023\2023-07\Camara\20230726_124814.jpg
│   ├── 2024\2024-06\Capturas\Screenshot_20240608_193933_WhatsApp.jpg
│   ├── 2024\2024-01\Redes-Sociales\FB_IMG_1704399976041.jpg
│   └── Sin-Fecha\Galeria\...
├── Videos\
│   ├── 2022\2022-10\WhatsApp\VID-20221020-WA0039.mp4
│   └── 2024\2024-09\Camara\20240912_233242.mp4
├── Animaciones\
│   └── 2021\2021-03\Otros\risa.gif
├── BORRAR\
│   ├── copia-exacta\Fotos\...          ← bytes idénticos
│   └── copia-pixel\Fotos\...           ← píxeles idénticos, bytes distintos
├── _ORGANIZADOR\
│   ├── plan-20260813-120000.csv        ← detalle de cada movimiento
│   ├── resumen-20260813-120000.json    ← estadísticas de la corrida
│   ├── manifiesto-20260813-120000.jsonl ← para revertir
│   └── log-20260813-120000.txt
└── organizador\                        ← esta herramienta (se excluye sola)
```

---

## Parámetros

| Parámetro | Descripción |
|---|---|
| `ruta` | Carpeta a organizar (posicional). También `--ruta`. |
| `--destino RUTA` | Raíz de salida. Por defecto la misma carpeta de origen (in-place, sin costo de espacio). |
| `--ejecutar` | Aplica los cambios. Sin esta bandera solo simula. |
| `--copiar` | Copia en vez de mover. Requiere espacio libre equivalente. |
| `--forzar` | No pedir confirmación interactiva. |
| `--aplicar-plan CSV` | Usa un plan calculado antes, sin recalcular. Combínalo con `--ejecutar`. |
| `--deshacer JSONL` | Revierte una corrida usando su manifiesto. |
| `--modo MODO` | `media` (predeterminado), `archivos`, `todo`. Ver arriba. |
| `--profundidad N` | Niveles de subcarpeta a recorrer. `1` = solo archivos sueltos, `0` = sin límite. Por defecto: sin límite en `media`, `1` en los demás. |
| `--jerarquia FORMA` | Para fotos y videos: `tipo-fecha-fuente` (predeterminado), `tipo-fecha`, `tipo-fuente`, `fecha`, `tipo`. |
| `--jerarquia-archivos FORMA` | Para el resto: `categoria-subtipo-anio` (predeterminado), `categoria-anio`, `categoria`. |
| `--mes-con-nombre` | `2024-05-Mayo` en lugar de `2024-05`. |
| `--duplicados MODO` | `pixel` (predeterminado), `byte`, `ninguno`. |
| `--prefiltro` | Prefiltro JPEG por escalado DCT: mucho más rápido, exactitud algo menor. |
| `--incluir-ext EXT...` | Extensiones extra a tratar como media (`.raf .insv`). |
| `--excluir CARPETA...` | Nombres de carpeta a ignorar. |
| `--limite N` | Procesar solo N archivos. Útil para probar. |
| `--tamano-minimo BYTES` | Ignorar archivos más chicos. |
| `--reprocesar` | Incluir también las carpetas ya organizadas. |
| `--seguir-enlaces` | Recorrer symlinks y junctions (desactivado por seguridad). |
| `--procesos N` | Procesos paralelos. Por defecto `núcleos × 2`, con tope de 16 (ver Rendimiento). |
| `--sin-manifiesto` | No escribir el registro de reversión (no recomendado). |
| `--log ARCHIVO`, `-v`, `-q` | Control de registro. |

Códigos de salida: `0` bien · `1` hubo movimientos fallidos · `2` configuración inválida ·
`130` interrumpido.

---

## Organizar cualquier carpeta del computador

```bash
python organizar.py "C:\Users\tu-usuario\Downloads" --modo archivos
python organizar.py "D:\Cursos" --modo todo
python organizar.py "D:\Trabajo" --modo archivos --profundidad 2
```

Resultado del modo `archivos`:

```
Documentos\PDF\2024\          Instaladores\Windows\2023\
Documentos\Word\2021\         Instaladores\Imagenes-de-disco\2022\
Documentos\Hojas-de-calculo\  Comprimidos\ZIP\2024\
Documentos\Paginas-guardadas\ Bases-de-datos\Scripts-SQL\2023\
Codigo\Python\2024\           Redes\Packet-Tracer\2022\
Audio\Comprimido\2019\        Juegos\Switch\2024\
Diseno\3D\2021\               Certificados-y-llaves\
Maquinas-virtuales\           Otros-Archivos\<EXT>\
```

Las 17 categorías y sus subtipos están en [categorias.py](mediaorg/categorias.py), calibradas
contra un censo real de 75.481 archivos. La tabla rechaza extensiones duplicadas al importar,
así que una ambigüedad se detecta al arrancar y no en producción; las que existen de verdad
(`.ts` TypeScript vs MPEG, `.key` Keynote vs llave privada, `.pub`, `.sav`, `.3ds`, `.mm`)
están resueltas y documentadas en el propio archivo.

### Qué no toca

| Protección | Detalle |
|---|---|
| Carpetas de sistema | Se **rechaza la corrida** si la ruta contiene `Windows`, `Program Files`, `ProgramData`, `AppData`, `$RECYCLE.BIN`, `System Volume Information`, `found.00x`, `Recovery`, `System32`… en cualquier nivel. Tampoco la raíz del disco del sistema. |
| Raíces de proyecto | `.git`, `package.json`, `requirements.txt`, `pyproject.toml`, `pom.xml`, `__manifest__.py`, `node_modules`, `.venv`, `unins000.exe`, `*.sln`, `*.csproj` y unos treinta más. Se reportan y se dejan enteras. |
| Subcarpetas, por defecto | El modo `archivos` se queda en el primer nivel: lo que esté anidado no se toca. |

La detección de proyectos mira un nivel más abajo en las carpetas que no va a recorrer,
porque un módulo descargado suele quedar como `descarga\modulo_real\__manifest__.py`. Ese
caso real existía en el disco de prueba y se detecta:

```
- D:\Documentos\modulo-descargado   [submodulo_real/__manifest__.py]
- D:\Documentos\paquete-zip-abierto [contenido_real/__manifest__.py]
```

## Cómo decide la fecha

Cascada, de más confiable a menos. El origen elegido se reporta por archivo en el CSV,
así que siempre se puede auditar.

1. **EXIF** `DateTimeOriginal` / `DateTimeDigitized` / `DateTime` (fotos).
2. **Contenedor** ISO-BMFF: `moov/mvhd.creation_time` y `moov/udta/©day` (videos).
   **Metadatos del documento** para el resto: `dcterms:created` en `docProps/core.xml` de los
   OOXML (`.docx`, `.xlsx`, `.pptx`), `meta:creation-date` en OpenDocument, y `/CreationDate`
   en PDF. Todo con `zipfile` y expresiones regulares de la biblioteca estándar, sin
   dependencias nuevas. En la prueba real sobre 283 documentos, **179 (63%) obtuvieron su
   fecha de aquí** en lugar del `mtime`, que solo indicaba cuándo se copiaron al respaldo.
3. **Patrón en el nombre**: `IMG-20240512-WA0003`, `20230726_124814`,
   `Screenshot_20240608_193933`, `VID_20241209_120302`, `FB_IMG_1704399976041`,
   `2024-05-12 at 14.30.22`, etc.
4. **Fecha del sistema de archivos**: la más antigua entre modificación y creación. En un
   archivo copiado a un disco de respaldo la fecha de creación es la de la copia, mientras
   que la de modificación suele conservar la original, así que la más antigua se parece más
   a la fecha de captura.

Toda fecha se valida: se rechazan años anteriores a 1990, fechas futuras y relojes en
cero. Los archivos sin fecha confiable van a `Sin-Fecha` en lugar de recibir una inventada.

Un detalle que importa: los archivos tipo `1000035167.jpg` **no** se interpretan como
época Unix. Son identificadores de galería de 10 dígitos y leerlos como época daría 2001.
Solo se acepta época en milisegundos (13 dígitos) y precedida de un prefijo conocido.

---

## Cómo clasifica el origen

Reglas deterministas por patrón de nombre, calibradas contra el acervo real que motivó
esta herramienta (57.701 archivos).

| Carpeta | Se detecta por | Ejemplo |
|---|---|---|
| `WhatsApp` | sufijo `WA` de mensajería | `IMG-20190606-WA0044.jpg` |
| `Capturas` | `Screenshot`, `Screen_Recording`, `Captura` | `Screenshot_20240608_193933_WhatsApp.jpg` |
| `Redes-Sociales` | `FB_IMG`, `received_`, Instagram, TikTok, Telegram… | `FB_IMG_1704399976041.jpg` |
| `Editadas` | sufijo `-NN` del editor, PicsArt, Remini, PENUP, collage… | `20231008_171110-01.jpeg` |
| `Camara` | `IMG_`/`VID_`/`DSC`/`PXL` + fecha, o `yyyymmdd_hhmmss` | `20230726_124814.jpg` |
| `Galeria` | identificadores numéricos de 9 a 12 dígitos | `1000035167.jpg` |
| `Descargas` | UUID, hashes, `download`, `unnamed` | `7b0393d7-43ed-….jpg` |
| `Otros` | nada de lo anterior | `vacaciones familia.jpg` |

El orden de las reglas es deliberado. Dos casos que se resuelven a propósito:

- `Screenshot_20240608_193933_WhatsApp.jpg` es una **captura** de la app, no un archivo
  recibido por WhatsApp.
- `20231008_171110-01.jpeg` es una foto de cámara **editada**: el sufijo `-01` lo agrega
  el editor, así que gana `Editadas` sobre `Camara`.

El tipo (foto / video / animación) **no** se decide por la extensión: se verifica con los
bytes mágicos del archivo. Un `.jpg` que en realidad es un MP4 —caso común en respaldos de
mensajería— termina en `Videos`. Un `.webp` o `.png` con más de un cuadro va a
`Animaciones`.

---

## Cómo detecta duplicados

Cuatro etapas, cada una diseñada para no hacer trabajo innecesario:

1. **Filtro por tamaño.** Dos archivos byte-idénticos pesan lo mismo. Solo se leen los
   archivos cuyo tamaño coincide con el de otro. En 280 GB esto evita leer casi todo el
   disco: en una muestra real, 1.562 de 12.000 archivos fueron candidatos.
2. **Huella de bytes** (BLAKE2b) sobre esos candidatos.
3. **Filtro por firma de imagen.** Dos imágenes idénticas píxel a píxel comparten ancho,
   alto, modo de color y número de cuadros. Solo se decodifican las que colisionan en esa
   firma.
4. **Huella de píxeles** (BLAKE2b sobre los píxeles decodificados de cada cuadro). Detecta
   copias con EXIF distinto, con metadatos agregados por una app o recomprimidas sin
   pérdida — casos que la comparación de bytes no ve.

Las etapas 2 y 4 unen componentes con *union-find*, así que los grupos se forman por
transitividad: si A=B por bytes y B=C por píxeles, el grupo final es {A, B, C}. Además,
solo un representante por componente entra a la etapa de píxeles, de modo que ninguna
imagen se decodifica dos veces.

**Qué copia sobrevive.** De cada grupo se conserva una y las demás van a `BORRAR`. El
criterio, en orden: fecha confiable antes que fecha del sistema → mayor tamaño (suele
conservar el EXIF y no estar recomprimida) → `mtime` más antiguo → nombre más corto →
orden alfabético. Es completamente determinista: dos corridas sobre el mismo acervo dan el
mismo resultado.

---

## Garantías

- **Nada se borra, nunca.** Los duplicados se mueven a `BORRAR`; la decisión de eliminarlos
  es de la persona. No hay una sola llamada de borrado de archivos en el código.
- **Nada se sobreescribe.** Se verifica que el destino esté libre antes de cada movimiento,
  y en Windows `os.rename` falla si el destino existe, lo que da la garantía a nivel de
  sistema operativo. Si dos archivos distintos coinciden en nombre, el segundo recibe un
  sufijo `~1`.
- **Todo es reversible.** Cada operación se anota en un manifiesto JSONL con vaciado
  inmediato: incluso una interrupción abrupta deja un registro completo de lo ya hecho.
- **Simulación fiel.** No es una estimación: el objeto `Plan` que se muestra es
  literalmente el que se aplica después.
- **Idempotente.** Correr el script dos veces no reorganiza lo ya organizado, porque las
  carpetas de salida se excluyen del escaneo.
- **Contención de rutas.** Todo nombre de carpeta derivado de metadatos se sanea (se
  eliminan separadores, caracteres prohibidos y nombres reservados de Windows) y se
  verifica que el destino calculado quede dentro del árbol permitido. La comprobación
  rechaza cualquier ruta con `..`, en lugar de compararla: `raíz/../../otro` tiene a `raíz`
  entre sus ancestros léxicos y pasaría un control ingenuo.
- **El CSV y el manifiesto se tratan como entrada no confiable.** Son archivos que viven en
  el disco de medios y que la persona abre y edita. Al leerlos se valida cada fila: el
  origen debe estar dentro de la raíz y el destino dentro del árbol de salida, con las
  rutas ya normalizadas. Se rechazan además las formas híbridas de Windows (`C:notas.txt`,
  `\Windows\x`), que `pathlib` re-ancla de maneras sorprendentes. El manifiesto exige
  encabezado y valida cada operación contra la raíz y el destino que ese encabezado declara.
- **Sin fórmulas ejecutables en el CSV.** Un archivo llamado `=cmd|'/c calc'!A1.jpg` se
  ejecutaría al abrir el reporte en Excel. Las celdas que empiezan con `=`, `+`, `-`, `@` o
  tabulación se escriben con un apóstrofo delante, y `--aplicar-plan` lo quita al leer, así
  que el viaje de ida y vuelta es exacto.
- **Un archivo problemático no se lleva la corrida.** El análisis nunca propaga
  excepciones: degrada a la información disponible. Y si un proceso hijo muere sin
  excepción (una DLL de imagen que aborta), el respaldo serial **salta el lote en vuelo**
  en lugar de reejecutar en el proceso padre el archivo que lo mató.
- **Nada queda a medias.** Si una copia falla a mitad (disco lleno, USB desconectado), el
  destino truncado se elimina en lugar de quedar dentro del árbol organizado sin registro
  en el manifiesto.
- **Rutas largas de Windows.** Se usa el prefijo `\\?\` para sortear el límite de 260
  caracteres.
- **No se siguen enlaces** simbólicos ni junctions por defecto: no se sale del árbol ni se
  entra en ciclos.

---

## Revertir una corrida

```bash
python organizar.py "D:\Fotos" --deshacer "D:\Fotos\_ORGANIZADOR\manifiesto-20260813-120000.jsonl"
```

Muestra qué revertiría. Con `--ejecutar` lo aplica: devuelve cada archivo a su ubicación
original en orden inverso y elimina las carpetas que queden vacías. Solo revierte lo que
sigue consistente; un archivo que ya se movió a mano se omite en lugar de fallar.

---

## Rendimiento medido

Medido sobre el acervo real: 57.701 archivos, 280 GB, HDD de 1 TB por USB.

| Operación | Costo por archivo |
|---|---|
| `stat` (entrada de directorio) | 0,03 ms |
| primer acceso al contenido (frío) | 98,6 ms |
| mismo acceso ya en caché | 0,10 ms |
| decodificación JPEG completa | 22,1 ms |
| decodificación con `draft` 1/8 | 10,1 ms |

La conclusión de diseño: **el costo es la latencia del primer acceso a cada archivo**, no
la CPU. Por eso el escaneo no abre archivos —solo lee entradas de directorio— y todo el
trabajo de disco ocurre en la fase paralela, donde de una única apertura por archivo salen
a la vez el tipo real, las dimensiones y la fecha EXIF.

Efecto de ese cambio, medido con 5.000 archivos ya en caché: **550 s → 11,9 s**.

Sobre archivos fríos, con 8 núcleos:

| Procesos | Costo por archivo frío | Proyección para 57.701 archivos |
|---|---|---|
| 7 (núcleos − 1) | 62,8 ms | ~60 min |
| 16 (predeterminado) | **31,2 ms** | **~30 min** |

Por eso el valor predeterminado sobresuscribe los núcleos (`núcleos × 2`, con tope de 16):
el disco atiende varias peticiones encoladas a la vez, así que más lectores concurrentes
reducen el tiempo total aunque no haya más CPU. El tope acota la memoria, porque cada
trabajador puede tener una imagen decodificada en RAM. En un SSD la diferencia es mucho
menor; ahí conviene bajar `--procesos` al número de núcleos.

Para acervos grandes de JPEG, `--prefiltro` reduce el costo de la etapa de píxeles a menos
de la mitad, a cambio de un riesgo teórico —despreciable en la práctica— de no detectar una
copia producida por un codificador distinto.

---

## Arquitectura

Cada módulo tiene una responsabilidad y una sola. La política (dónde va cada archivo) está
separada de los efectos (mover archivos), lo que hace que el planificador sea código puro y
verificable.

| Módulo | Responsabilidad |
|---|---|
| `config.py` | Configuración inmutable y validada de la corrida. |
| `errores.py` | Jerarquía de excepciones propias. |
| `registro.py` | Logging y barra de progreso. |
| `rutas_seguras.py` | Saneado de nombres, rutas largas, colisiones, contención. |
| `tipos.py` | Tipo de medio por extensión y por bytes mágicos. |
| `fechas.py` | Cascada EXIF → contenedor → nombre → sistema. |
| `fuentes.py` | Clasificación del origen por patrón de nombre. |
| `escaneo.py` | Recorrido del árbol sin abrir archivos. |
| `huellas.py` | Huellas de bytes y de píxeles (funciones de trabajador). |
| `paralelo.py` | Pool de procesos con degradación a modo serial. |
| `metadatos.py` | Una apertura por archivo → tipo, fecha y firma. |
| `duplicados.py` | Detección en dos niveles con union-find. |
| `planificador.py` | Política de destinos y de superviviente (puro). |
| `ejecutor.py` | Movimientos, manifiesto y reversión. |
| `reportes.py` | CSV, JSON y resumen de consola. |
| `aplicacion.py` | Orquestación del pipeline. |
| `cli.py` | Interfaz de línea de comandos. |

---

## Pruebas

```bash
python -m unittest discover -s tests -t . -v
```

196 pruebas, sin dependencias de prueba adicionales. Cubren los patrones de nombre reales,
el parser ISO-BMFF con `moov` al final del archivo, el saneado de rutas contra intentos de
recorrido de directorios, la transitividad de la detección de duplicados, la garantía de no
sobreescritura, los límites de trabajadores del sistema operativo y una corrida completa de
extremo a extremo con su reversión. Los ejemplos de las docstrings se ejecutan como pruebas,
así que la documentación no puede quedar desactualizada en silencio.

[tests/test_seguridad.py](tests/test_seguridad.py) es una regresión por cada defecto
encontrado en la revisión del código: CSV con origen fuera de la raíz, destino con `..`,
rutas híbridas de Windows, fórmulas de hoja de cálculo, manifiesto manipulado o sin
encabezado, y un archivo cuyo fallo no debe abortar la corrida.

---

## Uso como biblioteca

```python
from pathlib import Path
from mediaorg import Configuracion, ModoDuplicados, organizar, texto_resumen

cfg = Configuracion(
    raiz=Path(r"D:\Fotos"),
    destino=Path(r"E:\Ordenado"),
    simulacion=True,
    duplicados=ModoDuplicados.PIXEL,
)
informe = organizar(cfg)
print(texto_resumen(informe))
print(informe.a_dict()["distribucion"]["por_fuente"])
```
