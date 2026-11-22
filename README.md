# Reconocimiento de placas · Proyecto IoT Edge

Preparé este prototipo académico para reconocer matrículas de automóviles y motocicletas. Uso una ESP32-CAM AI Thinker para enviar el JPEG al pulsar **Capturar y enviar**. En el portátil localizo la placa con YOLO, preparo el recorte con OpenCV, leo sus caracteres, consulto SQLite y muestro la decisión y las imágenes en Node-RED.

## Resultados de las 60 capturas

Tomé 60 fotos en casa con la ESP32-CAM en SXGA (1280 × 1024). Usé matrículas sintéticas en pantalla, luz fija, distancias de 0,80 m, 1,15 m y 1,50 m y ángulos de 0° y 25°. Para comparar los motores, reenvié los mismos JPEG a la API.

| Ensayo | Exactas | Incorrectas aceptadas | Inconclusas | Exactas a 25° |
| --- | ---: | ---: | ---: | ---: |
| Capturas originales con Tesseract | 18/60 | 4 | 38 | 0/30 |
| Repetición con Tesseract y confirmación de EasyOCR | 24/60 | 4 | 32 | 6/30 |
| Repetición con FastPlateOCR | **53/60** | **0** | **7** | **24/30** |

Elegí FastPlateOCR para el arranque de ejemplo porque obtuve **53 lecturas exactas de 60 (88,3 %)** con el recorte YOLO en color. En los siete casos inconclusos obtuve varias cajas candidatas y la API se abstuvo porque no pudo asociarlas a una sola placa. En la comparación sobre los mismos JPEG medí una mediana de **139,2 ms** con FastPlateOCR y **1.813,6 ms** con Tesseract y confirmación de EasyOCR. En una repetición aparte de FastPlateOCR obtuve **109,2 ms**. Medí el procesamiento dentro de la API. Tengo pendiente validar el desempeño con vehículos en el parqueadero.

Dejé [`comparacion_api_60.csv`](resultados/comparacion_api_60.csv) como **CSV principal** para comparar los dos motores por caso. En [`resultados/`](resultados/) expliqué cuándo consultar el segundo CSV y dónde están el Excel diligenciado, los registros y las fotos.

## Qué hace cada archivo Python

| Archivo | Para qué lo uso |
| --- | --- |
| [`preparar_modelos.py`](preparar_modelos.py) | Preparo los pesos de YOLO y FastPlateOCR antes del primer arranque. |
| [`api_placas.py`](outputs/api_placas.py) | Recibo la foto, detecto y leo la placa, consulto SQLite, guardo evidencia y publico el resultado. Inicio la API con Uvicorn. |
| [`Repetir_60_FastPlate.py`](outputs/Repetir_60_FastPlate.py) | Vuelvo a procesar las 60 fotos con FastPlateOCR y genero un CSV nuevo. Puedo ejecutarlo sin cámara ni servicios Docker. |
| [`Captura_guiada_placas.py`](outputs/Captura_guiada_placas.py) | Tomo fotos nuevas desde una ventana y guardo la decisión, el tiempo de ida y vuelta y el ID en una copia de la planilla. |
| [`Importar_resultados_placas.py`](outputs/Importar_resultados_placas.py) | Completo campos vacíos en una copia de una planilla que ya tiene IDs de captura. Cruzo cada ID con su diagnóstico y, si tengo un archivo HAR del navegador, recupero el tiempo total. Es opcional. |

Documenté el recorrido de una foto en [Flujo de la API](outputs/Flujo_API_placas.md). Dejé los controles de cámara en [`CameraWebServerPlacas/`](outputs/CameraWebServerPlacas/), el broker y Node-RED en [`compose.yaml`](compose.yaml), el panel importable en [`flows_placas.json`](outputs/flows_placas.json) y una [planilla vacía](outputs/Protocolo_pruebas_placas_luz_fija.xlsx) para nuevas pruebas.

## Instalación en el portátil

Usa Python 3.11, Docker con Compose y Arduino IDE para programar la cámara. Todos los comandos parten de la raíz de este repositorio. El comando de PyTorch instala la edición CPU.

**Windows · PowerShell**

```powershell
py -3.11 -m venv .venv
$python = '.\.venv\Scripts\python.exe'
& $python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
& $python -m pip install -r requirements.txt
& $python preparar_modelos.py
```

**Linux · Bash**

```bash
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install torch torchvision --index-url https://download.pytorch.org/whl/cpu
python -m pip install -r requirements.txt
python preparar_modelos.py
```

En Debian o Ubuntu, la edición de Python que uses debe incluir `venv`. Si `import cv2` indica que falta `libGL.so.1`, instala `libgl1` con tu gestor de paquetes. La herramienta de captura guiada requiere Tkinter, normalmente instalado con `python3-tk`.

`preparar_modelos.py` descarga `best.pt` a la caché de Hugging Face y el modelo `cct-s-v2-global-model` de FastPlateOCR a `work/ocr_comparacion/modelos/fastplate`. La preparación necesita Internet. La inferencia del prototipo se ejecuta en el portátil. Versiones de referencia de la repetición en Windows · Python 3.11.3, PyTorch 2.15.0 CPU, Ultralytics 8.4.203, OpenCV 5.0.0.93, FastPlateOCR 1.1.0 y ONNX Runtime 1.32.0. [Detector](https://huggingface.co/Koushim/yolov8-license-plate-detection) · [FastPlateOCR](https://ankandrew.github.io/fast-plate-ocr/latest/reference/inference/hub/)

## Repetir las 60 capturas sin cámara

**Windows**

```powershell
& $python .\outputs\Repetir_60_FastPlate.py
```

**Linux**

```bash
python outputs/Repetir_60_FastPlate.py
```

Para repetir la prueba, reenvío los JPEG originales a `POST /capturas` con el cliente local de FastAPI, consulto la base de prueba en modo lectura y comparo los seis caracteres con la referencia. Simulo el envío a MQTT y genero las evidencias en una carpeta temporal. Dejo el CSV nuevo en `resultados/locales/`, que Git ignora.

## Arrancar el sistema con la cámara

1. Inicia los servicios con `docker compose up -d`. Si ya estás ejecutando Mosquitto o Node-RED en los puertos `18884` o `1880`, usa los servicios existentes con la misma configuración o libera esos puertos antes de iniciar Compose.
2. En [el editor de Node-RED](http://127.0.0.1:1880), abre **Manage palette → Install** e instala `@flowfuse/node-red-dashboard`. Importa `outputs/flows_placas.json` desde **Import** y pulsa **Deploy**. Abre [el panel de placas](http://127.0.0.1:1880/dashboard/placas) en el mismo portátil que ejecuta la API.
3. Abre una terminal en la raíz del repositorio y arranca la API con los comandos de tu sistema. La configuración de ejemplo usa FastPlateOCR, la base sintética y una carpeta nueva para las evidencias.

   **Windows · PowerShell**

   ```powershell
   $python = '.\.venv\Scripts\python.exe'
   $env:PLACAS_DB = Join-Path $PWD 'resultados/bloque_controlado/placas_controlado.db'
   $env:EVIDENCIAS_DIR = Join-Path $PWD 'resultados/locales/evidencias'
   $env:PLACAS_FASTPLATE_DIR = Join-Path $PWD 'work/ocr_comparacion/modelos/fastplate'
   $env:PLACAS_OCR_MOTOR = 'fastplate'
   & $python -m uvicorn api_placas:app --app-dir outputs --host 0.0.0.0 --port 8000
   ```

   **Linux · Bash**

   ```bash
   . .venv/bin/activate
   export PLACAS_DB="$PWD/resultados/bloque_controlado/placas_controlado.db"
   export EVIDENCIAS_DIR="$PWD/resultados/locales/evidencias"
   export PLACAS_FASTPLATE_DIR="$PWD/work/ocr_comparacion/modelos/fastplate"
   export PLACAS_OCR_MOTOR=fastplate
   python -m uvicorn api_placas:app --app-dir outputs --host 0.0.0.0 --port 8000
   ```

4. En `outputs/CameraWebServerPlacas`, copia `config_ejemplo.h` como `config_local.h`. Completa el nombre de tu Wi‑Fi, su contraseña y la URL de la API en esa copia. Usa la IP del portátil que alcanza la cámara, por ejemplo `http://192.168.1.20:8000/capturas`. Git ignora `config_local.h`. Abre `CameraWebServerPlacas.ino` en Arduino IDE y selecciona **AI Thinker ESP32-CAM** del paquete de placas ESP32 de Espressif. La versión de referencia del paquete es `3.3.10`.
5. Carga el sketch, abre el monitor serie a `115200` y entra a la IP que imprime la cámara. Cámara y portátil deben estar en la misma red de pruebas. Selecciona **SXGA (1280 × 1024)** para replicar el bloque y detén el stream antes de pulsar **Capturar y enviar**. La respuesta JSON debe indicar `ocr: "fastplate"`. El evento llega a `iot/final/placas/decisiones` y el panel muestra la imagen anotada y el recorte cuando estén disponibles. Las evidencias nuevas quedan en `resultados/locales/evidencias`.

Para usar Tesseract, instala el ejecutable con el idioma `eng`, establece `PLACAS_OCR_MOTOR=tesseract` y reinicia Uvicorn. Si el ejecutable queda fuera del `PATH`, configura `TESSERACT_EXE` con su ruta. La confirmación lateral requiere además `easyocr==1.7.2`, sus pesos de inglés en caché y `PLACAS_OCR_LATERAL=1`. La consola de la API indica el modo activo en las respuestas.

Para iniciar la captura guiada, usa `& $python .\outputs\Captura_guiada_placas.py` en PowerShell o `python outputs/Captura_guiada_placas.py` desde el entorno activado en Linux. Crea `resultados/locales/`, copia allí la planilla vacía, selecciónala en la ventana e indica la IP de CameraWebServer. Si elegiste una ruta de evidencias distinta de `resultados/locales/evidencias`, configura el mismo `EVIDENCIAS_DIR` en la terminal que abre la herramienta.
