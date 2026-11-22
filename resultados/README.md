# Resultados de las pruebas

Dejé el resumen de aciertos, errores y capturas inconclusas en el [README principal](../README.md#resultados-de-las-60-capturas). En esta carpeta reuní los datos para verificar los resultados por caso.

## Qué archivo consultar

| Archivo | Para qué lo dejé |
| --- | --- |
| [`comparacion_api_60.csv`](comparacion_api_60.csv) | **CSV principal.** Comparé ambos motores en las mismas 60 fotos. Dejé dos filas por caso, una para `tesseract` con confirmación opcional de EasyOCR y otra para `fastplate`. |
| [`fastplate_60.csv`](fastplate_60.csv) | Guardé una ejecución aparte de FastPlateOCR sobre las mismas fotos, con una fila por caso. Lo uso como referencia al ejecutar `Repetir_60_FastPlate.py`. |
| [Planilla de las capturas originales](bloque_controlado/Protocolo_pruebas_placas_luz_fija_capturas.xlsx) | Registré las condiciones de la prueba con la cámara, la decisión original, el tiempo de procesamiento y el de ida y vuelta. Uso la hoja **Casos** para buscar una captura por su ID. |

## Cómo hice la comparación

Volví a enviar los JPEG originales a la API en el portátil y usé la misma base SQLite para ambos motores. Calenté cada motor antes de registrar los tiempos. En ambas repeticiones simulé la publicación MQTT. En el CSV principal marqué como exacta la lectura que coincidió en los seis caracteres, como incorrecta la matrícula diferente que la API aceptó y como inconcluso el caso en el que la API se abstuvo. Medí el tiempo dentro de la API, desde la recepción del JPEG completo hasta la decisión. Las capturas originales incluyen además la medición de ida y vuelta de la herramienta guiada.

Para revisar una mejora concreta uso **C017**, a 0,80 m y 25°. Con Tesseract obtuve `NO CONCLUYENTE` y con FastPlateOCR leí `GXA795`, que estaba registrada en la base de prueba. Para revisar un caso que sigue inconcluso uso **C011**, donde encontré varias cajas candidatas con ambos motores.

## Fotos, registros y datos para repetir la prueba

- En [`bloque_controlado/evidencias_controlado/`](bloque_controlado/evidencias_controlado/) guardé las fotos originales, las detecciones, el recorte cuando estuvo disponible y el diagnóstico. Vinculé cada caso `C001` a `C060` con su carpeta de fotos mediante los [registros JSON](bloque_controlado/Protocolo_pruebas_placas_luz_fija_capturas.registros/).
- En [`referencias_60.csv`](bloque_controlado/referencias_60.csv) dejé la matrícula esperada, el tipo, la distancia y el ángulo de cada caso. Es la entrada que lee mi script de repetición.
- En [`placas_controlado.db`](bloque_controlado/placas_controlado.db) incluí seis matrículas sintéticas registradas. Los [patrones del ensayo](bloque_controlado/Patrones_bloque_controlado.pdf) contienen las matrículas que mostré a la cámara.
- Guardé las salidas de nuevas ejecuciones bajo `resultados/locales/`, una carpeta que Git ignora.
