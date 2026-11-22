# Flujo de la API de placas

En [`api_placas.py`](api_placas.py) concentro el procesamiento de cada foto. Configuro el motor OCR al iniciar la API. Para usar la cámara y el panel, dejé los comandos en el [README principal](../README.md).

## Cómo proceso una captura

1. **Recibo el JPEG.** Desde CameraWebServer envío la foto y su `captura_id` a `POST /capturas`. Valido el tipo de contenido, el tamaño del archivo y las dimensiones de la imagen.
2. **Localizo la placa.** Ejecuto YOLO en CPU con un umbral de confianza de `0.25`. Pruebo un tamaño de entrada de `640` y, si faltan detecciones, vuelvo a intentar con `320`. Uso OpenCV para dibujar las cajas, comprobar si corresponden a un mismo contorno y recortar la placa cuando puedo identificar una sola.
3. **Leo los caracteres.** Con FastPlateOCR uso el recorte YOLO en color, convertido de BGR a RGB. Tomo la menor confianza de los caracteres leídos y exijo al menos `0.70`. Con Tesseract uso recortes preprocesados y, cuando activo la confirmación lateral, corrijo la perspectiva y verifico la coincidencia de la lectura con EasyOCR.
4. **Decido si está registrada.** Valido formatos como `ABC123` y `ABC12D`. Si acepto la lectura, busco la matrícula por igualdad exacta en SQLite y devuelvo `REGISTRADA` o `NO REGISTRADA`. Si la detección o la lectura queda ambigua, devuelvo `NO CONCLUYENTE` con el motivo.

## Qué guardo y qué envío al panel

Guardo el JPEG original, las vistas intermedias disponibles y un `diagnostico.json` por captura. Publico la decisión y su referencia de evidencia en `iot/final/placas/decisiones` con QoS 1 y devuelvo el resultado a la cámara por HTTP. Con Node-RED muestro la decisión más reciente, el historial y las imágenes disponibles.

Para mostrar la foto desde el navegador local, sirvo `detecciones.jpg` o el JPEG original en `GET /evidencias/{carpeta}/foto`. Para mostrar el recorte uso `recorte_yolo.png` en `GET /evidencias/{carpeta}/recorte`.

Mido `procesamiento_ms` desde que recibo el JPEG completo hasta que tomo la decisión, antes de guardar la evidencia y publicar por MQTT. Con la captura guiada también mido el tiempo de ida y vuelta desde que solicito la foto hasta que recibo la respuesta. Dejé el detalle de las pruebas en [`resultados/`](../resultados/).
