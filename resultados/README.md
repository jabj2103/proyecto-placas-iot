# Resultados de las pruebas

El resumen de aciertos, errores y capturas inconclusas está en el [README principal](../README.md#resultados-de-las-60-capturas). En esta carpeta se reunieron los datos para verificar los resultados por caso.

## Qué archivo consultar

| Archivo | Qué contiene |
| --- | --- |
| [`comparacion_api_60.csv`](comparacion_api_60.csv) | **CSV principal.** Se compararon ambos motores en las mismas 60 fotos. Hay dos filas por caso, una para `tesseract` con confirmación opcional de EasyOCR y otra para `fastplate`. |
| [`fastplate_60.csv`](fastplate_60.csv) | Resultados de una ejecución aparte de FastPlateOCR sobre las mismas fotos, con una fila por caso. Sirve de referencia al ejecutar `Repetir_60_FastPlate.py`. |
| [Planilla de las capturas originales](bloque_controlado/Protocolo_pruebas_placas_luz_fija_capturas.xlsx) | Se registraron las condiciones de la prueba con la cámara, la decisión original, el tiempo de procesamiento y el de ida y vuelta. En la hoja **Casos** se puede buscar cada captura por su ID. |

## Cómo se hizo la comparación

Se reenviaron los JPEG originales a la API en el portátil y se usó la misma base SQLite para ambos motores. Cada motor se calentó antes de registrar los tiempos. En ambas repeticiones se simuló la publicación MQTT. En el CSV principal, una lectura se marcó como exacta si coincidieron los seis caracteres, como incorrecta si la API aceptó una matrícula diferente y como inconclusa si la API se abstuvo. Se midió el tiempo dentro de la API, desde la recepción del JPEG completo hasta la decisión. Las capturas originales incluyen además el tiempo de ida y vuelta de la herramienta guiada.

En el caso **C017**, a 0,80 m y 25°, con Tesseract se obtuvo `NO CONCLUYENTE` y con FastPlateOCR se leyó `GXA795`, que estaba registrada en la base de prueba. El caso **C011** quedó inconcluso con ambos motores porque se detectaron varias cajas candidatas.

## Fotos, registros y datos para repetir la prueba

- En [`bloque_controlado/evidencias_controlado/`](bloque_controlado/evidencias_controlado/) se guardaron las fotos originales, las detecciones, el recorte cuando estuvo disponible y el diagnóstico. Los [registros JSON](bloque_controlado/Protocolo_pruebas_placas_luz_fija_capturas.registros/) vinculan cada caso de `C001` a `C060` con su carpeta de fotos.
- En [`referencias_60.csv`](bloque_controlado/referencias_60.csv) se registraron la matrícula esperada, el tipo, la distancia y el ángulo de cada caso. El script de repetición usa este archivo como entrada.
- En [`placas_controlado.db`](bloque_controlado/placas_controlado.db) se incluyeron seis matrículas sintéticas registradas. Los [patrones del ensayo](bloque_controlado/Patrones_bloque_controlado.pdf) contienen las matrículas que se mostraron a la cámara.
- Las salidas de nuevas ejecuciones se guardan en `resultados/locales/`, una carpeta que Git ignora.
