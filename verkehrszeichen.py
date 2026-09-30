"""
Verkehrszeichen (Oesterreich) als 3D-Druck - zwei Varianten je Zeichen:
  Stufen-Modell      fuer Einzel-Extruder mit Farbwechsel (z. B. Ender 3 V2)
  Multicolor-Modell  fuer Mehrfarbdrucker (Bambu Studio / OrcaSlicer mit AMS)

Quelle: https://de.wikipedia.org/wiki/Bildtafel_der_Verkehrszeichen_in_Österreich
        (SVG-Dateien von Wikimedia Commons, oesterreichische Verkehrszeichen sind amtliche Werke
        und gemeinfrei)

Stufen-Modell (Vorderseite oben, Rueckseite flach auf dem Druckbett):
  - Grundplatte in der hellsten Farbe (BASIS mm dick)
  - jede weitere Farbe als eigene Stufe, je STUFE mm hoeher, sortiert von hell nach dunkel.
    Dunkle Farben liegen also immer auf helleren -> deckt sauber, nichts scheint durch.
Multicolor-Modell (Vorderseite UNTEN auf dem Druckbett):
  - flache Platte (MC_DICKE mm), alle Farben buendig in einer Ebene als MC_FARBE mm dicke Schicht,
    dahinter der Grundkoerper in der hellsten Farbe. Jede Farbe ist ein eigenes Teil, das beim
    Oeffnen in Bambu Studio / OrcaSlicer bereits einem Filament-Slot zugeordnet ist.
Beide:
  - Rueckseite: Rillen mit dem Umriss der Halterplatte (Teil B aus schildhalter.py)
    + Mittenmarken passend zu den V-Kerben -> einfach einlegen und festkleben.
    Die Halterplatte sitzt im Flaechenschwerpunkt des Schilds (Dreiecke haengen so gerade).

Aufruf:
  python verkehrszeichen.py --liste                  alle Zeichen mit Nummer anzeigen
  python verkehrszeichen.py --liste --suche kinder   Liste filtern
  python verkehrszeichen.py 17 42                    Zeichen Nr. 17 und 42 erzeugen
  python verkehrszeichen.py "Vorschriftszeichen_24"  per Dateiname (oder eindeutigem Textteil)
  python verkehrszeichen.py --alle                   alle Zeichen nacheinander erzeugen
  python verkehrszeichen.py --alle --suche Gefahr    nur die zur Suche passenden
Optionen:
  --groesse 180   max. Breite/Hoehe in mm       --basis 1.2   Dicke Grundplatte (Stufen)
  --stufe 0.6     Hoehe je Farbstufe (Stufen)   --schicht 0.2 Schichthoehe (Hoehen werden gerundet)
  --mc-dicke 2.0  Gesamtdicke (Multicolor)      --mc-farbe 0.6  Dicke der Farbschicht (Multicolor)
  --modell stufen|multicolor|beide              welche Variante(n) erzeugt werden (Standard: beide)
  --reihenfolge weiss,rot,schwarz   Farbreihenfolge von unten nach oben selbst festlegen
  --neu           mit --alle: auch bereits erzeugte Zeichen neu erzeugen (sonst uebersprungen)

Ergebnis je Zeichen in ausgabe/, z. B. 021_Gefahr-6d_Andreaskreuz-liegend-eingleisig_...:
  <Name>_Stufen.stl       Druckdatei Einzel-Extruder (bereits richtig ausgerichtet)
  <Name>_Multicolor.3mf   Druckdatei Mehrfarbdrucker (Farben als Teile, Slots vorbelegt)
  <Name>_anleitung.txt    Farbwechsel-Schichten, Filament-Slots, Halterplatte
  <Name>_vorschau.png     Vorder- und Rueckseite
  Die fuehrende Nummer ist die Nummer aus --liste (eindeutig).
"""
import argparse
import html as htmllib
import json
import os
import re
import sys
import time
import traceback
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
import uuid
import zipfile
from xml.sax.saxutils import escape, quoteattr

import cv2
import numpy as np
import resvg_py
import trimesh
from manifold3d import CrossSection, FillRule, JoinType, Manifold

HERE = os.path.dirname(os.path.abspath(__file__))
CACHE = os.path.join(HERE, "cache")
OUT = os.path.join(HERE, "ausgabe")
UA = {"User-Agent": "Verkehrszeichen3D/1.0 (privates Bastelprojekt; python urllib)"}
DOWNLOAD_PAUSE = 1.0   # Sekunden zwischen Wikimedia-Anfragen (sonst HTTP 429 beim Stapellauf)
WIKI_PAGE = "Bildtafel der Verkehrszeichen in Österreich"

# ---------------- Parameter (mm) ----------------
GROESSE = 190.0      # max. Breite bzw. Hoehe
BASIS = 1.2          # Grundplatte (hellste Farbe); farbige Stufen versteifen zusaetzlich
STUFE = 0.6          # zusaetzliche Hoehe je weiterer Farbe
SCHICHT = 0.2        # Schichthoehe im Slicer
PX_MM = 20           # Renderaufloesung (Pixel je mm) -> 0.05 mm Genauigkeit
MIN_FLAECHE = 0.5    # kleinere Farbinseln (mm^2) werden der Umgebung zugeschlagen
HAARLINIE = 0.25     # schmalere Stellen (mm) werden entfernt: Luecken zwischen Formen in der SVG,
                     # durch die der Hintergrund blitzt - ohnehin nicht druckbar
KANTE_GLATT = 0.04   # Toleranz beim Vereinfachen der Konturen (mm)

MC_DICKE = 3.0       # Multicolor: Gesamtdicke der Platte
MC_FARBE = 0.6       # Multicolor: Dicke der Farbschicht auf der Vorderseite (3 Schichten a 0.2)
MC_SPLITTER = 0.02   # Multicolor: schmalere Reste (x2, mm) zwischen den Farbflaechen werden entfernt
BETT_MITTE = (128.0, 128.0)   # Multicolor: Position auf dem Druckbett (Bambu X1/P1: 256 x 256 mm)

RANDLINIE = 2.0     # farbige Linien (ausser weiss), die nur am Aussenrand liegen, werden entfernt
                    # (Wikipedia zeichnet sie nur zur Abgrenzung; spart einen Farbwechsel)
RILLE_B = 0.8        # Markierungsrillen auf der Rueckseite: Breite
RILLE_T = 0.4        #                                       Tiefe (2 Schichten)
MARKE_L = 8.0        # Laenge der Mittenmarken
RAND_MIN = 2.0       # so viel Abstand muss die Halterplatte zum Schildrand haben

# Masse der Halterplatte (Teil B) - werden aus schildhalter.py uebernommen, falls vorhanden
PLATE_W, PLATE_H, PLATE_R = 64.0, 84.0, 8.0
try:
    sys.path.insert(0, HERE)
    import schildhalter as _sh
    PLATE_W, PLATE_H, PLATE_R = _sh.PLATE_W, _sh.PLATE_H, _sh.PLATE_R
except Exception:
    pass

# Druckfarben: Name -> RGB (nur fuer die Vorschau; die Zuordnung erfolgt ueber den Farbton)
PALETTE = {
    "weiss":  (255, 255, 255),
    "gelb":   (255, 204, 0),
    "orange": (240, 130, 0),
    "grau":   (140, 140, 140),
    "rot":    (200, 25, 35),
    "gruen":  (0, 130, 70),
    "blau":   (0, 80, 160),
    "braun":  (110, 65, 30),
    "schwarz": (0, 0, 0),
}
# Kurzname der Kategorie im Dateinamen (Praefix des Wikimedia-Dateinamens)
KATEGORIE = {"Gefahrenzeichen": "Gefahr", "Vorschriftszeichen": "Vorschrift",
             "Hinweiszeichen": "Hinweis", "Zusatztafel": "Zusatz"}
# ------------------------------------------------


class ZeichenFehler(Exception):
    """Fehler bei einem einzelnen Zeichen (der Stapellauf macht mit dem naechsten weiter)."""


def lp(path):
    """Windows: lange Pfade (> 260 Zeichen) erlauben."""
    path = os.path.abspath(path)
    if os.name == "nt" and not path.startswith("\\\\?\\"):
        return "\\\\?\\" + path
    return path


def http_get(url, versuche=6):
    """Download; wenn Wikimedia bremst (HTTP 429), warten und erneut versuchen."""
    for i in range(versuche):
        try:
            return urllib.request.urlopen(urllib.request.Request(url, headers=UA), timeout=60).read()
        except urllib.error.HTTPError as ex:
            if ex.code != 429 or i == versuche - 1:
                raise
            ra = ex.headers.get("Retry-After", "")
            warte = int(ra) if ra.isdigit() else 15 * 2 ** i
            print(f"  Wikimedia bremst (zu viele Anfragen) - warte {warte} s ...")
            time.sleep(warte)


# ---------------------------------------------------------------- Katalog
def katalog():
    path = os.path.join(CACHE, "katalog.json")
    if os.path.isfile(path):
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    print("Lade Zeichenliste von Wikipedia ...")
    api = "https://de.wikipedia.org/w/api.php?" + urllib.parse.urlencode(
        {"action": "parse", "page": WIKI_PAGE, "prop": "text", "format": "json", "formatversion": "2"})
    page = json.loads(http_get(api))["parse"]["text"]
    eintraege, abschnitt = [], ""
    token = re.compile(r'<h[23][^>]*>(.*?)</h[23]>|<li class="gallerybox".*?</li>', re.S)
    for m in token.finditer(page):
        if m.group(1) is not None:
            abschnitt = htmllib.unescape(re.sub(r"<[^>]+>", "", m.group(1))).strip()
            continue
        box = m.group(0)
        f = re.search(r'href="/wiki/Datei:([^"]+?\.svg)"', box)
        t = re.search(r'<div class="gallerytext">(.*?)</div>', box, re.S)
        if not f:
            continue
        datei = urllib.parse.unquote(htmllib.unescape(f.group(1)))
        text = htmllib.unescape(re.sub(r"<[^>]+>", "", t.group(1))).strip() if t else datei
        w = re.search(r'data-file-width="(\d+)"', box)
        h = re.search(r'data-file-height="(\d+)"', box)
        eintraege.append({"nr": len(eintraege) + 1, "abschnitt": abschnitt, "text": " ".join(text.split()),
                          "datei": datei, "w": int(w.group(1)) if w else 0, "h": int(h.group(1)) if h else 0})
    os.makedirs(CACHE, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(eintraege, f, ensure_ascii=False, indent=1)
    return eintraege


def finde(kat, frage):
    if frage.isdigit():
        n = int(frage)
        return [e for e in kat if e["nr"] == n]
    q = frage.lower().replace(" ", "_")
    exakt = [e for e in kat if e["datei"].lower() in (q, q + ".svg")]
    if exakt:
        return exakt
    return [e for e in kat if frage.lower() in e["text"].lower() or q in e["datei"].lower()]


def passt_suche(e, suche):
    return not suche or suche.lower() in (e["text"] + " " + e["datei"] + " " + e["abschnitt"]).lower()


def slug(s, maxlen=60):
    """Text -> dateinamentauglich: Umlaute umschreiben, Sonderzeichen -> '-', Laenge begrenzen."""
    s = s.replace("\xad", "")                       # weiche Trennstriche aus dem Wiki-Text
    for a, b in (("ä", "ae"), ("ö", "oe"), ("ü", "ue"), ("Ä", "Ae"), ("Ö", "Oe"), ("Ü", "Ue"), ("ß", "ss")):
        s = s.replace(a, b)
    s = unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()
    s = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-")
    if len(s) > maxlen:
        s = s[:maxlen + 1]
        s = s.rsplit("-", 1)[0] if "-" in s else s[:maxlen]
    return s


def dateiname(e):
    """Sprechender, eindeutiger Basisname, z. B. 021_Gefahr-6d_Andreaskreuz-liegend-eingleisig."""
    stamm = os.path.splitext(e["datei"])[0]
    m = re.match(r"\s*([^:]{1,12}):\s*(.+)", e["text"])
    code, bez = (m.group(1), m.group(2)) if m else ("", e["text"])
    kopf = "-".join(x for x in (KATEGORIE.get(stamm.split("_")[0], ""), slug(code)) if x)
    teile = [f"{e['nr']:03d}", kopf, slug(bez) or slug(stamm)]
    if "historisch" in e["abschnitt"].lower():
        teile.append("historisch")
    return "_".join(t for t in teile if t)


def svg_laden(datei):
    d = os.path.join(CACHE, "svg")
    os.makedirs(d, exist_ok=True)
    path = os.path.join(d, datei)
    if not os.path.isfile(lp(path)) or os.path.getsize(lp(path)) == 0:
        api = "https://commons.wikimedia.org/w/api.php?" + urllib.parse.urlencode(
            {"action": "query", "titles": "File:" + datei, "prop": "imageinfo", "iiprop": "url",
             "format": "json", "formatversion": "2"})
        url = json.loads(http_get(api))["query"]["pages"][0]["imageinfo"][0]["url"]
        time.sleep(DOWNLOAD_PAUSE)
        daten = http_get(url)                # erst laden, dann schreiben: kein leerer Cache bei Fehlern
        with open(lp(path), "wb") as f:
            f.write(daten)
        time.sleep(DOWNLOAD_PAUSE)
    return path


# ---------------------------------------------------------------- Bild -> Farbflaechen
def rendern(svg_path, w, h):
    """SVG ohne Kantenglaettung rendern (sonst entstehen Mischfarben an den Kanten)."""
    lang = GROESSE * PX_MM
    kw = {"width": int(lang)} if (w >= h or not h) else {"height": int(lang)}
    with open(lp(svg_path), encoding="utf-8") as f:
        svg = f.read()
    # resvg-py kennt keine Einheiten (mm, cm, ...) in der Groesse des <svg>-Elements -> weglassen
    kopf = re.search(r"<svg\b[^>]*>", svg)
    if kopf:
        neu = re.sub(r'\b(width|height)="([\d.]+)\s*(mm|cm|in|pt|pc)"', r'\1="\2"', kopf.group(0))
        svg = svg[:kopf.start()] + neu + svg[kopf.end():]
    png = resvg_py.svg_to_bytes(svg_string=svg, shape_rendering="crisp_edges",
                                text_rendering="optimize_speed", **kw)
    img = cv2.imdecode(np.frombuffer(bytes(png), np.uint8), cv2.IMREAD_UNCHANGED)
    if img.ndim == 2:
        img = cv2.cvtColor(img, cv2.COLOR_GRAY2BGRA)
    if img.shape[2] == 3:
        img = cv2.cvtColor(img, cv2.COLOR_BGR2BGRA)
    # falls das Bild nicht genau passt: auf max. GROESSE skalieren
    s = lang / max(img.shape[:2])
    if abs(s - 1) > 1e-3:
        img = cv2.resize(img, None, fx=s, fy=s, interpolation=cv2.INTER_NEAREST)
    return img


def farbname(rgb):
    """Farbe ueber Farbton/Saettigung/Helligkeit einer Druckfarbe zuordnen."""
    import colorsys
    r, g, b = (c / 255 for c in rgb)
    h, s, v = colorsys.rgb_to_hsv(r, g, b)
    h *= 360
    if v < 0.2:
        return "schwarz"
    if s < 0.25:
        return "weiss" if v > 0.8 else ("grau" if v > 0.3 else "schwarz")
    if h < 15 or h >= 330:
        return "rot"
    if h < 45:
        return "orange" if v > 0.65 else "braun"
    if h < 70:
        return "gelb"
    if h < 170:
        return "gruen"
    if h < 270:
        return "blau"
    return "rot"


def neu_zuordnen(label, maske):
    """Pixel in maske bekommen die Farbe des naechstgelegenen Pixels ausserhalb der Maske."""
    frei = (~maske & (label >= 0)).astype(np.uint8)
    if not frei.any():
        return
    _, idx = cv2.distanceTransformWithLabels(1 - frei, cv2.DIST_L2, 5, labelType=cv2.DIST_LABEL_PIXEL)
    ys, xs = np.nonzero(frei)
    quelle = np.zeros(idx.max() + 1, np.int16)
    quelle[idx[ys, xs]] = label[ys, xs]
    label[maske] = quelle[idx[maske]]


def farben_zuordnen(img):
    alpha = img[:, :, 3] >= 128
    # Umriss = Aussenkontur (Loecher im Umriss werden gefuellt)
    cnts, _ = cv2.findContours(alpha.astype(np.uint8), cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)
    umriss = np.zeros(alpha.shape, np.uint8)
    cv2.drawContours(umriss, cnts, -1, 1, thickness=cv2.FILLED)
    # Umriss saeubern: duenne Anhaengsel/Splitter (z. B. Reste von Randlinien) entfernen,
    # nur grosse zusammenhaengende Teile behalten
    k = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (int(0.6 * PX_MM) | 1,) * 2)
    umriss = cv2.morphologyEx(umriss, cv2.MORPH_OPEN, k)
    n, comp, stats, _ = cv2.connectedComponentsWithStats(umriss, connectivity=4)
    if n > 1:
        groesste = stats[1:, cv2.CC_STAT_AREA].max()
        behalten = [c for c in range(1, n) if stats[c, cv2.CC_STAT_AREA] > 0.05 * groesste]
        umriss = np.isin(comp, behalten).astype(np.uint8)
        if len(behalten) > 1:
            print(f"  HINWEIS: Schild besteht aus {len(behalten)} getrennten Teilen.")
    umriss = umriss.astype(bool)

    namen = list(PALETTE)
    rgb = cv2.cvtColor(img[:, :, :3], cv2.COLOR_BGR2RGB)
    # Farben einmalig zuordnen (es gibt nur wenige verschiedene)
    flach = rgb[umriss & alpha]
    uniq, inv = np.unique(flach.reshape(-1, 3), axis=0, return_inverse=True)
    lbl_u = np.array([namen.index(farbname(tuple(int(x) for x in u))) for u in uniq])
    label = np.full(alpha.shape, -1, np.int16)
    label[umriss & alpha] = lbl_u[inv.ravel()]
    # transparente Stellen innerhalb des Umrisses -> hellste vorhandene Farbe (wird unten gesetzt)
    label[umriss & ~alpha] = -2

    # Haarlinien entfernen (vor allem anderen - sie verbinden sonst getrennte Flaechen)
    kern = cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (int(HAARLINIE * PX_MM) | 1,) * 2)
    duenn = np.zeros(label.shape, bool)
    for k in np.unique(label[label >= 0]):
        m = (label == k).astype(np.uint8)
        duenn |= (m > 0) & (cv2.morphologyEx(m, cv2.MORPH_OPEN, kern) == 0)
    if duenn.any():
        neu_zuordnen(label, duenn)

    # Randlinien entfernen: Komponenten, die komplett im schmalen Randstreifen liegen
    rand_abst = cv2.distanceTransform(np.pad(umriss, 1).astype(np.uint8), cv2.DIST_L2, 5)[1:-1, 1:-1]
    weg = np.zeros(label.shape, bool)
    for fname in namen:
        if fname == "weiss":
            continue
        k = namen.index(fname)
        n, comp, stats, _ = cv2.connectedComponentsWithStats((label == k).astype(np.uint8), connectivity=8)
        for c in range(1, n):
            x, y, w, h = stats[c, :4]
            sub = comp[y:y + h, x:x + w] == c
            d = rand_abst[y:y + h, x:x + w][sub]
            if d.min() <= 2 and d.max() <= RANDLINIE * PX_MM:
                weg[y:y + h, x:x + w] |= sub
    if weg.any():
        neu_zuordnen(label, weg)

    # kleine Farbinseln der Umgebung zuschlagen
    min_px = MIN_FLAECHE * PX_MM * PX_MM
    for _ in range(2):
        for k in np.unique(label[label >= 0]):
            n, comp, stats, _ = cv2.connectedComponentsWithStats((label == k).astype(np.uint8), connectivity=8)
            for c in range(1, n):
                if stats[c, cv2.CC_STAT_AREA] >= min_px:
                    continue
                x, y, w, h = stats[c, :4]
                x0, y0, x1, y1 = max(x - 2, 0), max(y - 2, 0), x + w + 2, y + h + 2
                sub = comp[y0:y1, x0:x1] == c
                ring = cv2.dilate(sub.astype(np.uint8), np.ones((3, 3), np.uint8)).astype(bool) & ~sub
                nb = label[y0:y1, x0:x1][ring]
                nb = nb[nb >= 0]
                if len(nb):
                    label[y0:y1, x0:x1][sub] = np.bincount(nb).argmax()
    return label, umriss, namen


def zu_crosssection(maske):
    """Binaermaske (Bildkoordinaten) -> CrossSection in mm (y nach oben)."""
    cnts, _ = cv2.findContours(maske.astype(np.uint8), cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
    H = maske.shape[0]
    polys = []
    for c in cnts:
        c = cv2.approxPolyDP(c, KANTE_GLATT * PX_MM, True).reshape(-1, 2).astype(np.float64)
        if len(c) < 3:
            continue
        # Pixelmitte -> mm; Konturen liegen auf Pixelmitten, daher +0.5
        polys.append(np.column_stack([(c[:, 0] + 0.5) / PX_MM, (H - c[:, 1] - 0.5) / PX_MM]))
    if not polys:
        return CrossSection()
    return CrossSection(polys, FillRule.EvenOdd)


def lum(name):
    r, g, b = PALETTE[name]
    return 0.2126 * r + 0.7152 * g + 0.0722 * b


def rund(z):
    return round(z / SCHICHT) * SCHICHT


# ---------------------------------------------------------------- 3D-Modell
def platte_cs(cx, cy, grow=0.0):
    w, h, r = PLATE_W + 2 * grow, PLATE_H + 2 * grow, PLATE_R + grow
    cs = CrossSection.square([w - 2 * r, h - 2 * r]).translate([-(w / 2 - r), -(h / 2 - r)])
    return cs.offset(r, JoinType.Round, circular_segments=64).translate([cx, cy])


def mesh_aufbereiten(koerper):
    """Manifold -> trimesh: auf STL-Genauigkeit (float32) runden, zusammenfallende Punkte/Dreiecke
    bereinigen und winzige Splitter entfernen."""
    mesh = koerper.to_mesh()
    v32 = np.asarray(mesh.vert_properties)[:, :3].astype(np.float32).astype(np.float64)
    t = trimesh.Trimesh(v32, np.asarray(mesh.tri_verts), process=True)
    t.update_faces(t.nondegenerate_faces())
    t.remove_unreferenced_vertices()
    teile = t.split(only_watertight=False)
    if len(teile) > 1:
        with np.errstate(divide="ignore", invalid="ignore"):     # Splitter mit Volumen 0
            t = trimesh.util.concatenate([p for p in teile if abs(p.volume) > 0.01])
    return t


def ausgabe_dateien(e, modell):
    basis = os.path.join(OUT, dateiname(e))
    dateien = []
    if modell in ("stufen", "beide"):
        dateien.append(basis + "_Stufen.stl")
    if modell in ("multicolor", "beide"):
        dateien.append(basis + "_Multicolor.3mf")
    return dateien


def zeichen_bauen(e, reihenfolge=None, modell="beide", fortschritt=""):
    basisname = dateiname(e)
    print(f"\n=== {fortschritt}{e['nr']}: {e['text']}  ({e['datei']})\n    -> {basisname}")
    img = rendern(svg_laden(e["datei"]), e["w"], e["h"])
    label, umriss, namen = farben_zuordnen(img)

    flaeche = {namen[k]: (label == k).sum() / PX_MM ** 2 for k in np.unique(label[label >= 0])}
    vorhanden = [n for n in flaeche if flaeche[n] > 0]
    if reihenfolge:
        fehlt = [n for n in vorhanden if n not in reihenfolge]
        if fehlt:
            raise ZeichenFehler(f"Farbe(n) {fehlt} fehlen in --reihenfolge (vorhanden: {vorhanden})")
        ordnung = [n for n in reihenfolge if n in vorhanden]
    else:
        ordnung = sorted(vorhanden, key=lum, reverse=True)       # hell -> dunkel
    # transparente Innenbereiche bekommen die unterste Farbe
    label[label == -2] = namen.index(ordnung[0])
    flaeche[ordnung[0]] = (label == namen.index(ordnung[0])).sum() / PX_MM ** 2

    umriss_cs = zu_crosssection(umriss)
    H, W = umriss.shape
    breite, hoehe = W / PX_MM, H / PX_MM

    # Halterplatte im Flaechenschwerpunkt
    m = cv2.moments(umriss.astype(np.uint8), binaryImage=True)
    cx = (m["m10"] / m["m00"] + 0.5) / PX_MM
    cy = (H - m["m01"] / m["m00"] - 0.5) / PX_MM
    rille = platte_cs(cx, cy, RILLE_B / 2) - platte_cs(cx, cy, -RILLE_B / 2)
    hb, hh = PLATE_W / 2, PLATE_H / 2
    L, b = MARKE_L, RILLE_B
    marken = (CrossSection.square([b, L]).translate([cx - b / 2, cy + hh - L / 2]) +
              CrossSection.square([b, L]).translate([cx - b / 2, cy - hh - L / 2]) +
              CrossSection.square([L, b]).translate([cx + hb - L / 2, cy - b / 2]) +
              CrossSection.square([L, b]).translate([cx - hb - L / 2, cy - b / 2]))
    ueberstand = 0.0
    if hoehe < PLATE_H + 2 * RAND_MIN:
        # zu niedriges Schild (z. B. Einbahn): Platte oben buendig, ragt unten ueber
        cy = hoehe - RAND_MIN - PLATE_H / 2
        ueberstand = PLATE_H / 2 - cy
    passt = (platte_cs(cx, cy, RAND_MIN) - umriss_cs).area() < 1.0
    if ueberstand > 0:
        print(f"  HINWEIS: Schild ist nur {hoehe:.0f} mm hoch - die Halterplatte ragt unten "
              f"{ueberstand:.0f} mm ueber den Schildrand hinaus.")
    elif not passt:
        print(f"  WARNUNG: Halterplatte ({PLATE_W:.0f} x {PLATE_H:.0f} mm) passt nicht mit "
              f"{RAND_MIN} mm Rand auf dieses Schild.")
    # Rillen auf der Rueckseite (z = 0), fuer beide Modelle gleich
    rillen = ((rille + marken) ^ umriss_cs).extrude(RILLE_T + 0.01).translate([0, 0, -0.01])

    os.makedirs(OUT, exist_ok=True)
    ziel = os.path.join(OUT, basisname)
    zeilen = [f"{e['text']}  ({e['datei']})", f"Nr. {e['nr']} in --liste, Groesse {breite:.0f} x {hoehe:.0f} mm"]
    info = []
    if modell in ("stufen", "beide"):
        z, i = stufen_bauen(ziel, label, namen, ordnung, umriss_cs, rillen, breite, hoehe)
        zeilen += z
        info.append(i)
    if modell in ("multicolor", "beide"):
        z, i = multicolor_bauen(ziel, label, namen, ordnung, umriss_cs, rillen, breite, hoehe, e["text"])
        zeilen += z
        info.append(i)

    zeilen += ["", "=== HALTERPLATTE (beide Modelle)",
               f"Rueckseite: Rille = Umriss der Halterplatte (Teil B), Mitte bei "
               f"x={cx:.1f} / y={cy:.1f} mm vom linken unteren Eck (Flaechenschwerpunkt, Blick auf die Vorderseite).",
               "Teil B mit der offenen Nut nach UNTEN einlegen und verkleben."]
    if ueberstand > 0:
        zeilen += [f"HINWEIS: Das Schild ist niedriger als die Halterplatte. Platte oben buendig "
                   f"(Rillen markieren die Seiten), sie ragt unten {ueberstand:.0f} mm heraus."]
    elif not passt:
        zeilen += ["ACHTUNG: Die Halterplatte passt nicht ganz auf dieses Schild."]
    zeilen += ["", "Flaechen je Farbe:"] + [f"  {n:8s} {flaeche[n] / 100:7.1f} cm2" for n in ordnung]
    with open(lp(ziel + "_anleitung.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(zeilen) + "\n")

    vorschau(ziel + "_vorschau.png", label, umriss, namen, ordnung, rille + marken, e["text"], info)


def stufen_bauen(ziel, label, namen, ordnung, umriss_cs, rillen, breite, hoehe):
    """Farben als Stufen uebereinander (Einzel-Extruder, Farbwechsel per Pause)."""
    basis = rund(BASIS)
    stufe = max(rund(STUFE), SCHICHT)
    koerper = umriss_cs.extrude(basis)
    hoehen = {ordnung[0]: basis}
    for i, farbe in enumerate(ordnung[1:], start=1):
        z = basis + i * stufe
        hoehen[farbe] = z
        # minimal vergroessern und am Umriss schneiden: Kanten am Schildrand fallen dann exakt
        # auf den Umriss (sonst entstehen hauchduenne Splitter)
        cs = zu_crosssection(label == namen.index(farbe)).offset(0.1, JoinType.Miter) ^ umriss_cs
        koerper = koerper + cs.extrude(z)
    koerper = (koerper - rillen).simplify(0.01)

    # Druckdatei: mittig auf dem Bett
    t = mesh_aufbereiten(koerper.translate([-breite / 2, -hoehe / 2, 0]))
    stl = ziel + "_Stufen.stl"
    t.export(lp(stl))
    gramm = t.volume / 1000 * 1.24

    zeilen = ["", f"=== STUFEN-MODELL (Einzel-Extruder mit Farbwechsel): {os.path.basename(stl)}",
              f"Dicke {basis:.1f}-{max(hoehen.values()):.1f} mm, ca. {gramm:.0f} g PLA",
              f"Schichthoehe {SCHICHT} mm (auch die erste Schicht!), Rueckseite liegt auf dem Druckbett.", "",
              f"Start mit Farbe: {ordnung[0].upper()}"]
    for i, farbe in enumerate(ordnung[1:], start=1):
        z = hoehen[ordnung[i - 1]]
        schicht = int(round(z / SCHICHT)) + 1
        zeilen.append(f"Farbwechsel vor Schicht {schicht:>3}  (Z = {z:.1f} mm)  ->  {farbe.upper()}")
    zeilen += ["", "Cura: Erweiterungen > Nachbearbeitung > Skript hinzufuegen > 'Filament Change'",
               "      (oder 'Pause at height'), je Farbwechsel ein Eintrag mit der Schichtnummer."]
    print("  Stufen:     " + "\n              ".join(zeilen[2:3] + zeilen[5:5 + len(ordnung)]))
    print(f"              -> {os.path.relpath(stl, HERE)}  (dicht: {t.is_watertight})")
    return zeilen, "Stufen: " + "  |  ".join(f"{n} bis {hoehen[n]:.1f} mm" for n in ordnung)


def multicolor_bauen(ziel, label, namen, ordnung, umriss_cs, rillen, breite, hoehe, titel):
    """Alle Farben buendig in einer Ebene (Mehrfarbdrucker), Vorderseite unten auf dem Bett."""
    D = rund(MC_DICKE)
    F = max(rund(MC_FARBE), SCHICHT)
    if D < F + RILLE_T + SCHICHT - 1e-6:
        raise ZeichenFehler(f"--mc-dicke {D:.1f} ist zu klein fuer Farbschicht {F:.1f} mm + Rillen {RILLE_T} mm")

    # Farbflaechen ueberschneidungsfrei aufteilen; die Grundfarbe bekommt den Rest des Umrisses
    # (fuellt damit auch Haarspalte zwischen den einzeln vereinfachten Konturen).
    # Hauchduenne Splitter aus den Subtraktionen werden entfernt (sonst nicht-dichte Teile).
    def ohne_splitter(cs):
        return cs.offset(-MC_SPLITTER, JoinType.Miter).offset(MC_SPLITTER, JoinType.Miter) ^ umriss_cs

    flaechen, vergeben = {}, CrossSection()
    for farbe in ordnung[1:]:
        cs = ohne_splitter((zu_crosssection(label == namen.index(farbe)) ^ umriss_cs) - vergeben)
        vergeben = vergeben + cs
        flaechen[farbe] = cs
    flaechen[ordnung[0]] = ohne_splitter(umriss_cs - vergeben)

    # erst mit Vorderseite oben bauen (Rueckseite z=0, Front z=D), dann umdrehen
    teile = []
    for farbe in ordnung:
        k = flaechen[farbe].extrude(F).translate([0, 0, D - F])
        if farbe == ordnung[0]:
            k = (k + umriss_cs.extrude(D - F)) - rillen
        # Drehung um die y-Achse (keine Spiegelung!) -> Vorderseite liegt richtig lesbar auf dem Bett
        k = k.translate([-breite / 2, -hoehe / 2, 0]).rotate([0, 180, 0]).translate([0, 0, D]).simplify(0.01)
        if k.is_empty():
            continue
        t = mesh_aufbereiten(k)
        if len(t.faces) and t.volume > 0.01:
            teile.append((farbe, PALETTE[farbe], t))

    pfad = ziel + "_Multicolor.3mf"
    schreibe_3mf_bambu(pfad, teile, titel)
    gramm = sum(t.volume for _, _, t in teile) / 1000 * 1.24

    zeilen = ["", f"=== MULTICOLOR-MODELL (Bambu Studio / OrcaSlicer, AMS): {os.path.basename(pfad)}",
              f"Dicke {D:.1f} mm, Farbschicht {F:.1f} mm ({int(round(F / SCHICHT))} Schichten a {SCHICHT} mm), "
              f"ca. {gramm:.0f} g PLA",
              "VORDERSEITE liegt auf dem Druckbett (Rillen fuer Teil B oben) - die Oberflaeche der Druckplatte",
              "(glatt / strukturiert) bestimmt das Aussehen der Sichtseite.", "",
              "Filament-Slots (im Slicer bereits zugeordnet, nur passende Farben in die Slots legen):"]
    zeilen += [f"  Slot {i}: {farbe.upper()}" + ("  (Grundkoerper)" if i == 1 else "")
               for i, (farbe, _, _) in enumerate(teile, start=1)]
    if len(teile) > 4:
        zeilen.append(f"HINWEIS: {len(teile)} Farben - mehr als ein AMS (4 Slots) fasst.")
        print(f"  HINWEIS: {len(teile)} Farben - mehr als ein AMS (4 Slots) fasst.")
    slots = "  ".join(f"{i}={farbe}" for i, (farbe, _, _) in enumerate(teile, start=1))
    print(f"  Multicolor: {D:.1f} mm, Slots {slots}\n              -> {os.path.relpath(pfad, HERE)}  "
          f"(dicht: {all(t.is_watertight for _, _, t in teile)})")
    return zeilen, "Multicolor-Slots: " + slots


# ---------------------------------------------------------------- 3MF (Bambu Studio / OrcaSlicer)
NS_3MF = ('xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
          'xmlns:BambuStudio="http://schemas.bambulab.com/package/2021" '
          'xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06" requiredextensions="p"')
REL_3MF = "http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"
EINHEIT = "1 0 0 0 1 0 0 0 1 0 0 0"


def _mesh_xml(t):
    v = "".join(f'<vertex x="{x:.4f}" y="{y:.4f}" z="{z:.4f}"/>' for x, y, z in np.asarray(t.vertices))
    f = "".join(f'<triangle v1="{a}" v2="{b}" v3="{c}"/>' for a, b, c in np.asarray(t.faces))
    return f"<mesh><vertices>{v}</vertices><triangles>{f}</triangles></mesh>"


def schreibe_3mf_bambu(pfad, teile, titel):
    """3MF im Projektformat von Bambu Studio / OrcaSlicer: ein Objekt, je Farbe ein Teil,
    Teil i ist Filament-Slot i zugeordnet. teile = [(farbname, rgb, trimesh), ...]"""
    n = len(teile)
    oid = n + 1
    uid = lambda: str(uuid.uuid4())
    kopf = '<?xml version="1.0" encoding="UTF-8"?>\n'

    objekte = "".join(f'<object id="{i}" p:UUID="{uid()}" type="model">{_mesh_xml(t)}</object>\n'
                      for i, (_, _, t) in enumerate(teile, start=1))
    teil_model = (f'{kopf}<model unit="millimeter" xml:lang="en-US" {NS_3MF}>\n'
                  f'<metadata name="BambuStudio:3mfVersion">1</metadata>\n'
                  f'<resources>\n{objekte}</resources>\n<build/>\n</model>\n')

    komponenten = "".join(f'<component p:path="/3D/Objects/object_1.model" objectid="{i}" '
                          f'p:UUID="{uid()}" transform="{EINHEIT}"/>\n' for i in range(1, n + 1))
    bx, by = BETT_MITTE
    haupt_model = (f'{kopf}<model unit="millimeter" xml:lang="en-US" {NS_3MF}>\n'
                   # Orca/Bambu werten Dateien mit dieser Kennung als ihr Projektformat aus (Teile + Slots);
                   # Version 2.x, da neuere Versionen 1.x-Dateien teils als veraltet ablehnen
                   f'<metadata name="Application">BambuStudio-02.00.00.00</metadata>\n'
                   f'<metadata name="BambuStudio:3mfVersion">1</metadata>\n'
                   f'<metadata name="Title">{escape(titel)}</metadata>\n'
                   f'<resources>\n<object id="{oid}" p:UUID="{uid()}" type="model">\n'
                   f'<components>\n{komponenten}</components>\n</object>\n</resources>\n'
                   f'<build p:UUID="{uid()}">\n<item objectid="{oid}" p:UUID="{uid()}" '
                   f'transform="1 0 0 0 1 0 0 0 1 {bx:g} {by:g} 0" printable="1"/>\n</build>\n</model>\n')

    parts = "".join(f'  <part id="{i}" subtype="normal_part">\n'
                    f'    <metadata key="name" value={quoteattr(farbe)}/>\n'
                    f'    <metadata key="matrix" value="1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"/>\n'
                    f'    <metadata key="extruder" value="{i}"/>\n  </part>\n'
                    for i, (farbe, _, _) in enumerate(teile, start=1))
    settings = (f'{kopf}<config>\n <object id="{oid}">\n'
                f'  <metadata key="name" value={quoteattr(titel)}/>\n'
                f'  <metadata key="extruder" value="1"/>\n{parts} </object>\n'
                f' <plate>\n  <metadata key="plater_id" value="1"/>\n  <metadata key="plater_name" value=""/>\n'
                f'  <metadata key="locked" value="false"/>\n  <model_instance>\n'
                f'   <metadata key="object_id" value="{oid}"/>\n   <metadata key="instance_id" value="0"/>\n'
                f'   <metadata key="identify_id" value="1"/>\n  </model_instance>\n </plate>\n</config>\n')

    content_types = (f'{kopf}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">\n'
                     '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>\n'
                     '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>\n'
                     '<Default Extension="config" ContentType="text/xml"/>\n</Types>\n')
    rels = lambda ziel: (f'{kopf}<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\n'
                         f'<Relationship Target="{ziel}" Id="rel-1" Type="{REL_3MF}"/>\n</Relationships>\n')

    with zipfile.ZipFile(lp(pfad), "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("[Content_Types].xml", content_types)
        z.writestr("_rels/.rels", rels("/3D/3dmodel.model"))
        z.writestr("3D/3dmodel.model", haupt_model)
        z.writestr("3D/_rels/3dmodel.model.rels", rels("/3D/Objects/object_1.model"))
        z.writestr("3D/Objects/object_1.model", teil_model)
        z.writestr("Metadata/model_settings.config", settings)


def vorschau(path, label, umriss, namen, ordnung, rillen_cs, titel, info):
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    k = max(1, label.shape[1] // 900)
    lb = label[::k, ::k]
    rgb = np.full(lb.shape + (3,), 235, np.uint8)
    for i, n in enumerate(namen):
        rgb[lb == i] = PALETTE[n]
    rueck = np.where(umriss[::k, ::k, None], np.uint8(PALETTE[ordnung[0]]), np.uint8(235)).astype(np.uint8)
    H, W = label.shape
    ext = [0, W / PX_MM, 0, H / PX_MM]
    fig, ax = plt.subplots(1, 2, figsize=(11, 5.8), dpi=100)
    ax[0].imshow(rgb, extent=ext)
    ax[0].set_title("Vorderseite (gedruckte Farben)")
    ax[1].imshow(rueck[:, ::-1], extent=ext)
    from matplotlib.path import Path
    from matplotlib.patches import PathPatch
    pfade = []
    for p in rillen_cs.to_polygons():
        p = np.asarray(p)
        p = np.vstack([p, p[:1]])            # closed=True erwartet den Startpunkt am Ende
        pfade.append(Path(np.column_stack([ext[1] - p[:, 0], p[:, 1]]), closed=True))
    ax[1].add_patch(PathPatch(Path.make_compound_path(*pfade), color="#333", lw=0))
    ax[1].set_title("Rückseite (Rillen für Teil B)")
    for a in ax:
        a.set_xlabel("mm")
    fig.suptitle("\n".join([titel] + info), fontsize=10)
    plt.tight_layout()
    plt.savefig(lp(path))
    plt.close(fig)


# ---------------------------------------------------------------- main
def main():
    global GROESSE, BASIS, STUFE, SCHICHT, MC_DICKE, MC_FARBE
    ap = argparse.ArgumentParser(description="Oesterreichische Verkehrszeichen als 3D-Druck "
                                             "(Stufen fuer Farbwechsel + Multicolor-3MF)")
    ap.add_argument("zeichen", nargs="*", help="Nummer aus --liste, Dateiname oder eindeutiger Textteil")
    ap.add_argument("--liste", action="store_true", help="Zeichen auflisten")
    ap.add_argument("--suche", default="", help="Liste filtern (auch fuer --alle)")
    ap.add_argument("--alle", action="store_true", help="alle Zeichen nacheinander erzeugen (mit --suche filterbar)")
    ap.add_argument("--neu", action="store_true", help="mit --alle: bereits erzeugte Zeichen nicht ueberspringen")
    ap.add_argument("--modell", choices=["stufen", "multicolor", "beide"], default="beide")
    ap.add_argument("--groesse", type=float, default=GROESSE)
    ap.add_argument("--basis", type=float, default=BASIS)
    ap.add_argument("--stufe", type=float, default=STUFE)
    ap.add_argument("--schicht", type=float, default=SCHICHT)
    ap.add_argument("--mc-dicke", type=float, default=MC_DICKE, help="Multicolor: Gesamtdicke")
    ap.add_argument("--mc-farbe", type=float, default=MC_FARBE, help="Multicolor: Dicke der Farbschicht")
    ap.add_argument("--reihenfolge", default="", help="z. B. weiss,rot,schwarz (unten -> oben)")
    ap.add_argument("--neu-laden", action="store_true", help="Zeichenliste neu von Wikipedia holen")
    a = ap.parse_args()
    GROESSE, BASIS, STUFE, SCHICHT = a.groesse, a.basis, a.stufe, a.schicht
    MC_DICKE, MC_FARBE = a.mc_dicke, a.mc_farbe
    if a.alle and a.zeichen:
        ap.error("--alle nicht zusammen mit einzelnen Zeichen verwenden (Auswahl ueber --suche)")

    if a.neu_laden and os.path.isfile(os.path.join(CACHE, "katalog.json")):
        os.remove(os.path.join(CACHE, "katalog.json"))
    kat = katalog()

    if a.liste or not (a.zeichen or a.alle):
        abschnitt = None
        for e in kat:
            if not passt_suche(e, a.suche):
                continue
            if e["abschnitt"] != abschnitt:
                abschnitt = e["abschnitt"]
                print(f"\n--- {abschnitt}")
            print(f"{e['nr']:4d}  {e['text']}")
        if not (a.zeichen or a.alle):
            print("\nZeichen erzeugen:  python verkehrszeichen.py <Nummer> [<Nummer> ...]"
                  "\n       alle:       python verkehrszeichen.py --alle [--suche <Text>]")
        return

    if a.alle:
        auswahl = [e for e in kat if passt_suche(e, a.suche)]
    else:
        auswahl = []
        for q in a.zeichen:
            treffer = finde(kat, q)
            if len(treffer) != 1:
                print(f"\n'{q}': {len(treffer)} Treffer - bitte Nummer verwenden:")
                for e in treffer[:30]:
                    print(f"{e['nr']:4d}  {e['text']}  [{e['abschnitt']}]")
                continue
            auswahl.append(treffer[0])

    reihenfolge = [s.strip() for s in a.reihenfolge.split(",") if s.strip()] or None
    erzeugt, uebersprungen, fehler = [], [], []
    for i, e in enumerate(auswahl, start=1):
        fortschritt = f"[{i}/{len(auswahl)}] " if len(auswahl) > 1 else ""
        if a.alle and not a.neu and all(os.path.isfile(lp(p)) for p in ausgabe_dateien(e, a.modell)):
            print(f"{fortschritt}{e['nr']}: schon vorhanden - uebersprungen (--neu erzwingt Neuerzeugung)")
            uebersprungen.append(e)
            continue
        try:
            zeichen_bauen(e, reihenfolge, a.modell, fortschritt)
            erzeugt.append(e)
        except KeyboardInterrupt:
            raise
        except Exception as ex:
            if not isinstance(ex, ZeichenFehler):
                traceback.print_exc()
            print(f"  FEHLER bei Nr. {e['nr']}: {ex}")
            fehler.append((e, ex))

    if len(auswahl) > 1:
        print(f"\n=== Fertig: {len(erzeugt)} erzeugt, {len(uebersprungen)} uebersprungen, {len(fehler)} fehlgeschlagen")
        for e, ex in fehler:
            print(f"  {e['nr']:4d}  {e['text']}: {ex}")
    if fehler:
        sys.exit(1)


if __name__ == "__main__":
    main()
