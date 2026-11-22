"""Completa una copia de la hoja Casos usando los ID anotados en el Excel."""
import argparse
import base64
import json
import math
from io import BytesIO
from pathlib import Path
from urllib.parse import urlsplit

from openpyxl import load_workbook


def leer_har(rutas):
    tiempos = {}
    for archivo in rutas:
        registros = json.loads(archivo.read_text(encoding="utf-8-sig"))["log"]["entries"]
        for registro in registros:
            url = urlsplit(registro["request"]["url"])
            respuesta = registro["response"]
            if url.path != "/enviar" or respuesta.get("status") != 200:
                continue
            contenido = respuesta.get("content", {})
            cuerpo = contenido.get("text", "")
            if not cuerpo:
                continue
            try:
                if contenido.get("encoding") == "base64":
                    cuerpo = base64.b64decode(cuerpo, validate=True)
                datos = json.loads(cuerpo)
            except (ValueError, UnicodeError):
                continue
            if not isinstance(datos, dict):
                continue
            captura_id = datos.get("captura_id")
            tiempo = registro.get("time")
            if isinstance(captura_id, str) and isinstance(tiempo, (float, int)) and math.isfinite(tiempo) and tiempo >= 0:
                tiempos.setdefault(captura_id, set()).add(round(tiempo, 1))
    return tiempos


def leer_evidencias(carpeta):
    evidencias = {}
    for archivo in carpeta.glob("*/diagnostico.json"):
        try:
            datos = json.loads(archivo.read_text(encoding="utf-8-sig"))
        except (OSError, ValueError):
            print(f"Evidencia pendiente de revisar: {archivo.parent.name}")
            continue
        if isinstance(datos, dict) and isinstance(datos.get("captura_id"), str):
            evidencias.setdefault(datos["captura_id"], []).append(datos)
    return evidencias


def completar(hoja, columnas, evidencias, tiempos):
    filas = {}
    for fila in range(2, hoja.max_row + 1):
        valor = hoja.cell(fila, columnas["Captura ID"]).value
        if valor:
            captura_id = str(valor).strip()
            if captura_id in filas:
                raise ValueError(f"El ID {captura_id} aparece en más de una fila.")
            filas[captura_id] = fila

    if not filas:
        raise ValueError("Anota al menos un ID en la columna Captura ID de la hoja Casos.")

    def escribir(fila, columna, valor):
        celda = hoja.cell(fila, columnas[columna])
        if celda.value in (None, "") and valor is not None:
            celda.value = valor

    for captura_id, fila in filas.items():
        casos = evidencias.get(captura_id, [])
        if len(casos) == 1 and "decision" in casos[0]:
            datos = casos[0]
            decision = datos["decision"]
            placa = datos.get("placa")
            motivo = datos.get("motivo") or ""
            escribir(fila, "Decisión API", decision)
            if decision in ("REGISTRADA", "NO REGISTRADA"):
                escribir(fila, "Matrícula devuelta", placa)
            if decision == "NO CONCLUYENTE" and placa:
                motivo = f"{motivo} Candidata OCR sin aceptar: {placa}.".strip()
            escribir(fila, "API (ms)", datos.get("procesamiento_ms"))
            escribir(fila, "Nota / motivo", motivo or None)
        else:
            print(f"{captura_id}: verifica el JSON y la decisión original.")
        duraciones = tiempos.get(captura_id, set())
        if len(duraciones) == 1:
            escribir(fila, "Total (ms)", next(iter(duraciones)))
        elif hoja.cell(fila, columnas["Total (ms)"]).value in (None, ""):
            print(f"{captura_id}: tiempo de navegador pendiente.")
    return len(filas)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("excel", type=Path, help="Planilla .xlsx que contiene la hoja Casos")
    parser.add_argument("evidencias", type=Path, help="Carpeta que contiene las subcarpetas por captura")
    parser.add_argument("har", type=Path, nargs="*", help="Archivos HAR exportados desde el navegador")
    parser.add_argument("--salida", type=Path, help="Ruta para la copia de la planilla")
    args = parser.parse_args()
    try:
        if not args.evidencias.is_dir():
            raise ValueError(f"No se encuentra la carpeta: {args.evidencias}")
        libro = load_workbook(args.excel)
        hoja = libro["Casos"]
        columnas = {celda.value: celda.column for celda in hoja[1]}
        revisadas = completar(hoja, columnas, leer_evidencias(args.evidencias), leer_har(args.har))
        salida = args.salida or args.excel.with_name(args.excel.stem + "_importada.xlsx")
        datos = BytesIO()
        libro.save(datos)
        with salida.open("xb") as archivo:
            archivo.write(datos.getbuffer())
    except (OSError, ValueError, KeyError) as error:
        parser.exit(1, f"Error: {error}\n")
    print(f"Filas revisadas: {revisadas}\nCopia generada: {salida.resolve()}")


if __name__ == "__main__":
    main()
