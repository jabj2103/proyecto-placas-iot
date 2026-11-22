# Flujo de la API de placas

En [`api_placas.py`](api_placas.py) se procesa cada foto con el motor OCR elegido al iniciar la API. Los comandos para usar la cámara y el panel están en el [README principal](../README.md).

## Cómo se procesa una captura

1. **Recepción del JPEG.** CameraWebServer envía la foto y su `captura_id` a `POST /capturas`. Se validan el tipo de contenido, el tamaño del archivo y las dimensiones de la imagen.
2. **Detección de la placa.** Se ejecuta YOLO en CPU con un umbral de confianza de `0.25`. Se prueba un tamaño de entrada de `640` y, si no hay detecciones, se vuelve a intentar con `320`. Con OpenCV se dibujan las cajas, se comprueba si corresponden a un mismo contorno y se recorta la placa cuando se identifica una sola.
3. **Lectura de los caracteres.** Con FastPlateOCR se usa el recorte YOLO en color, convertido de BGR a RGB. Se exige que la menor confianza de los caracteres leídos alcance `0.70`. Con Tesseract se usan recortes preprocesados. Si se activa la confirmación lateral, se corrige la perspectiva y se comprueba que la lectura coincida con la de EasyOCR.
4. **Consulta del registro.** Se validan formatos como `ABC123` y `ABC12D`. Si se acepta la lectura, se busca la matrícula por igualdad exacta en SQLite. La API devuelve `REGISTRADA` si la encuentra y `NO REGISTRADA` si no figura en la base. Si la detección o la lectura queda ambigua, devuelve `NO CONCLUYENTE` con el motivo.

## Qué se guarda y qué se envía al panel

Por cada captura se guardan el JPEG original, las vistas intermedias disponibles y un `diagnostico.json`. La decisión y su referencia de evidencia se publican en `iot/final/placas/decisiones` con QoS 1. La cámara recibe el resultado por HTTP y Node-RED muestra la decisión más reciente, el historial y las imágenes disponibles.

Desde el navegador local, la ruta `GET /evidencias/{carpeta}/foto` devuelve `detecciones.jpg` o el JPEG original. La ruta `GET /evidencias/{carpeta}/recorte` devuelve `recorte_yolo.png`.

Se mide `procesamiento_ms` desde que se recibe el JPEG completo hasta que se toma la decisión, antes de guardar la evidencia y publicar por MQTT. Con la captura guiada también se mide el tiempo de ida y vuelta, desde la solicitud de la foto hasta la recepción de la respuesta. El detalle de las pruebas está en [`resultados/`](../resultados/).
