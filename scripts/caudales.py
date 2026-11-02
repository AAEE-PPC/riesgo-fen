"""Genera fen/caudales_hoy.json para el dashboard Riesgo FEN con datos de SENAMHI (plataforma Phisis).

Para cada estación del corredor:
  1. Estado actual desde el GeoServer público (WFS g_dbphisis:estaciones_monitoreo): hora de la última lectura y código de alerta.
  2. Serie horaria desde la página del hidrograma (include/mnt-grafica-new.php), que trae incrustado
     `var data = {...}` con: dato (serie [ms, valor]), amarillo, naranja y rojo (umbrales), variable y unidad.
     Se prueba primero por GET (como lo abre el mapa) y, si no trae datos, por POST (como el formulario Horario/Diario).
  3. Opcional: vista diaria (btnTipo=D) para el promedio histórico del día y calcular la anomalía en %.

Sin dependencias externas. Tolerante a fallos: una estación que falla se omite.
Uso: python scripts/caudales.py
"""
import json, re, sys, time, signal, datetime, urllib.request, urllib.parse, urllib.error, uuid
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
LIMA = ZoneInfo("America/Lima")
WFS = "https://idesep.senamhi.gob.pe/geoserver/g_dbphisis/ows"
GRAF = "https://www.senamhi.gob.pe/mapas/mapa-monitoreohidro/include/mnt-grafica-new.php"
HEAD = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) dashboard-RiesgoFEN",
        "Referer": "https://www.senamhi.gob.pe/mapas/mapa-monitoreohidro/mapa-monitoreo.php"}
EST = json.loads((ROOT / "scripts" / "estaciones_senamhi.json").read_text(encoding="utf-8"))
ALERTA_COD = {0: "", 1: "amarillo", 2: "naranja", 3: "rojo"}   # campo 'nivel' de estaciones_monitoreo (supuesto)
FALLOS_MAX = 3          # si las 3 primeras estaciones no responden, se deja de consultar el hidrograma
LIMITE_SEG = 8 * 60     # tiempo máximo total del script


class Tope(Exception):
    pass


def _alarma(signum, frame):
    raise Tope("sin respuesta completa en el tiempo máximo")


def http(url, data=None, headers=None, timeout=20, tope=30):
    """Petición con dos límites: `timeout` por operación de red y `tope` segundos en total (aunque el servidor responda a cuentagotas)."""
    h = dict(HEAD, **(headers or {}))
    req = urllib.request.Request(url, data=data, headers=h, method="POST" if data else "GET")
    signal.signal(signal.SIGALRM, _alarma)
    signal.alarm(tope)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read().decode("utf-8", "replace")
    finally:
        signal.alarm(0)


def wfs_estado():
    q = dict(service="WFS", version="1.0.0", request="GetFeature", typeName="g_dbphisis:estaciones_monitoreo",
             outputFormat="json", cql_filter="estado='1'", maxFeatures=5000)
    js = json.loads(http(WFS + "?" + urllib.parse.urlencode(q)))
    return {str(f.get("id", "")).split(".")[-1]: f.get("properties", {}) for f in js.get("features", [])}


def extraer_data(html):
    """Devuelve el objeto `var data = {...}` incrustado en la página del hidrograma, o None."""
    m = re.search(r"var\s+data\s*=\s*(\{.*?\})\s*;?\s*\n", html, re.S)
    if not m:
        return None
    try:
        d = json.loads(m.group(1))
        return d if d.get("dato") else None
    except json.JSONDecodeError:
        return None


def fecha_lima(fh):
    """'2026-10-28T12:00:00' (UTC, como viene del WFS) -> '2026-10-28 07:00:00' (Lima)."""
    try:
        dt = datetime.datetime.fromisoformat(str(fh).replace("Z", "")).replace(tzinfo=datetime.timezone.utc)
        return dt.astimezone(LIMA).strftime("%Y-%m-%d %H:00:00")
    except Exception:
        return datetime.datetime.now(LIMA).strftime("%Y-%m-%d %H:00:00")


def multipart(campos):
    b = "----fen" + uuid.uuid4().hex
    body = "".join(f"--{b}\r\nContent-Disposition: form-data; name=\"{k}\"\r\n\r\n{v}\r\n" for k, v in campos.items()) + f"--{b}--\r\n"
    return body.encode("utf-8"), {"Content-Type": f"multipart/form-data; boundary={b}"}


def hidrograma(e, props, tipo="H"):
    """Serie del hidrograma (tipo H horario / D diario) para la estación e."""
    ficha = dict(e.get("ficha", {}))
    ficha.update({k: v for k, v in props.items() if v is not None})
    ficha["id"] = "estaciones_monitoreo." + e["cod"]
    var = (props.get("variable") or e["variable"] or "C")[:1]
    # 1) GET, igual que el mapa (querystring con todos los campos de la ficha)
    q = dict(ficha, variable_opcion=var, btnTipo=tipo)
    try:
        d = extraer_data(http(GRAF + "?" + urllib.parse.urlencode(q)))
        if d: return d
        print("   GET sin datos")
    except Exception as ex:
        print("   GET falló:", type(ex).__name__, ex)
    # 2) POST, igual que el formulario de la página (botones Horario/Diario)
    body, h = multipart({"fecha_hora": fecha_lima(ficha.get("fecha_hora")), "btnTipo": tipo,
                         "variable_opcion": var, "id": ficha["id"]})
    try:
        return extraer_data(http(GRAF, data=body, headers=h))
    except Exception as ex:
        print("   POST falló:", type(ex).__name__, ex)
    return None


def ultimo(serie):
    serie = [p for p in (serie or []) if p and p[1] is not None]
    return serie[-1] if serie else None


def main():
    print("Consultando GeoServer SENAMHI (estado de estaciones)…", flush=True)
    try:
        estado = wfs_estado()
        print(f"   {len(estado)} estaciones activas", flush=True)
    except Exception as ex:
        print("   WFS no disponible:", type(ex).__name__, ex, flush=True); estado = {}
    salida = []
    t0 = time.time(); fallos = 0; consultar = True
    for e in EST:
        props = estado.get(e["cod"], {})
        print("·", e["est"], flush=True)
        if consultar and time.time() - t0 > LIMITE_SEG:
            print("   límite de tiempo alcanzado: se omite el hidrograma del resto"); consultar = False
        h = hidrograma(e, props, "H") if consultar else None
        if consultar and not h:
            fallos += 1
            if fallos >= FALLOS_MAX and not any(s.get("caudal_m3s") is not None or s.get("nivel_m") is not None for s in salida):
                print(f"   {fallos} estaciones seguidas sin respuesta: se deja de consultar el hidrograma"); consultar = False
        reg = dict(rio=e["rio"], est=e["est"].title(), cod=e["cod"],
                   hora=props.get("fecha_hora") or e["ficha"].get("fecha_hora"),
                   nivel_m=None, caudal_m3s=None, anomalia_pct=None, condicion="",
                   alerta=ALERTA_COD.get(int(float(props.get("nivel") or 0)), ""),
                   umbrales=None, url="https://www.senamhi.gob.pe/?p=monitoreo-hidrologico")
        if h:
            u = ultimo(h.get("dato"))
            if u:
                ts, valor = u
                reg["hora"] = datetime.datetime.fromtimestamp(ts / 1000, datetime.timezone.utc).astimezone(LIMA).isoformat(timespec="minutes")
                valor = round(float(valor), 3)
                if str(h.get("variable", "")).upper().startswith("CAUDAL"): reg["caudal_m3s"] = valor
                else: reg["nivel_m"] = valor
                um = {k: (ultimo(h.get(k)) or [None, None])[1] for k in ("amarillo", "naranja", "rojo")}
                reg["umbrales"] = um
                if um["rojo"] is not None and valor >= um["rojo"]: reg["alerta"] = "rojo"
                elif um["naranja"] is not None and valor >= um["naranja"]: reg["alerta"] = "naranja"
                elif um["amarillo"] is not None and valor >= um["amarillo"]: reg["alerta"] = "amarillo"
                elif um["amarillo"] is not None: reg["alerta"] = "verde"
                reg["pct_umbral_amarillo"] = round(100 * valor / um["amarillo"], 1) if um["amarillo"] else None
                reg["unidad"] = h.get("unidad", "").replace("\\/", "/")
            # anomalía respecto al promedio histórico (vista diaria)
            try:
                d = hidrograma(e, props, "D")
                hist, act = ultimo((d or {}).get("promHistorico")), ultimo((d or {}).get("datoActual"))
                if hist and act and hist[1]:
                    a = round(100 * (float(act[1]) - float(hist[1])) / float(hist[1]), 1)
                    reg["anomalia_pct"] = a
                    reg["condicion"] = ("Significativamente sobre lo normal" if a >= 50 else "Ligeramente sobre lo normal" if a >= 15
                                        else "Significativamente por debajo de lo normal" if a <= -50 else "Ligeramente por debajo de lo normal" if a <= -15 else "Normal")
            except Exception as ex:
                print("   diario:", ex)
            print("   ok:", reg["caudal_m3s"] or reg["nivel_m"], reg.get("unidad", ""), "alerta:", reg["alerta"] or "-", "anomalía:", reg["anomalia_pct"], flush=True)
        else:
            print("   sin serie (solo estado WFS)")
        salida.append(reg)
        if consultar: time.sleep(1)   # cortesía con el servidor
    out = dict(fecha=datetime.datetime.now(LIMA).strftime("%Y-%m-%d"),
               generado=datetime.datetime.now(LIMA).isoformat(timespec="minutes"),
               fuente="SENAMHI (monitoreo hidrológico, plataforma Phisis) · datos en tiempo casi real, sin control de calidad",
               estaciones=salida)
    (ROOT / "fen").mkdir(exist_ok=True)
    (ROOT / "fen" / "caudales_hoy.json").write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    n = sum(1 for s in salida if s["caudal_m3s"] is not None or s["nivel_m"] is not None)
    print(f"{n} de {len(salida)} estaciones con valor")
    return 0


if __name__ == "__main__":
    sys.exit(main())
