# Reconocimiento de placas · Proyecto IoT Edge

Prototipo académico de reconocimiento de matrículas de automóviles y motocicletas. La ESP32-CAM AI Thinker toma y envía un JPEG al pulsar **Capturar y enviar**. El portátil localiza la placa con YOLO, prepara el recorte con OpenCV, lee los caracteres, consulta SQLite y presenta el resultado y las imágenes en Node-RED. La detección automática de llegadas queda pendiente de discusión del equipo.

## Qué lleva esta copia

| Archivo o carpeta | Contenido |
| --- | --- |
| [`outputs/api_placas.py`](outputs/api_placas.py) | API comentada con selección de Tesseract o FastPlateOCR al arrancar. |
| [`outputs/CameraWebServerPlacas/`](outputs/CameraWebServerPlacas/) | Sketch para la ESP32-CAM AI Thinker, con controles de CameraWebServer y botón para enviar a la API. |
| [`compose.yaml`](compose.yaml) y [`outputs/flows_placas.json`](outputs/flows_placas.json) | Mosquitto, Node-RED con volumen persistente y flujo importable del panel. |
| [`outputs/Kit_bloque_controlado/`](outputs/Kit_bloque_controlado/) | Patrones de prueba, SQLite con seis matrículas sintéticas registradas y evidencia de las 60 capturas. |
| [`outputs/`](outputs/) | Protocolo en Excel, planilla diligenciada, registros por captura y scripts de toma guiada y repetición. |
| [`resultados/`](resultados/) | CSV de la repetición FastPlateOCR y de la comparación de los dos modos sobre los mismos JPEG. |
| [`outputs/Versiones_API_placas.md`](outputs/Versiones_API_placas.md) | Flujo detallado, cambios de la API, condiciones del ensayo y resultados. |

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
& $python .\outputs\Repetir_60_FastPlate.py --salida .\datos_locales\resultados
```

**Linux**

```bash
python outputs/Repetir_60_FastPlate.py --salida datos_locales/resultados
```

El script abre la base de prueba en modo lectura, vuelve a enviar cada JPEG original a `POST /capturas` con el cliente local de FastAPI y compara los seis caracteres con la referencia. El envío a MQTT se simula y las evidencias nuevas son temporales. Solo queda el CSV de la repetición en `datos_locales/resultados`.

## Arrancar el sistema con la cámara

1. Inicia los servicios con `docker compose up -d`. Si ya estás ejecutando Mosquitto o Node-RED en los puertos `18884` o `1880`, usa los servicios existentes con la misma configuración o libera esos puertos antes de iniciar Compose.
2. En [el editor de Node-RED](http://127.0.0.1:1880), abre **Manage palette → Install** e instala `@flowfuse/node-red-dashboard`. Importa `outputs/flows_placas.json` desde **Import** y pulsa **Deploy**. Abre [el panel de placas](http://127.0.0.1:1880/dashboard/placas) en el mismo portátil que ejecuta la API.
3. Abre una terminal en la raíz del repositorio y arranca la API con los comandos de tu sistema. La configuración de ejemplo usa FastPlateOCR, la base sintética y una carpeta nueva para las evidencias.

   **Windows · PowerShell**

   ```powershell
   $python = '.\.venv\Scripts\python.exe'
   $env:PLACAS_DB = Join-Path $PWD 'outputs/Kit_bloque_controlado/placas_controlado.db'
   $env:EVIDENCIAS_DIR = Join-Path $PWD 'datos_locales/evidencias'
   $env:PLACAS_FASTPLATE_DIR = Join-Path $PWD 'work/ocr_comparacion/modelos/fastplate'
   $env:PLACAS_OCR_MOTOR = 'fastplate'
   & $python -m uvicorn api_placas:app --app-dir outputs --host 0.0.0.0 --port 8000
   ```

   **Linux · Bash**

   ```bash
   . .venv/bin/activate
   export PLACAS_DB="$PWD/outputs/Kit_bloque_controlado/placas_controlado.db"
   export EVIDENCIAS_DIR="$PWD/datos_locales/evidencias"
   export PLACAS_FASTPLATE_DIR="$PWD/work/ocr_comparacion/modelos/fastplate"
   export PLACAS_OCR_MOTOR=fastplate
   python -m uvicorn api_placas:app --app-dir outputs --host 0.0.0.0 --port 8000
   ```

4. En `outputs/CameraWebServerPlacas`, copia `config_ejemplo.h` como `config_local.h`. Completa el nombre de tu Wi‑Fi, su contraseña y la URL de la API en esa copia. Usa la IP del portátil que alcanza la cámara, por ejemplo `http://192.168.1.20:8000/capturas`. Git ignora `config_local.h`. Abre `CameraWebServerPlacas.ino` en Arduino IDE y selecciona **AI Thinker ESP32-CAM** del paquete de placas ESP32 de Espressif. La versión de referencia del paquete es `3.3.10`.
5. Carga el sketch, abre el monitor serie a `115200` y entra a la IP que imprime la cámara. Cámara y portátil deben estar en la misma red de pruebas. Selecciona **SXGA (1280 × 1024)** para replicar el bloque y detén el stream antes de pulsar **Capturar y enviar**. La respuesta JSON debe indicar `ocr: "fastplate"`. El evento llega a `iot/final/placas/decisiones` y el panel muestra la imagen anotada y el recorte cuando estén disponibles. Las evidencias nuevas quedan en `datos_locales/evidencias`.

Para usar Tesseract, instala el ejecutable con el idioma `eng`, establece `PLACAS_OCR_MOTOR=tesseract` y reinicia Uvicorn. Si el ejecutable queda fuera del `PATH`, configura `TESSERACT_EXE` con su ruta. La confirmación lateral requiere además `easyocr==1.7.2`, sus pesos de inglés en caché y `PLACAS_OCR_LATERAL=1`. La consola de la API indica el modo activo en las respuestas.

Para iniciar la captura guiada, usa `& $python .\outputs\Captura_guiada_placas.py` en PowerShell o `python outputs/Captura_guiada_placas.py` desde el entorno activado en Linux. Abre una copia del protocolo en `datos_locales` e indica la IP de CameraWebServer. La herramienta vincula la fila al `captura_id` y registra el tiempo de ida y vuelta. Si elegiste una ruta de evidencias distinta de `datos_locales/evidencias`, configura el mismo `EVIDENCIAS_DIR` en la terminal que abre la herramienta.

## Resultados y alcance

| Ensayo | Exactas | Incorrectas aceptadas | Inconclusas |
| --- | ---: | ---: | ---: |
| Capturas originales, Tesseract | 18/60 | 4 | 38 |
| Repetición de los mismos JPEG, Tesseract con confirmación lateral | 24/60 | 4 | 32 |
| Repetición de los mismos JPEG, FastPlateOCR | 53/60 | 0 | 7 |

El bloque se hizo en casa, con placas sintéticas en pantalla, luz fija, distancias de 0,80 m, 1,15 m y 1,50 m, y ángulos de 0° y 25°. Los siete casos inconclusos con FastPlateOCR quedaron en la etapa de detección por múltiples candidatos. En la repetición de referencia, la mediana de `procesamiento_ms` fue 109,2 ms. Ese campo empieza tras recibir el JPEG y termina antes de persistir la evidencia y publicar por MQTT. El tiempo de ida y vuelta de la captura guiada se registró aparte en el ensayo original. La validación en el parqueadero con autos y motos queda pendiente.
