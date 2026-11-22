import http.client
import json
import os
import re
import tempfile
import time
import tkinter as tk
from pathlib import Path
from queue import Empty, Queue
from threading import Thread
from tkinter import filedialog, messagebox, ttk
from urllib.error import HTTPError
from urllib.parse import urlsplit
from urllib.request import ProxyHandler, Request, build_opener

from openpyxl import load_workbook


PROYECTO = Path(__file__).resolve().parent
EVIDENCIAS = Path(os.getenv("EVIDENCIAS_DIR", str(PROYECTO.parent / "resultados" / "locales" / "evidencias")))
ESTADOS = {"REGISTRADA", "NO REGISTRADA", "NO CONCLUYENTE"}
CAMPOS = {"Caso", "Entorno", "Tipo", "Distancia real (m)", "Ángulo lateral (°)", "Movimiento",
          "Matrícula real", "Captura ID", "Decisión API", "Matrícula devuelta",
          "API (ms)", "Total (ms)", "Nota / motivo"}


def ruta_captura(direccion):
    if "://" not in direccion:
        direccion = "http://" + direccion
    url = urlsplit(direccion)
    if url.scheme != "http" or not url.hostname or url.username or url.password:
        raise ValueError("Escribe la dirección http de CameraWebServer.")
    if url.path not in ("", "/", "/enviar") or url.query or url.fragment:
        raise ValueError("Copia solo la dirección de CameraWebServer, sin rutas adicionales.")
    return f"http://{url.netloc}/enviar"


def tomar_captura(url):
    peticion = Request(url, data=b"", method="POST", headers={"Cache-Control": "no-cache"})
    cliente = build_opener(ProxyHandler({}))
    inicio = time.perf_counter()
    try:
        with cliente.open(peticion, timeout=45) as respuesta:
            cuerpo = respuesta.read()
            total = round((time.perf_counter() - inicio) * 1000, 1)
            resolucion = respuesta.headers.get("X-Captura-Resolucion")
        datos = json.loads(cuerpo)
        if not isinstance(datos, dict) or datos.get("decision") not in ESTADOS:
            raise ValueError("La cámara devolvió una respuesta sin decisión.")
        identificador = datos.get("captura_id")
        if not isinstance(identificador, str) or not identificador.strip():
            raise ValueError("La respuesta no contiene el ID de captura.")
        datos["total_ms"] = total
        datos["resolucion"] = resolucion
        return datos
    except HTTPError as error:
        with error:
            try:
                detalle = error.read(500).decode("utf-8", errors="replace")
            except (OSError, http.client.HTTPException):
                detalle = str(error.reason)
        motivo = f"HTTP {error.code}. {detalle}"
    except (OSError, ValueError, http.client.HTTPException) as error:
        motivo = str(error)
    return {"decision": "SIN RESPUESTA", "motivo": motivo,
            "intento_ms": round((time.perf_counter() - inicio) * 1000, 1)}


def guardar_seguro(destino, escribir):
    with tempfile.NamedTemporaryFile(dir=destino.parent, delete=False) as temporal:
        temporal_ruta = Path(temporal.name)
    try:
        escribir(temporal_ruta)
        os.replace(temporal_ruta, destino)
    finally:
        temporal_ruta.unlink(missing_ok=True)


class Registro:
    def __init__(self, ruta, evidencias):
        self.ruta = ruta if ruta.stem.endswith("_capturas") else ruta.with_stem(ruta.stem + "_capturas")
        fuente = self.ruta if self.ruta.is_file() else ruta
        self.evidencias = evidencias
        self.reabrir(fuente)
        self.respaldo = self.ruta.with_suffix(".registros")
        self.respaldo.mkdir(exist_ok=True)
        self.sincronizar()

    def reabrir(self, ruta):
        self.libro = load_workbook(ruta)
        self.hoja = self.libro["Casos"]
        self.columnas = {c.value: c.column for c in self.hoja[1]}
        if not CAMPOS.issubset(self.columnas):
            raise ValueError("La hoja Casos no tiene las columnas de la planilla del proyecto.")
        self.filas = {}
        for fila in range(2, self.hoja.max_row + 1):
            caso = self.valor(fila, "Caso")
            if self.valor(fila, "Entorno") == "Controlado" and isinstance(caso, str) and re.fullmatch(r"C[0-9]{3}", caso):
                if not self.valor(fila, "Matrícula real") or not isinstance(self.valor(fila, "Distancia real (m)"), (int, float)):
                    raise ValueError(f"Faltan la matrícula de referencia o la distancia en {caso}. Selecciona la planilla de luz fija.")
                self.filas[caso] = fila
        if not self.filas:
            raise ValueError("No se encontraron casos controlados en la hoja Casos.")

    def valor(self, fila, campo):
        return self.hoja.cell(fila, self.columnas[campo]).value

    def guardar_excel(self):
        guardar_seguro(self.ruta, self.libro.save)

    def siguiente(self):
        return next((caso for caso, fila in sorted(self.filas.items()) if not self.valor(fila, "Decisión API")), None)

    def iniciar(self, caso):
        fila = self.filas[caso]
        if self.valor(fila, "Captura ID"):
            raise ValueError(f"{caso} ya tiene ID. Completa esa fila con la evidencia existente.")
        ruta = self.respaldo / (caso + ".json")
        if ruta.exists():
            raise ValueError(f"{caso} tiene un disparo previo pendiente. Revisa las evidencias y completa esa fila antes de continuar.")
        self.guardar_excel()
        with ruta.open("x", encoding="utf-8") as archivo:
            json.dump({"estado": "iniciada"}, archivo)

    def registrar(self, caso, datos, nota):
        ruta = self.respaldo / (caso + ".json")
        contenido = {"estado": "completa", "datos": datos, "nota": nota}
        guardar_seguro(ruta, lambda tmp: tmp.write_text(json.dumps(contenido, ensure_ascii=False), encoding="utf-8"))
        return self.sincronizar()

    def sincronizar(self):
        if self.ruta.is_file():
            self.reabrir(self.ruta)
        avisos = []
        cambios = False
        for caso, fila in self.filas.items():
            ruta = self.respaldo / (caso + ".json")
            if not ruta.is_file() or self.valor(fila, "Decisión API"):
                continue
            registro = json.loads(ruta.read_text(encoding="utf-8"))
            if registro["estado"] == "completa":
                avisos.extend(self.llenar(fila, registro["datos"], registro["nota"]))
                cambios = True
        if cambios or not self.ruta.is_file():
            self.guardar_excel()
        return avisos

    def llenar(self, fila, datos, nota):
        decision = datos["decision"]
        placa = datos.get("placa")
        id_captura = datos.get("captura_id")
        motivo = [self.valor(fila, "Nota / motivo"), nota, datos.get("motivo")]
        avisos = []
        if not self.valor(fila, "Movimiento"):
            self.hoja.cell(fila, self.columnas["Movimiento"], "Detenido")
        if decision == "NO CONCLUYENTE" and placa:
            motivo.append(f"Candidata OCR sin aceptar: {placa}.")
        if decision == "SIN RESPUESTA":
            motivo.append(f"Tiempo hasta el fallo: {datos['intento_ms']:.1f} ms.")
        else:
            evidencia = datos.get("evidencia") or {}
            carpeta = evidencia.get("carpeta")
            if (not evidencia.get("completa") or not carpeta or not carpeta.startswith(id_captura + "-")
                or Path(carpeta).name != carpeta
                or not (self.evidencias / carpeta / "diagnostico.json").is_file()
                or not (self.evidencias / carpeta / "original.jpg").is_file()):
                avisos.append("Revisa la evidencia en la carpeta del bloque.")
            if datos.get("resolucion") != "1280x1024":
                avisos.append(f"Resolución recibida: {datos.get('resolucion') or 'sin dato'}. El plan usa 1280x1024.")
        if datos.get("resolucion"):
            motivo.append(f"Resolución: {datos['resolucion']}.")
        valores = {"Captura ID": id_captura, "Decisión API": decision,
                   "Matrícula devuelta": placa if decision in ("REGISTRADA", "NO REGISTRADA") else None,
                   "API (ms)": datos.get("procesamiento_ms"), "Total (ms)": datos.get("total_ms"),
                   "Nota / motivo": " ".join(item for item in [*motivo, *avisos] if item)}
        for campo, valor in valores.items():
            celda = self.hoja.cell(fila, self.columnas[campo])
            if celda.value in ("", None) and valor is not None:
                celda.value = valor
        return avisos


class Aplicacion:
    def __init__(self, ventana, ruta):
        self.ventana = ventana
        self.registro = Registro(ruta, EVIDENCIAS)
        self.en_curso = False
        self.pendiente = None
        self.resultados = Queue()
        ventana.title("Proyecto de placas | Captura guiada")
        ventana.minsize(590, 335)
        marco = ttk.Frame(ventana, padding=20)
        marco.pack(fill="both", expand=True)
        ttk.Label(marco, text="Dirección de CameraWebServer").pack(anchor="w")
        self.ip = ttk.Entry(marco)
        self.ip.pack(fill="x", pady=(4, 16))
        self.ip.focus()
        self.indicacion = ttk.Label(marco, font=("Segoe UI", 16, "bold"), wraplength=540)
        self.indicacion.pack(anchor="w", pady=(0, 12))
        ttk.Label(marco, text="Nota de la toma (opcional)").pack(anchor="w")
        self.nota = ttk.Entry(marco)
        self.nota.pack(fill="x", pady=(4, 10))
        self.boton = ttk.Button(marco, text="Capturar y registrar", command=self.disparar)
        self.boton.pack(anchor="w")
        self.estado = ttk.Label(marco, wraplength=540, justify="left")
        self.estado.pack(anchor="w", pady=(12, 8))
        ttk.Label(marco, text=f"Archivo de resultados\n{self.registro.ruta}", wraplength=540).pack(anchor="w")
        ventana.protocol("WM_DELETE_WINDOW", self.cerrar)
        self.actualizar_caso()

    def actualizar_caso(self):
        caso = self.registro.siguiente()
        if caso is None:
            self.indicacion.configure(text="Los casos controlados ya están registrados.")
            self.boton.state(["disabled"])
            return
        fila = self.registro.filas[caso]
        placa = self.registro.valor(fila, "Matrícula real")
        distancia = self.registro.valor(fila, "Distancia real (m)")
        angulo = self.registro.valor(fila, "Ángulo lateral (°)")
        medida = f"{distancia:.2f}".replace(".", ",")
        self.indicacion.configure(text=f"{caso}  ·  {placa}  ·  {medida} m  ·  {angulo}°")
        self.boton.state(["!disabled"])

    def disparar(self):
        if self.en_curso:
            return
        if self.pendiente:
            self.guardar_pendiente()
            return
        caso = self.registro.siguiente()
        if caso is None:
            return
        try:
            url = ruta_captura(self.ip.get().strip())
            self.registro.iniciar(caso)
        except (OSError, ValueError) as error:
            self.estado.configure(text=str(error))
            return
        nota = self.nota.get().strip()
        self.en_curso = True
        self.boton.state(["disabled"])
        self.estado.configure(text="Capturando y esperando la decisión…")
        Thread(target=self.tomar, args=(caso, url, nota), daemon=True).start()
        self.ventana.after(100, self.recibir)

    def tomar(self, caso, url, nota):
        self.resultados.put((caso, tomar_captura(url), nota))

    def recibir(self):
        try:
            self.pendiente = self.resultados.get_nowait()
        except Empty:
            self.ventana.after(100, self.recibir)
            return
        self.en_curso = False
        self.guardar_pendiente()

    def guardar_pendiente(self):
        caso, datos, nota = self.pendiente
        try:
            avisos = self.registro.registrar(caso, datos, nota)
        except (OSError, ValueError) as error:
            self.estado.configure(text=f"Captura recibida. Cierra el Excel de resultados y pulsa Guardar fila. {error}")
            self.boton.configure(text="Guardar fila")
            self.boton.state(["!disabled"])
            return
        self.pendiente = None
        self.nota.delete(0, tk.END)
        self.boton.configure(text="Capturar y registrar")
        detalles = [caso, datos["decision"]]
        if datos.get("placa") and datos["decision"] in ("REGISTRADA", "NO REGISTRADA"):
            detalles.append(datos["placa"])
        if datos.get("captura_id"):
            detalles.append(datos["captura_id"])
        if datos.get("total_ms") is not None:
            detalles.append(f"{datos['total_ms']:.1f} ms")
        if datos["decision"] == "SIN RESPUESTA":
            avisos.append(datos["motivo"])
        self.estado.configure(text=" · ".join(detalles) + ("\n" + " ".join(avisos) if avisos else ""))
        self.actualizar_caso()

    def cerrar(self):
        if self.en_curso:
            messagebox.showinfo("Captura en curso", "Espera a que termine el intento.")
        elif self.pendiente:
            messagebox.showinfo("Fila pendiente", "Cierra el Excel de resultados y pulsa Guardar fila.")
        else:
            self.ventana.destroy()


def main():
    ventana = tk.Tk()
    ventana.withdraw()
    ruta = filedialog.askopenfilename(
        title="Selecciona tu planilla de pruebas de luz fija",
        initialdir=PROYECTO if PROYECTO.is_dir() else Path.home(),
        filetypes=[("Planilla Excel", "*.xlsx")],
    )
    if not ruta:
        ventana.destroy()
        return
    try:
        Aplicacion(ventana, Path(ruta))
    except (KeyError, OSError, ValueError) as error:
        messagebox.showerror("No se pudo abrir la planilla", str(error))
        ventana.destroy()
        return
    ventana.deiconify()
    ventana.mainloop()


if __name__ == "__main__":
    main()
