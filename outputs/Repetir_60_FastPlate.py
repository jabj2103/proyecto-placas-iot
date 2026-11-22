"""Repite las capturas C001 a C060 en la API local con FastPlateOCR."""

import argparse
import csv
import json
import os
import sqlite3
from collections import Counter
from contextlib import closing
from datetime import datetime
from pathlib import Path
from statistics import median
from tempfile import TemporaryDirectory
from unittest.mock import patch


SALIDAS = Path(__file__).resolve().parent
TRABAJO = SALIDAS.parent / "work"
KIT = SALIDAS / "Kit_bloque_controlado"
REGISTROS = SALIDAS / "Protocolo_pruebas_placas_luz_fija_capturas.registros"
MODELO = TRABAJO / "ocr_comparacion" / "modelos" / "fastplate"


def cargar_casos():
    manifiesto = TRABAJO / "ocr_comparacion" / "casos.csv"
    with manifiesto.open(encoding="utf-8-sig", newline="") as archivo:
        casos = sorted(
            (fila for fila in csv.DictReader(archivo, delimiter=";") if fila["vista"] == "original"),
            key=lambda fila: fila["caso"],
        )
    if [fila["caso"] for fila in casos] != [f"C{i:03}" for i in range(1, 61)]:
        raise ValueError("El manifiesto debe contener una referencia por caso, de C001 a C060.")
    for fila in casos:
        registro = REGISTROS / f'{fila["caso"]}.json'
        datos = json.loads(registro.read_text(encoding="utf-8-sig"))["datos"]
        original = KIT / "evidencias_controlado" / datos["evidencia"]["carpeta"] / "original.jpg"
        fila["jpeg"] = original.read_bytes()
    return casos


def enviar(cliente, caso, captura_id):
    respuesta = cliente.post(
        "/capturas", params={"captura_id": captura_id}, content=caso["jpeg"],
        headers={"Content-Type": "image/jpeg"},
    )
    if respuesta.status_code != 200:
        raise RuntimeError(f'{caso["caso"]}: HTTP {respuesta.status_code}. {respuesta.text}')
    resultado = respuesta.json()
    if resultado["ocr"] != "fastplate":
        raise RuntimeError(f'{caso["caso"]}: se esperaba FastPlateOCR y la API devolvió {resultado["ocr"]}.')
    return resultado


def evaluar(caso, resultado, autorizadas):
    inconclusa = resultado["decision"] == "NO CONCLUYENTE"
    lectura = "INCONCLUSA" if inconclusa else "EXACTA" if resultado["placa"] == caso["referencia"] else "INCORRECTA"
    registro = "REGISTRADA" if caso["referencia"] in autorizadas else "NO REGISTRADA"
    return {
        "caso": caso["caso"], "tipo": caso["tipo"], "angulo": caso["angulo"], "distancia_m": caso["distancia_m"],
        "referencia": caso["referencia"], "placa_leida": resultado["placa"], "lectura": lectura,
        "registro_esperado": registro, "decision_api": resultado["decision"],
        "motor": resultado["ocr"], "confianza_ocr": resultado["confianza_ocr"],
        "tiempo_api_ms": resultado["procesamiento_ms"], "motivo": resultado["motivo"],
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--salida", type=Path, default=SALIDAS, help="Carpeta donde se guardará el CSV.")
    args = parser.parse_args()
    casos = cargar_casos()
    base = KIT / "placas_controlado.db"
    with closing(sqlite3.connect(base.as_uri() + "?mode=ro", uri=True)) as bd:
        autorizadas = {fila[0] for fila in bd.execute("SELECT placa FROM placas")}

    args.salida.mkdir(parents=True, exist_ok=True)
    fecha = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
    destino = (args.salida / f"Resultados_FastPlate_60_{fecha}.csv").resolve()
    with TemporaryDirectory(prefix="placas_fastplate_") as temporal:
        os.environ.update(
            PLACAS_OCR_MOTOR="fastplate", PLACAS_DB=str(base), EVIDENCIAS_DIR=str(Path(temporal) / "evidencias"),
            YOLO_OFFLINE="true", HF_HUB_OFFLINE="1", YOLO_CONFIG_DIR=str(Path(temporal) / "yolo"),
        )
        os.environ.setdefault("PLACAS_FASTPLATE_DIR", str(MODELO))
        import api_placas as api
        import cv2
        import torch
        from fastapi.testclient import TestClient

        torch.set_num_threads(2)
        cv2.setNumThreads(2)
        filas = []
        # La repetición conserva la decisión de la API y simula su envío a MQTT.
        with patch.object(api.publish, "single"), TestClient(api.app) as cliente:
            enviar(cliente, casos[0], "calentamiento-fastplate")
            with destino.open("x", encoding="utf-8-sig", newline="") as archivo:
                for numero, caso in enumerate(casos, 1):
                    respuesta = enviar(cliente, caso, f'fastplate-{caso["caso"]}')
                    fila = evaluar(caso, respuesta, autorizadas)
                    if numero == 1:
                        escritor = csv.DictWriter(archivo, fieldnames=list(fila), delimiter=";")
                        escritor.writeheader()
                    escritor.writerow(fila)
                    archivo.flush()
                    filas.append(fila)
                    if numero % 10 == 0:
                        print(f"Procesadas {numero} de 60 capturas", flush=True)

    conteo = Counter(fila["lectura"] for fila in filas)
    print(f'Exactas {conteo["EXACTA"]}/60 | Incorrectas {conteo["INCORRECTA"]} | Inconclusas {conteo["INCONCLUSA"]}')
    print(f'Mediana de procesamiento en la API: {median(fila["tiempo_api_ms"] for fila in filas):.1f} ms')
    print(f"CSV: {destino}")


if __name__ == "__main__":
    main()
