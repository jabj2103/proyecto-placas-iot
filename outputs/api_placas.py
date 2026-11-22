# Esta API se ejecuta en el computador. Recibe JPEG de la ESP32-CAM,
# localiza y lee la placa, consulta SQLite y publica la decisión para Node-RED.
import csv
import json
import logging
import os
import re
import shutil
import sqlite3
import subprocess
import tempfile
from contextlib import asynccontextmanager, closing
from io import BytesIO
from itertools import combinations
from pathlib import Path
from threading import Lock
from time import perf_counter
from typing import Annotated, Literal

import cv2
import numpy as np
from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse
from huggingface_hub import hf_hub_download
from paho.mqtt import MQTTException, publish
from PIL import Image, UnidentifiedImageError
from starlette.concurrency import run_in_threadpool
from ultralytics import YOLO


# Mosquitto se alcanza desde la API por el puerto publicado en el computador.
MQTT_HOST = "127.0.0.1"
MQTT_PORT = 18884
TOPIC_DECISIONES = "iot/final/placas/decisiones"
MAX_JPEG_BYTES = 5 * 1024 * 1024
MAX_JPEG_PIXELES = 12_000_000
# Cada etapa tiene su propio umbral. La confianza de YOLO corresponde a la caja
# y la de Tesseract a la lectura del texto.
UMBRAL_DETECTOR = 0.25
UMBRAL_OCR = 0.70
UMBRAL_FASTPLATE = 0.70
CONTENCION_MINIMA_CAJAS = 0.90
COBERTURA_MINIMA_CONTORNO = 0.90
# El último carácter puede ser un dígito (ABC123) o una letra (ABC12D).
FORMATO_PLACA = re.compile(r"[A-Z]{3}[0-9]{2}[A-Z0-9]")
FORMATO_CON_SEPARADOR = re.compile(r"([A-Z]{3})[\s.\u00b7:\u2022-]*([0-9]{2}[A-Z0-9])")
UMBRAL_EASYOCR_LATERAL = 0.50
BRILLO_MINIMO_LATERAL_SIN_REALCE = 65
# Estas variables permiten usar una base de prueba y una carpeta por ensayo.
PLACAS_DB = Path(os.getenv(
    "PLACAS_DB", str(Path.home() / "Desktop" / "ProyectoPlacas" / "placas.db")
)).expanduser().resolve()
EVIDENCIAS_DIR = Path(os.getenv("EVIDENCIAS_DIR", str(PLACAS_DB.parent / "evidencias_api"))).expanduser().resolve()

logger = logging.getLogger(__name__)
# Las peticiones comparten los modelos cargados al iniciar el servidor.
bloqueo_detector = Lock()
bloqueo_easyocr = Lock()


class DiagnosticoCaptura:
    # Reúne la evidencia de una petición para poder inspeccionar cada etapa después.
    def __init__(self):
        self.original: bytearray | None = None
        self.imagenes: dict[str, np.ndarray] = {}
        self.entradas_ocr: dict[str, bytes] = {}
        self.detecciones: list[dict] = []
        self.lecturas_ocr: dict[str, list[tuple[str, float]]] = {}
        self.resolucion: dict | None = None
        self.recorte_xyxy: list[int] | None = None
        self.cajas_agrupadas = 0
        self.esquinas_placa_xy: list[list[float]] | None = None
        self.rescate_lateral: dict | None = None

    def guardar(self, captura_id: str, decision: dict) -> dict:
        carpeta = None
        try:
            EVIDENCIAS_DIR.mkdir(parents=True, exist_ok=True)
            # El sufijo aleatorio evita sobrescribir evidencia si se reintenta el mismo ID.
            carpeta = Path(tempfile.mkdtemp(prefix=f"{captura_id}-", dir=EVIDENCIAS_DIR))
            (carpeta / "original.jpg").write_bytes(self.original)
            for nombre, imagen in self.imagenes.items():
                codificada, buffer = cv2.imencode(Path(nombre).suffix, imagen)
                if not codificada:
                    raise OSError(f"No se pudo codificar {nombre}.")
                (carpeta / nombre).write_bytes(buffer.tobytes())
            for nombre, contenido in self.entradas_ocr.items():
                (carpeta / nombre).write_bytes(contenido)
            datos = {
                **decision,
                "umbral_detector": UMBRAL_DETECTOR,
                "umbral_ocr": UMBRAL_OCR,
                "resolucion_original": self.resolucion,
                "detecciones": self.detecciones,
                "recorte_xyxy": self.recorte_xyxy,
                "cajas_agrupadas": self.cajas_agrupadas,
                "esquinas_placa_xy": self.esquinas_placa_xy,
                "lecturas_ocr": self.lecturas_ocr,
            }
            if self.rescate_lateral is not None:
                datos["rescate_lateral"] = self.rescate_lateral
            (carpeta / "diagnostico.json").write_text(json.dumps(datos, ensure_ascii=False, indent=2), encoding="utf-8")
        except (OSError, cv2.error) as error:
            logger.warning("No se pudo guardar toda la evidencia de %s: %s", captura_id, error)
            return {"carpeta": carpeta.name if carpeta else None, "completa": False}
        return {"carpeta": carpeta.name, "completa": True}


def ejecutar_tesseract(ejecutable: str, argumentos: list[str], imagen: bytes | None = None) -> str:
    # Envía la imagen al ejecutable por stdin y recupera el texto desde stdout.
    proceso = subprocess.run(
        [ejecutable, *argumentos],
        input=imagen,
        capture_output=True,
        timeout=10,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if proceso.returncode:
        detalle = proceso.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Tesseract devolvió un error: {detalle}")
    return proceso.stdout.decode("utf-8-sig")


def abrir_bd():
    # La consulta de una placa abre SQLite en modo de solo lectura.
    return sqlite3.connect(f"{PLACAS_DB.as_uri()}?mode=ro", uri=True)


def preparar_tesseract() -> str:
    ruta_windows = Path(os.getenv("ProgramFiles", r"C:\Program Files")) / "Tesseract-OCR" / "tesseract.exe"
    tesseract = os.getenv("TESSERACT_EXE") or shutil.which("tesseract")
    if not tesseract and ruta_windows.is_file():
        tesseract = str(ruta_windows)
    if not tesseract:
        raise RuntimeError("Instala Tesseract o indica su ejecutable con TESSERACT_EXE.")
    idiomas = ejecutar_tesseract(tesseract, ["--list-langs"])
    if "eng" not in idiomas.splitlines():
        raise RuntimeError("Falta el idioma eng en la instalación de Tesseract.")
    return tesseract


def preparar_fastplate():
    # Se cargan los mismos pesos ONNX y la configuración RGB usados en las pruebas.
    try:
        from fast_plate_ocr import LicensePlateRecognizer
    except ImportError as error:
        raise RuntimeError('Instala fast-plate-ocr[onnx]==1.1.0 en el entorno de la API.') from error

    carpeta_predeterminada = Path(__file__).resolve().parent / "modelos" / "fastplate"
    carpeta = Path(os.getenv("PLACAS_FASTPLATE_DIR", str(carpeta_predeterminada))).expanduser()
    pesos = carpeta / "cct_s_v2_global.onnx"
    configuracion = carpeta / "cct_s_v2_global_plate_config.yaml"
    if not pesos.is_file() or not configuracion.is_file():
        raise RuntimeError(f"Faltan los archivos de cct-s-v2-global-model en {carpeta}. Revisa PLACAS_FASTPLATE_DIR.")
    return LicensePlateRecognizer(
        onnx_model_path=pesos, plate_config_path=configuracion, providers=["CPUExecutionProvider"],
    )


@asynccontextmanager
async def lifespan(app: FastAPI):
    # El motor se selecciona una vez al arrancar y se carga solo el necesario.
    motor = os.getenv("PLACAS_OCR_MOTOR", "tesseract").strip().lower()
    if motor not in {"tesseract", "fastplate"}:
        raise RuntimeError("PLACAS_OCR_MOTOR debe ser tesseract o fastplate.")
    app.state.tesseract = preparar_tesseract() if motor == "tesseract" else None
    app.state.fastplate = preparar_fastplate() if motor == "fastplate" else None

    with closing(abrir_bd()) as bd:
        bd.execute("SELECT placa FROM placas LIMIT 1").fetchone()
    pesos = hf_hub_download(
        repo_id="Koushim/yolov8-license-plate-detection",
        filename="best.pt",
        local_files_only=True,
    )
    app.state.detector = YOLO(pesos)
    app.state.lector_lateral = None
    # El segundo lector solo se carga al habilitar la confirmación para vistas laterales.
    if motor == "tesseract" and os.getenv("PLACAS_OCR_LATERAL") == "1":
        try:
            from easyocr import Reader

            app.state.lector_lateral = Reader(["en"], gpu=False, detector=False, download_enabled=False, verbose=False)
        except (ImportError, OSError, RuntimeError) as error:
            raise RuntimeError("PLACAS_OCR_LATERAL requiere EasyOCR y sus pesos en inglés instalados.") from error
        logger.info("Verificación lateral con Tesseract y EasyOCR habilitada.")
    yield


app = FastAPI(title="Reconocimiento de placas", lifespan=lifespan)


@app.get("/evidencias/{carpeta}/{vista}", response_class=FileResponse)
def ver_evidencia(request: Request, carpeta: str, vista: Literal["foto", "recorte"]) -> FileResponse:
    # El navegador del mismo computador pide una de las dos vistas del panel.
    # Se aceptan nombres de carpeta válidos y los archivos previstos dentro de ella.
    if (
        request.client is None
        or request.client.host not in {"127.0.0.1", "::1", "::ffff:127.0.0.1"}
        or not re.fullmatch(r"[A-Za-z0-9_-]{1,73}", carpeta)
    ):
        raise HTTPException(404, "Imagen no disponible.")

    directorio = EVIDENCIAS_DIR / carpeta
    if directorio.resolve() != directorio:
        raise HTTPException(404, "Imagen no disponible.")

    # La foto anotada es la primera opción. Si falta, se muestra el JPEG original.
    nombres = ("detecciones.jpg", "original.jpg") if vista == "foto" else ("recorte_yolo.png",)
    for nombre in nombres:
        archivo = directorio / nombre
        if archivo.is_file() and archivo.resolve() == archivo:
            tipo = "image/jpeg" if archivo.suffix == ".jpg" else "image/png"
            return FileResponse(archivo, media_type=tipo, headers={"Cache-Control": "private, no-store"})

    raise HTTPException(404, "Imagen no disponible.")


def validar_jpeg(contenido: bytearray) -> None:
    # Verifica el formato y el tamaño descomprimido antes de usar OpenCV y YOLO.
    if not contenido.startswith(b"\xff\xd8\xff"):
        raise HTTPException(400, "El cuerpo debe contener un JPEG válido.")
    try:
        with Image.open(BytesIO(contenido), formats=["JPEG"]) as imagen:
            if imagen.width * imagen.height > MAX_JPEG_PIXELES:
                raise HTTPException(413, "La imagen supera los 12 megapíxeles.")
            imagen.load()
    except Image.DecompressionBombError as error:
        raise HTTPException(413, "La imagen supera los 12 megapíxeles.") from error
    except (UnidentifiedImageError, OSError) as error:
        raise HTTPException(400, "El cuerpo debe contener un JPEG válido.") from error


def leer_lineas(
    tesseract: str, imagen: np.ndarray, psm: int, diagnostico: DiagnosticoCaptura, variante: str,
    restringir_caracteres: bool = True,
) -> list[tuple[str, float]]:
    _, png = cv2.imencode(".png", imagen)
    contenido = png.tobytes()
    diagnostico.entradas_ocr[f"ocr_{variante}.png"] = contenido
    argumentos = ["stdin", "stdout", "-l", "eng", "--oem", "1", "--psm", str(psm)]
    if restringir_caracteres:
        argumentos.extend(["-c", "tessedit_char_whitelist=ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-"])
    salida = ejecutar_tesseract(tesseract, [*argumentos, "tsv"], contenido)

    # El TSV trae las palabras y su confianza. Para cada línea se conserva el mínimo.
    palabras_por_linea = {}
    for fila in csv.DictReader(salida.splitlines(), delimiter="\t", quoting=csv.QUOTE_NONE):
        texto = fila["text"].strip()
        if fila["level"] == "5" and texto:
            clave = (fila["page_num"], fila["block_num"], fila["par_num"], fila["line_num"])
            palabras_por_linea.setdefault(clave, []).append((texto, float(fila["conf"]) / 100))

    lineas = [
        (" ".join(texto for texto, _ in palabras), min(conf for _, conf in palabras))
        for palabras in palabras_por_linea.values()
    ]
    diagnostico.lecturas_ocr[variante] = lineas
    return lineas


def contornos_amarillos(recorte: np.ndarray, brillo_minimo: int = 90):
    # La máscara HSV separa el fondo amarillo para buscar el borde de la placa.
    hsv = cv2.cvtColor(recorte, cv2.COLOR_BGR2HSV)
    mascara = cv2.inRange(hsv, (12, 80, brillo_minimo), (42, 255, 255))
    contornos, _ = cv2.findContours(mascara, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    return contornos


def esquinas_contorno(contorno: np.ndarray) -> np.ndarray | None:
    perimetro = cv2.arcLength(contorno, True)
    for tolerancia in (0.012, 0.025, 0.035):
        vertices = cv2.approxPolyDP(contorno, tolerancia * perimetro, True).reshape(-1, 2)
        if len(vertices) == 4:
            break
    else:
        return None

    sumas = vertices.sum(axis=1)
    diferencias = vertices[:, 1] - vertices[:, 0]
    # Orden de las esquinas para proyectarlas en el mismo orden sobre el rectángulo.
    orden = [sumas.argmin(), diferencias.argmin(), sumas.argmax(), diferencias.argmax()]
    if len(set(orden)) != 4:
        return None
    return vertices[orden].astype(np.float32)


def buscar_esquinas_placa(recorte: np.ndarray, brillo_minimo: int = 90) -> np.ndarray | None:
    # Solo se usa una geometría si aparece un contorno único con proporciones de placa.
    posibles = []
    for contorno in contornos_amarillos(recorte, brillo_minimo):
        envolvente = cv2.convexHull(contorno)
        _, _, ancho, alto = cv2.boundingRect(envolvente)
        if ancho < 32 or alto < 16 or not 1.25 <= ancho / alto <= 3.5:
            continue
        area = cv2.contourArea(envolvente)
        if area < 0.65 * ancho * alto or cv2.contourArea(contorno) < 0.72 * area:
            continue
        esquinas = esquinas_contorno(envolvente)
        if esquinas is not None:
            posibles.append(esquinas)
    if len(posibles) != 1 or cv2.contourArea(posibles[0]) < 0.15 * recorte.shape[0] * recorte.shape[1]:
        return None
    return posibles[0]


def rectificar_placa(recorte: np.ndarray, esquinas: np.ndarray, ancho: int, alto: int) -> np.ndarray:
    # Convierte la vista inclinada en una vista frontal aproximada para el OCR.
    destino = np.float32([[0, 0], [ancho - 1, 0], [ancho - 1, alto - 1], [0, alto - 1]])
    matriz = cv2.getPerspectiveTransform(esquinas, destino)
    return cv2.warpPerspective(recorte, matriz, (ancho, alto))


def aislar_linea_amarilla(recorte: np.ndarray) -> np.ndarray | None:
    # Intenta aislar seis componentes de tamaño compatible con los caracteres.
    contornos = contornos_amarillos(recorte)
    if not contornos:
        return None
    contorno = cv2.convexHull(max(contornos, key=cv2.contourArea))
    _, _, ancho, alto = cv2.boundingRect(contorno)
    if not 1.4 <= ancho / alto <= 3.5 or cv2.contourArea(contorno) < recorte.shape[0] * recorte.shape[1] * 0.25:
        return None
    esquinas = esquinas_contorno(contorno)
    if esquinas is None:
        return None
    rectificada = rectificar_placa(recorte, esquinas, ancho, alto)

    gris = cv2.cvtColor(rectificada, cv2.COLOR_BGR2GRAY)
    _, binaria = cv2.threshold(gris, 0, 255, cv2.THRESH_BINARY_INV | cv2.THRESH_OTSU)
    _, etiquetas, regiones, _ = cv2.connectedComponentsWithStats(binaria)
    componentes = [
        i for i, (x, y, w, h, area) in enumerate(regiones[1:], 1)
        if 0.26 * alto < h < 0.87 * alto and 0.027 * ancho < w < 0.24 * ancho
        and 0 < x < x + w < ancho and 0 < y < y + h < alto and area > 0.1 * w * h
    ]
    if len(componentes) != 6:
        return None
    mascara_texto = np.isin(etiquetas, componentes)
    filas, columnas = np.where(mascara_texto)
    linea = mascara_texto[filas.min():filas.max() + 1, columnas.min():columnas.max() + 1]
    return np.where(linea, 0, 255).astype(np.uint8)


def reconocer_linea_amarilla(
    tesseract: str, recorte: np.ndarray, diagnostico: DiagnosticoCaptura,
) -> dict[str, float]:
    linea = aislar_linea_amarilla(recorte)
    if linea is None:
        return {}

    # La segunda lectura comprueba que el texto se mantenga al cambiar la escala.
    borde = cv2.copyMakeBorder(linea, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=255)
    imagenes = {}
    for alto in (100, 200):
        ancho = round(borde.shape[1] * alto / borde.shape[0])
        imagenes[alto] = cv2.resize(borde, (ancho, alto), interpolation=cv2.INTER_AREA)

    candidatas = {}
    # PSM 8 relee la matrícula como una palabra cuando la línea no se confirma.
    for psm in (7, 8):
        lecturas = []
        for alto, imagen in imagenes.items():
            variante = f"linea_amarilla_{alto}" if psm == 7 else f"linea_amarilla_palabra_{alto}"
            filas = leer_lineas(tesseract, imagen, psm, diagnostico, variante, restringir_caracteres=False)
            if len(filas) != 1:
                break
            texto, confianza = filas[0]
            lecturas.append((re.sub(r"[\s-]+", "", texto), confianza))

        if len(lecturas) != 2:
            continue
        (primera, confianza_primera), (segunda, confianza_segunda) = lecturas
        if primera == segunda and FORMATO_PLACA.fullmatch(primera):
            confianza = min(confianza_primera, confianza_segunda)
            candidatas[primera] = max(candidatas.get(primera, 0.0), confianza)
            if confianza >= UMBRAL_OCR:
                break
    return candidatas


def leer_franja_rectificada(
    tesseract: str, gris: np.ndarray, diagnostico: DiagnosticoCaptura, variante: str,
) -> tuple[str, float] | None:
    alto, ancho = gris.shape
    _, binaria = cv2.threshold(gris, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    # Excluye el borde y la línea inferior donde aparece el municipio.
    franja = binaria[round(alto * 0.05):round(alto * 0.767), round(ancho * 0.025):round(ancho * 0.975)]
    entrada = cv2.copyMakeBorder(franja, 15, 15, 15, 15, cv2.BORDER_CONSTANT, value=255)
    filas = leer_lineas(tesseract, entrada, 7, diagnostico, variante, restringir_caracteres=False)
    if len(filas) != 1:
        return None
    texto, confianza = filas[0]
    return re.sub(r"[\s-]+", "", texto), confianza


def reconocer_placa_rectificada(
    tesseract: str, recorte: np.ndarray, esquinas: np.ndarray, diagnostico: DiagnosticoCaptura,
) -> dict[str, float]:
    # Dos tamaños de una misma vista rectificada deben producir la misma matrícula.
    imagenes = []
    lecturas = []
    for ancho in (384, 640):
        rectificada = rectificar_placa(recorte, esquinas, ancho, ancho // 2)
        gris = cv2.cvtColor(rectificada, cv2.COLOR_BGR2GRAY)
        lectura = leer_franja_rectificada(tesseract, gris, diagnostico, f"rectificada_{ancho}")
        if lectura is None:
            return {}
        imagenes.append(gris)
        lecturas.append(lectura)

    (primera, confianza_primera), (segunda, confianza_segunda) = lecturas
    if primera != segunda or not FORMATO_PLACA.fullmatch(primera):
        return {}

    menor = min(confianza_primera, confianza_segunda)
    mayor = max(confianza_primera, confianza_segunda)
    if menor < UMBRAL_OCR <= mayor:
        # Reintenta solo la escala que quedó por debajo del umbral.
        debil = imagenes[0] if confianza_primera < confianza_segunda else imagenes[1]
        suavizada = cv2.GaussianBlur(debil, (5, 5), 0)
        reintento = leer_franja_rectificada(
            tesseract, suavizada, diagnostico, f"rectificada_{debil.shape[1]}_suavizada",
        )
        if reintento is not None and reintento[0] == primera:
            menor = min(mayor, max(menor, reintento[1]))
    return {primera: menor}


def reconocer_placa(
    tesseract: str, recorte: np.ndarray, diagnostico: DiagnosticoCaptura, esquinas: np.ndarray | None,
) -> dict[str, float]:
    # Empieza con el recorte completo en gris, ampliado y binarizado.
    gris = cv2.cvtColor(recorte, cv2.COLOR_BGR2GRAY)
    escalado = cv2.resize(gris, None, fx=2, fy=2, interpolation=cv2.INTER_CUBIC)
    _, binaria = cv2.threshold(escalado, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    borde = cv2.copyMakeBorder(binaria, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=255)
    lecturas = leer_lineas(tesseract, borde, 6, diagnostico, "completo")
    if not any(
        FORMATO_PLACA.fullmatch(re.sub(r"[\s-]+", "", texto)) and conf >= UMBRAL_OCR
        for texto, conf in lecturas
    ):
        # En placas de dos líneas, el municipio suele ocupar la franja inferior.
        superior = binaria[:round(binaria.shape[0] * 0.80)]
        superior = cv2.copyMakeBorder(superior, 10, 10, 10, 10, cv2.BORDER_CONSTANT, value=255)
        lecturas = lecturas + leer_lineas(tesseract, superior, 7, diagnostico, "franja_superior")

    candidatas = {}
    for texto, confianza in lecturas:
        placa = re.sub(r"[\s-]+", "", texto)
        if FORMATO_PLACA.fullmatch(placa):
            candidatas[placa] = max(candidatas.get(placa, 0.0), confianza)
    if not any(confianza >= UMBRAL_OCR for confianza in candidatas.values()):
        for placa, confianza in reconocer_linea_amarilla(tesseract, recorte, diagnostico).items():
            candidatas[placa] = max(candidatas.get(placa, 0.0), confianza)
    if esquinas is not None and not any(confianza >= UMBRAL_OCR for confianza in candidatas.values()):
        for placa, confianza in reconocer_placa_rectificada(tesseract, recorte, esquinas, diagnostico).items():
            candidatas[placa] = max(candidatas.get(placa, 0.0), confianza)
    return candidatas


def limites_recorte(caja, ancho: int, alto: int) -> list[int]:
    x1, y1, x2, y2 = map(int, caja)
    return [max(0, x1 - 5), max(0, y1 - 5), min(ancho, x2 + 5), min(alto, y2 + 5)]


def contencion_cajas(primera, segunda) -> float:
    # Mide cuánto de la caja menor queda cubierto por la otra detección.
    x1, y1, x2, y2 = primera
    a1, b1, a2, b2 = segunda
    cruce_x = max(0, min(x2, a2) - max(x1, a1))
    cruce_y = max(0, min(y2, b2) - max(y1, b1))
    area_menor = min((x2 - x1) * (y2 - y1), (a2 - a1) * (b2 - b1))
    return cruce_x * cruce_y / area_menor if area_menor > 0 else 0.0


def buscar_placa_compartida(imagen: np.ndarray, cajas: list[list[float]]) -> np.ndarray | None:
    # Agrupa cajas muy solapadas si un mismo contorno amarillo es compatible con todas.
    if any(contencion_cajas(a, b) < CONTENCION_MINIMA_CAJAS for a, b in combinations(cajas, 2)):
        return None

    alto, ancho = imagen.shape[:2]
    union = [min(c[0] for c in cajas), min(c[1] for c in cajas), max(c[2] for c in cajas), max(c[3] for c in cajas)]
    x1, y1, x2, y2 = limites_recorte(union, ancho, alto)
    region = imagen[y1:y2, x1:x2]
    if not region.size:
        return None
    esquinas = buscar_esquinas_placa(region)
    if esquinas is None:
        return None

    esquinas += np.float32([x1, y1])
    area_placa = cv2.contourArea(esquinas)
    for a, b, c, d in cajas:
        marco = np.float32([[a - 5, b - 5], [c + 5, b - 5], [c + 5, d + 5], [a - 5, d + 5]])
        area_comun, _ = cv2.intersectConvexConvex(esquinas, marco)
        if area_comun < COBERTURA_MINIMA_CONTORNO * area_placa:
            return None
    return esquinas


def rectificar_candidata_lateral(
    imagen: np.ndarray, cajas: list[list[float]], *, sin_realce: bool = False,
) -> tuple[np.ndarray, np.ndarray] | None:
    # Amplía el recorte para recuperar bordes que quedaron fuera de las cajas de YOLO.
    if any(contencion_cajas(a, b) < 0.45 for a, b in combinations(cajas, 2)):
        return None
    alto, ancho = imagen.shape[:2]
    izquierda = min(c[0] for c in cajas)
    arriba = min(c[1] for c in cajas)
    derecha = max(c[2] for c in cajas)
    abajo = max(c[3] for c in cajas)
    margen_x, margen_y = round((derecha - izquierda) * 0.08), round((abajo - arriba) * 0.08)
    x1, y1 = max(0, int(izquierda) - margen_x), max(0, int(arriba) - margen_y)
    x2, y2 = min(ancho, int(derecha) + margen_x), min(alto, int(abajo) + margen_y)
    recorte = imagen[y1:y2, x1:x2]
    if not recorte.size:
        return None
    # Cada máscara localiza las esquinas. La rectificación siempre parte del recorte original.
    if sin_realce:
        esquinas = buscar_esquinas_placa(recorte, BRILLO_MINIMO_LATERAL_SIN_REALCE)
    else:
        esquinas = buscar_esquinas_placa(cv2.convertScaleAbs(recorte, alpha=1.7))
    if esquinas is None:
        return None
    esquinas_globales = esquinas + np.float32([x1, y1])
    area = cv2.contourArea(esquinas)
    for a, b, c, d in cajas:
        marco = np.float32([[a, b], [c, b], [c, d], [a, d]])
        interseccion, _ = cv2.intersectConvexConvex(esquinas_globales, marco)
        if interseccion < 0.50 * min(area, (c - a) * (d - b)):
            return None
    return rectificar_placa(recorte, esquinas, 560, 280), esquinas_globales


def aislar_caracteres_laterales(binaria: np.ndarray) -> np.ndarray | None:
    alto, ancho = binaria.shape
    _, etiquetas, regiones, _ = cv2.connectedComponentsWithStats(255 - binaria)
    caracteres = [
        i for i, (x, y, w, h, area) in enumerate(regiones[1:], 1)
        if 0.32 * alto <= h < 0.98 * alto and 0.027 * ancho < w < 0.23 * ancho
        and area > 0.12 * w * h and 0 < x < x + w < ancho and 0 < y < y + h < alto
    ]
    if len(caracteres) != 6:
        return None
    mascara = np.isin(etiquetas, caracteres)
    filas, columnas = np.where(mascara)
    franja = mascara[filas.min():filas.max() + 1, columnas.min():columnas.max() + 1]
    return np.where(franja, 0, 255).astype(np.uint8)


def preparar_vistas_laterales(rectificada: np.ndarray) -> dict[str, np.ndarray]:
    # Estima el fondo para compensar iluminación desigual y genera variantes de contraste.
    gris = cv2.cvtColor(rectificada, cv2.COLOR_BGR2GRAY)
    fondo = cv2.GaussianBlur(gris, (0, 0), 27)
    franja = cv2.divide(gris, np.maximum(fondo, 1), scale=200)[14:215, 14:546]
    _, otsu = cv2.threshold(franja, 0, 255, cv2.THRESH_BINARY | cv2.THRESH_OTSU)
    adaptativa = cv2.adaptiveThreshold(franja, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, 71, 9)
    entradas = {"gris": franja, "otsu": otsu, "adaptativa": adaptativa}
    caracteres = aislar_caracteres_laterales(otsu)
    if caracteres is not None:
        entradas["caracteres"] = caracteres
    return {
        nombre: cv2.copyMakeBorder(vista, 16, 16, 16, 16, cv2.BORDER_CONSTANT, value=255)
        for nombre, vista in entradas.items()
    }


def leer_placa_lateral(imagen, tesseract, lector, detecciones, diagnostico) -> tuple[str, float] | None:
    # Prueba primero el contorno con realce y luego con un umbral de brillo menor.
    cajas = [caja["xyxy"] for caja in detecciones]
    esquinas_previas = None
    for sin_realce in (False, True):
        candidata = rectificar_candidata_lateral(imagen, cajas, sin_realce=sin_realce)
        if candidata is None:
            continue
        rectificada, esquinas = candidata
        if np.array_equal(esquinas, esquinas_previas):
            continue
        esquinas_previas = esquinas
        sufijo = "_sin_realce" if sin_realce else ""
        diagnostico.rescate_lateral.update(
            estado="sin confirmar", esquinas_xy=esquinas.tolist(),
            metodo_contorno="sin realce" if sin_realce else "con realce",
        )
        diagnostico.imagenes[f"placa_rectificada_lateral{sufijo}.png"] = rectificada
        lectura = leer_lateral_rectificada(imagen, rectificada, esquinas, tesseract, lector, diagnostico, sufijo)
        if lectura is not None:
            return lectura
    return None


def leer_lateral_rectificada(imagen, rectificada, esquinas, tesseract, lector, diagnostico, sufijo) -> tuple[str, float] | None:
    # Tesseract debe proponer una matrícula válida en al menos dos variantes.
    votos = {}
    for nombre, vista in preparar_vistas_laterales(rectificada).items():
        filas = leer_lineas(tesseract, vista, 7, diagnostico, f"lateral{sufijo}_{nombre}", restringir_caracteres=False)
        if len(filas) != 1:
            continue
        texto, confianza = filas[0]
        lectura = FORMATO_CON_SEPARADOR.fullmatch(texto)
        if lectura and confianza >= UMBRAL_OCR:
            votos.setdefault("".join(lectura.groups()), []).append(confianza)
    if len(votos) != 1:
        return None
    placa, confianzas = next(iter(votos.items()))
    if len(confianzas) < 2:
        return None

    # EasyOCR verifica el texto sobre vistas de la placa que ya se localizó y rectificó.
    vistas_easyocr = preparar_vistas_laterales(rectificar_placa(imagen, esquinas, 560, 280))
    lecturas = []
    with bloqueo_easyocr:
        for nombre in ("gris", "caracteres" if "caracteres" in vistas_easyocr else "otsu"):
            vista = vistas_easyocr[nombre]
            etiqueta = f"easyocr_lateral{sufijo}_{nombre}"
            diagnostico.imagenes[f"{etiqueta}.png"] = vista
            filas = lector.recognize(vista, allowlist="ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789", workers=0)
            lineas = [(texto, float(confianza)) for _, texto, confianza in filas]
            diagnostico.lecturas_ocr[etiqueta] = lineas
            lecturas.extend(lineas)
    firmes = {texto for texto, confianza in lecturas if confianza >= UMBRAL_EASYOCR_LATERAL and FORMATO_PLACA.fullmatch(texto)}
    if firmes != {placa}:
        diagnostico.rescate_lateral["estado"] = "sin coincidencia entre OCR"
        return None
    confianza_easyocr = max(confianza for texto, confianza in lecturas if texto == placa)
    diagnostico.rescate_lateral.update(estado="confirmada", confianza_easyocr=round(confianza_easyocr, 4))
    return placa, min(confianzas)


def procesar_captura(
    detector: YOLO, tesseract: str, contenido: bytearray, diagnostico: DiagnosticoCaptura, lector_lateral=None,
    *, fastplate=None,
) -> dict:
    # Coordina la detección, la lectura base y el reintento lateral opcional.
    validar_jpeg(contenido)
    diagnostico.original = contenido
    imagen = cv2.imdecode(np.frombuffer(contenido, dtype=np.uint8), cv2.IMREAD_COLOR)
    if imagen is None:
        raise HTTPException(400, "OpenCV no pudo decodificar el JPEG.")
    alto, ancho = imagen.shape[:2]
    diagnostico.resolucion = {"ancho": ancho, "alto": alto}

    # La segunda escala de YOLO se usa cuando la primera no devuelve cajas.
    with bloqueo_detector:
        for tamano in (640, 320):
            prediccion = detector.predict(
                source=imagen, imgsz=tamano, conf=UMBRAL_DETECTOR, device="cpu", verbose=False,
            )[0]
            cajas = prediccion.boxes
            diagnostico.detecciones.append({
                "tamano_entrada_yolo": tamano,
                "cajas": [
                    {"xyxy": coordenadas, "confianza": round(confianza, 4)}
                    for coordenadas, confianza in zip(cajas.xyxy.tolist(), cajas.conf.tolist())
                ],
            })
            if len(cajas):
                break

    detecciones = diagnostico.detecciones[-1]["cajas"]
    resultado = leer_captura_base(imagen, tesseract, detecciones, diagnostico, fastplate=fastplate)
    if fastplate is not None or lector_lateral is None or resultado["decision"] != "NO CONCLUYENTE" or not detecciones:
        return resultado
    if resultado["motivo"] == "Tesseract produjo lecturas de matrícula distintas.":
        return resultado

    # El reintento parte de una abstención y conserva su motivo en el diagnóstico.
    diagnostico.rescate_lateral = {"estado": "sin confirmar", "motivo_original": resultado["motivo"]}
    try:
        confirmada = leer_placa_lateral(imagen, tesseract, lector_lateral, detecciones, diagnostico)
    except (OSError, RuntimeError, subprocess.TimeoutExpired, cv2.error) as error:
        diagnostico.rescate_lateral.update(estado="error", error=type(error).__name__)
        logger.warning("Falló el reintento de OCR lateral: %s", error)
        return resultado
    if confirmada is None:
        return resultado
    placa, confianza = confirmada
    resultado["confianza_detector"] = max(caja["confianza"] for caja in detecciones)
    return consultar_placa(placa, confianza, resultado, "Lectura lateral confirmada por ambos OCR y consultada en SQLite.")


def leer_captura_base(
    imagen, tesseract: str, detecciones: list[dict], diagnostico: DiagnosticoCaptura, *, fastplate=None,
) -> dict:
    # Se mantiene NO CONCLUYENTE hasta contar con una sola lectura firme.
    resultado = {
        "placa": None,
        "decision": "NO CONCLUYENTE",
        "motivo": "No se detectó una placa sobre el umbral.",
        "confianza_detector": None,
        "confianza_ocr": None,
    }
    if not detecciones:
        return resultado
    alto, ancho = imagen.shape[:2]
    anotada = imagen.copy()
    for caja in detecciones:
        x1, y1, x2, y2 = map(int, caja["xyxy"])
        cv2.rectangle(anotada, (x1, y1), (x2, y2), (0, 220, 0), 2)
    diagnostico.imagenes["detecciones.jpg"] = anotada
    diagnostico.cajas_agrupadas = 1
    coordenadas = max(detecciones, key=lambda caja: caja["confianza"])
    limite = coordenadas["xyxy"]
    esquinas = None
    if len(detecciones) > 1:
        esquinas = buscar_placa_compartida(imagen, [caja["xyxy"] for caja in detecciones])
        if esquinas is None:
            diagnostico.cajas_agrupadas = 0
            resultado["motivo"] = "Se detectaron varias placas candidatas."
            return resultado
        diagnostico.cajas_agrupadas = len(detecciones)
        izquierda, arriba = esquinas.min(axis=0).tolist()
        derecha, abajo = esquinas.max(axis=0).tolist()
        limite = [min(limite[0], izquierda), min(limite[1], arriba), max(limite[2], derecha), max(limite[3], abajo)]

    resultado["confianza_detector"] = coordenadas["confianza"]
    x1, y1, x2, y2 = limites_recorte(limite, ancho, alto)
    recorte = imagen[y1:y2, x1:x2]
    if not recorte.size:
        resultado["motivo"] = "El detector entregó un recorte vacío."
        return resultado
    diagnostico.imagenes["recorte_yolo.png"] = recorte
    diagnostico.recorte_xyxy = [x1, y1, x2, y2]
    # Las esquinas locales sirven para rectificar el recorte. Las globales se dibujan en la foto.
    origen = np.float32([x1, y1])
    esquinas_recorte = buscar_esquinas_placa(recorte) if esquinas is None else esquinas - origen
    if esquinas_recorte is not None:
        esquinas_original = esquinas_recorte + origen
        diagnostico.esquinas_placa_xy = esquinas_original.tolist()
        con_esquinas = anotada.copy()
        cv2.polylines(con_esquinas, [esquinas_original.astype(np.int32)], True, (0, 180, 255), 2)
        diagnostico.imagenes["esquinas_placa.jpg"] = con_esquinas
        diagnostico.imagenes["placa_rectificada.png"] = rectificar_placa(recorte, esquinas_recorte, 640, 320)

    if fastplate is not None:
        return leer_recorte_fastplate(fastplate, recorte, diagnostico, resultado)

    try:
        candidatas = reconocer_placa(tesseract, recorte, diagnostico, esquinas_recorte)
    except (OSError, RuntimeError, subprocess.TimeoutExpired) as error:
        logger.warning("Falló Tesseract: %s", error)
        raise HTTPException(503, "Tesseract no pudo procesar la imagen.") from error

    if not candidatas:
        resultado["motivo"] = "Tesseract no leyó una matrícula con formato ABC123 o ABC12D."
        return resultado
    confiables = [(placa, confianza) for placa, confianza in candidatas.items() if confianza >= UMBRAL_OCR]
    if len(confiables) > 1:
        resultado["motivo"] = "Tesseract produjo lecturas de matrícula distintas."
        return resultado
    if not confiables:
        if len(candidatas) == 1:
            # La respuesta puede incluir una lectura tentativa para facilitar la revisión.
            placa, confianza = next(iter(candidatas.items()))
            resultado.update(placa=placa, confianza_ocr=round(confianza, 4))
        resultado["motivo"] = "La lectura quedó por debajo del umbral de Tesseract."
        return resultado

    placa, confianza = confiables[0]
    return consultar_placa(placa, confianza, resultado, "Lectura aceptada y consulta exacta a SQLite completada.")


def leer_recorte_fastplate(lector, recorte: np.ndarray, diagnostico: DiagnosticoCaptura, resultado: dict) -> dict:
    # Se usa el recorte directo en color. ONNX aplica el tamaño de entrada del modelo.
    rgb = cv2.cvtColor(recorte, cv2.COLOR_BGR2RGB)
    prediccion = lector.run_one(rgb, return_confidence=True)
    placa = prediccion.plate
    # El modelo tiene posiciones de relleno al final. Solo cuentan los caracteres leídos.
    confianza = float(min(prediccion.char_probs[:len(placa)])) if placa else 0.0
    diagnostico.lecturas_ocr["fastplate_color"] = [(placa, confianza)]
    if not FORMATO_PLACA.fullmatch(placa):
        resultado["motivo"] = "FastPlateOCR no leyó una matrícula con formato ABC123 o ABC12D."
        return resultado
    if confianza < UMBRAL_FASTPLATE:
        resultado.update(placa=placa, confianza_ocr=round(confianza, 4))
        resultado["motivo"] = "La lectura quedó por debajo del umbral de FastPlateOCR."
        return resultado
    return consultar_placa(placa, confianza, resultado, "Lectura de FastPlateOCR aceptada y consultada en SQLite.")


def consultar_placa(placa: str, confianza: float, resultado: dict, motivo: str) -> dict:
    # Consulta la matrícula aceptada por igualdad exacta en la tabla placas.
    resultado.update(placa=placa, confianza_ocr=round(confianza, 4))
    try:
        with closing(abrir_bd()) as bd:
            existe = bd.execute("SELECT 1 FROM placas WHERE placa = ?", (placa,)).fetchone()
    except sqlite3.Error as error:
        logger.warning("Falló la consulta a SQLite: %s", error)
        raise HTTPException(503, "No se pudo consultar el registro de placas.") from error

    resultado["decision"] = "REGISTRADA" if existe else "NO REGISTRADA"
    resultado["motivo"] = motivo
    return resultado


@app.post("/capturas")
async def recibir_captura(
    request: Request,
    captura_id: Annotated[str, Query(pattern=r"^[A-Za-z0-9_-]{1,64}$")],
) -> dict:
    # CameraWebServer envía el JPEG como cuerpo HTTP y el ID en la URL.
    tipo = request.headers.get("content-type", "").split(";", 1)[0].strip().lower()
    if tipo != "image/jpeg":
        raise HTTPException(415, "Envía el JPEG con Content-Type: image/jpeg.")

    # Lee en bloques y corta la carga al superar el límite de bytes.
    contenido = bytearray()
    async for bloque in request.stream():
        if len(contenido) + len(bloque) > MAX_JPEG_BYTES:
            raise HTTPException(413, "El JPEG supera los 5 MiB.")
        contenido.extend(bloque)

    # Este tiempo empieza tras recibir el JPEG y termina al decidir.
    inicio = perf_counter()
    diagnostico = DiagnosticoCaptura()
    fastplate = request.app.state.fastplate
    try:
        resultado = await run_in_threadpool(
            procesar_captura, request.app.state.detector, request.app.state.tesseract, contenido, diagnostico,
            request.app.state.lector_lateral, fastplate=fastplate,
        )
    except Exception as error:
        if diagnostico.original is not None:
            motivo = error.detail if isinstance(error, HTTPException) else type(error).__name__
            await run_in_threadpool(diagnostico.guardar, captura_id, {"captura_id": captura_id, "error": motivo})
        raise
    motor = "fastplate" if fastplate is not None else "tesseract"
    if diagnostico.rescate_lateral and diagnostico.rescate_lateral["estado"] == "confirmada":
        motor = "tesseract+easyocr"
    decision = {
        "captura_id": captura_id,
        **resultado,
        "ocr": motor,
        "simulada": False,
        "procesamiento_ms": round((perf_counter() - inicio) * 1000, 1),
    }
    # Primero guarda la evidencia. Después publica el resultado y la referencia a la carpeta.
    decision["evidencia"] = await run_in_threadpool(diagnostico.guardar, captura_id, decision)
    try:
        await run_in_threadpool(
            publish.single,
            TOPIC_DECISIONES,
            payload=json.dumps(decision),
            qos=1,
            retain=False,
            hostname=MQTT_HOST,
            port=MQTT_PORT,
        )
    except (OSError, MQTTException) as error:
        logger.warning("Error al publicar %s: %s", captura_id, error)
        raise HTTPException(503, "No se pudo publicar la decisión en Mosquitto.") from error

    return decision
