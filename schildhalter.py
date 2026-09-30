"""
Schildhalter fuer 3D-gedruckte Verkehrszeichen (~180 x 180 mm) auf Rundholz 10 mm.

Teil A  "Stangenkappe": wird oben auf den Rundstab geklebt (Sackloch), traegt eine
        senkrechte Schwalbenschwanz-Schiene.
Teil B  "Schildplatte": wird auf die Rueckseite des Schilds geklebt, hat eine
        Schwalbenschwanz-Nut, unten offen, oben geschlossen (Anschlag).
        Schild von oben auf die Stange schieben -> haelt durch Schwerkraft,
        kann sich nicht verdrehen, laesst sich jederzeit wieder abziehen.

Koordinaten (zusammengebaut): z = senkrecht (Stabachse), y = Richtung Schild, x = seitlich.

Aufruf:  python schildhalter.py      -> erzeugt STL-Dateien in Druckorientierung.

Beschriftung (optional, alles auch oben bei den Parametern einstellbar):
  python schildhalter.py --text-a "STOP"                    Rohr-Rueckseite von Teil A
  python schildhalter.py --text-b-rechts "Lena" --text-b-links "2026"
                                                            Streifen neben der Nut von Teil B
  --modus gravur|erhaben    vertieft (Standard) oder erhaben
  --schrift "Arial"         Schriftname oder Pfad zu einer .ttf/.otf-Datei
  --tiefe 0.8               Gravurtiefe bzw. Hoehe der erhabenen Schrift in mm
  Der Text wird von unten nach oben lesbar gesetzt (wie auf einem Buchruecken) und
  automatisch verkleinert, wenn er zu lang ist.
"""
import argparse
import math
import os
import numpy as np
from manifold3d import Manifold, CrossSection, JoinType, FillRule
from matplotlib.textpath import TextPath
from matplotlib.font_manager import FontProperties
import trimesh

# ---------------- Parameter (alles in mm) ----------------
ROD_D        = 10.5   # Durchmesser Rundholz
BORE_CLEAR   = 0.1    # Spiel Bohrung (Durchmesser) -> Platz fuer Kleber
WALL         = 4.0    # Wandstaerke Kappe
A_H          = 95.0   # Gesamthoehe Teil A (Rohr um den Stab)
RAIL_L       = 65.0   # Laenge der Schiene (oberer Teil von A)
RAMP_L       = 15.0   # Schraege Auslauf unter der Schiene (keine Kerbe/Sollbruchstelle)
CAP_T        = 5.0    # Deckel oben auf Teil A (Regen bleibt draussen)
VENT_D       = 1.5    # Entlueftungsloch im Deckel (Luft/Kleber kann entweichen)

NECK_W       = 16.0   # schmale Breite Schwalbenschwanz
DT_H         = 6.0    # Hoehe Schwalbenschwanz-Kopf
DT_ANGLE     = 30.0   # Flankenwinkel gegen die Senkrechte (<=30 druckt ohne Support)
NECK_GAP     = 1.5    # Abstand Rohr -> Schwalbenschwanz

FIT_CLEAR    = 0.3    # Spiel Schiene/Nut pro Seite (0.2 = stramm, 0.4 = locker)
STOP_T       = 6.0    # Anschlag oben in Teil B
BASE_T       = 3.0    # Bodenstaerke unter der Nut (Klebeflaeche)
BOSS_W       = 36.0   # Breite der Nut-Leiste
PLATE_W      = 64.0   # Klebeplatte Breite
PLATE_H      = 84.0   # Klebeplatte Hoehe
PLATE_BELOW  = 6.0    # Platte ragt so weit unter den Nuteingang
PLATE_R      = 8.0    # Eckenradius
ROOT_R       = 2.0    # Hohlkehle zwischen Rohr und Schienenhals
LEAD_IN      = 1.2    # Einfuehrschraege am Nuteingang

# Beschriftung ("" = keine)
TEXT_A        = ""        # Teil A, Rohr-Rueckseite (sichtbar von hinten), laeuft senkrecht
TEXT_B_LINKS  = ""        # Teil B, Streifen links neben der Nut (von hinten gesehen)
TEXT_B_RECHTS = ""        # Teil B, Streifen rechts neben der Nut
TEXT_MODUS    = "gravur"  # "gravur" (vertieft) oder "erhaben"
TEXT_TIEFE    = 0.8       # mm Tiefe bzw. Hoehe
TEXT_SCHRIFT  = "DejaVu Sans"   # Schriftname oder Pfad zu .ttf/.otf
TEXT_FETT     = True
TEXT_A_HOEHE  = 9.0       # max. Buchstabenhoehe auf Teil A (mm, gemessen auf der Rundung)
TEXT_B_HOEHE  = 8.0       # max. Buchstabenhoehe auf Teil B
TEXT_MIN      = 4.0       # darunter wird gewarnt (zu klein zum sauberen Drucken)

SEG = 96
# ---------------------------------------------------------

R_TUBE = (ROD_D + BORE_CLEAR) / 2 + WALL
Y0 = R_TUBE + NECK_GAP                       # Beginn Schwalbenschwanz-Kopf
WIDE_W = NECK_W + 2 * DT_H * math.tan(math.radians(DT_ANGLE))
FLOOR_Y = Y0 + DT_H + FIT_CLEAR              # Nutgrund = Oberseite Bodenplatte
GLUE_Y = FLOOR_Y + BASE_T                    # Klebeflaeche am Schild
Z_B = A_H - RAIL_L                           # Unterkante Nut (Teil B) im Montage-KS


def rail_profile():
    neck = CrossSection.square([NECK_W, Y0 + 0.01]).translate([-NECK_W / 2, 0])
    trap = CrossSection([[[-NECK_W / 2, Y0], [NECK_W / 2, Y0],
                          [WIDE_W / 2, Y0 + DT_H], [-WIDE_W / 2, Y0 + DT_H]]])
    return neck + trap


def text_cs(text, max_h, max_len, label=""):
    """Text als 2D-Flaeche, Mitte im Ursprung, Leserichtung +x, auf max_h x max_len skaliert."""
    if os.path.isfile(TEXT_SCHRIFT):
        prop = FontProperties(fname=TEXT_SCHRIFT)
    else:
        prop = FontProperties(family=TEXT_SCHRIFT, weight="bold" if TEXT_FETT else "normal")
    tp = TextPath((0, 0), text, size=10, prop=prop)
    polys = [np.asarray(p) for p in tp.to_polygons(closed_only=True) if len(p) >= 3]
    cs = CrossSection(polys, FillRule.NonZero)
    x0, y0, x1, y1 = cs.bounds()
    k = min(max_h / (y1 - y0), max_len / (x1 - x0))
    if (y1 - y0) * k < TEXT_MIN:
        print(f"WARNUNG: Text '{text}' ({label}) ist nur {(y1 - y0) * k:.1f} mm hoch -> kuerzer fassen "
              f"oder --modus erhaben / kleinere Duese verwenden.")
    return cs.translate([-(x0 + x1) / 2, -(y0 + y1) / 2]).scale([k, k])


def label_a():
    """Text auf der Rohr-Rueckseite (-y), um die Rundung gebogen, Leserichtung nach oben."""
    if not TEXT_A:
        return None, None
    margin = 4.0
    length = A_H - 2 * margin
    cs = text_cs(TEXT_A, TEXT_A_HOEHE, length, "Teil A").rotate(90)   # Leserichtung -> +b
    cs = cs.translate([0, A_H / 2])
    if TEXT_MODUS == "erhaben":
        r0, t = R_TUBE - 0.3, TEXT_TIEFE + 0.3
    else:
        r0, t = R_TUBE - TEXT_TIEFE, TEXT_TIEFE + 0.3
    # (a, b, e) -> (X=a, Y=-e, Z=b); danach aufwickeln: r = r0 - Y, Winkel = X / R_TUBE
    flat = cs.extrude(t).transform([[1, 0, 0, 0], [0, 0, -1, 0], [0, 1, 0, 0]]).refine_to_length(0.4)

    def wrap(v):
        th = v[:, 0] / R_TUBE
        r = r0 - v[:, 1]
        return np.column_stack([r * np.sin(th), -r * np.cos(th), v[:, 2]])
    return flat.warp_batch(wrap), TEXT_MODUS


def label_b():
    """Texte auf den Plattenstreifen links/rechts der Nut (Teil-B-Koordinaten, Nut-Unterkante z=0)."""
    strip_w = (PLATE_W - BOSS_W) / 2
    xc = BOSS_W / 2 + strip_w / 2
    z_lo = -PLATE_BELOW + 3
    z_hi = PLATE_H - PLATE_BELOW - 3
    h = min(TEXT_B_HOEHE, strip_w - 5)
    parts = []
    # von hinten (Stangenseite) gesehen ist +x rechts
    for text, sx, name in ((TEXT_B_RECHTS, 1, "Teil B rechts"), (TEXT_B_LINKS, -1, "Teil B links")):
        if text:
            cs = text_cs(text, h, z_hi - z_lo, name).rotate(90)
            parts.append(cs.translate([sx * xc, (z_lo + z_hi) / 2]))
    if not parts:
        return None
    cs = parts[0]
    for p in parts[1:]:
        cs = cs + p
    if TEXT_MODUS == "erhaben":
        return plate_frame(cs.extrude(TEXT_TIEFE + 0.01), FLOOR_Y - TEXT_TIEFE)
    return plate_frame(cs.extrude(TEXT_TIEFE + 0.01), FLOOR_Y - 0.01)


def part_a():
    # Rohr + durchgehende Rippe (Hals) mit Hohlkehle; Closing (+r / -r) rundet die Innenecken aus
    root = (CrossSection.circle(R_TUBE, SEG) +
            CrossSection.square([NECK_W - 1, Y0 - 1]).translate([-(NECK_W - 1) / 2, 0]))
    root = root.offset(ROOT_R, JoinType.Round, circular_segments=48).offset(-ROOT_R, JoinType.Round, circular_segments=48)
    tube = root.extrude(A_H)
    # Schwalbenschwanz-Kopf (inkl. 2 mm Hals, ueberlappt mit der Rippe)
    w, W = NECK_W / 2, WIDE_W / 2
    head = CrossSection([[[-w, Y0 - 2], [w, Y0 - 2], [w, Y0], [W, Y0 + DT_H], [-W, Y0 + DT_H], [-w, Y0]]])
    rail = head.extrude(RAIL_L).translate([0, 0, Z_B])
    # Auslauf: Schiene laeuft schraeg in die Rippe aus statt abrupt aufzuhoeren (keine Kerbwirkung)
    ramp_top = head.extrude(1.0).translate([0, 0, Z_B])
    ramp_bot = CrossSection.square([NECK_W - 2, Y0 - 3]).translate([-(NECK_W - 2) / 2, 1]).extrude(0.01).translate([0, 0, Z_B - RAMP_L])
    ramp = Manifold.batch_hull([ramp_top, ramp_bot])
    body = tube + rail + ramp
    rib_zone = Manifold.cube([NECK_W + 12, Y0 + DT_H + 5, A_H + 10]).translate([-(NECK_W + 12) / 2, 3, -5])
    bore_depth = A_H - CAP_T
    bore = Manifold.cylinder(bore_depth + 0.01, (ROD_D + BORE_CLEAR) / 2, circular_segments=SEG).translate([0, 0, -0.01])
    chamfer = Manifold.cylinder(1.0, (ROD_D + BORE_CLEAR) / 2 + 1.0, (ROD_D + BORE_CLEAR) / 2, SEG).translate([0, 0, -0.001])
    vent = Manifold.cylinder(A_H + 2, VENT_D / 2, circular_segments=24).translate([0, 0, -1])
    # Aussenkanten oben leicht fasen (angenehmer fuer Kinderhaende)
    top_chamfer = Manifold.cylinder(1.0, R_TUBE + 0.01, R_TUBE - 1.0, SEG).translate([0, 0, A_H - 1.0])
    ring = Manifold.cylinder(1.0, R_TUBE + 5, R_TUBE + 5, SEG).translate([0, 0, A_H - 1.0]) - top_chamfer
    ring = ring - rib_zone   # nur das Rohr fasen, nicht Rippe/Schiene
    bot_chamfer = Manifold.cylinder(1.5, R_TUBE - 1.5, R_TUBE + 0.01, SEG)
    bot_ring = Manifold.cylinder(1.5, R_TUBE + 5, R_TUBE + 5, SEG) - bot_chamfer
    part = body - bore - chamfer - vent - ring - bot_ring
    txt, mode = label_a()
    if txt is not None:
        part = part + txt if mode == "erhaben" else part - txt
    return part


def plate_frame(m, y_start):
    """lokal (x, v, t) -> Montage (x, y_start + t, v): 2D-Plattenskizze in x/z, Dicke in y."""
    return m.transform([[1, 0, 0, 0], [0, 0, 1, y_start], [0, 1, 0, 0]])


def part_b():
    # Klebeplatte: Skizze in (x, z), Dicke BASE_T in y
    plate2d = CrossSection.square([PLATE_W - 2 * PLATE_R, PLATE_H - 2 * PLATE_R]).translate(
        [-(PLATE_W / 2 - PLATE_R), -PLATE_BELOW + PLATE_R]).offset(PLATE_R, JoinType.Round, circular_segments=48)
    plate = plate_frame(plate2d.extrude(BASE_T), FLOOR_Y)

    # Nut-Leiste
    slot_len = RAIL_L + FIT_CLEAR
    boss = Manifold.cube([BOSS_W, FLOOR_Y - Y0 + 0.01, slot_len + STOP_T]).translate([-BOSS_W / 2, Y0, 0])

    keep = Manifold.cube([200, FLOOR_Y + 100, 300]).translate([-100, -100, -100])   # nichts unter den Nutgrund schneiden
    cav2d = rail_profile().offset(FIT_CLEAR, JoinType.Miter)
    cavity = cav2d.extrude(slot_len + 1).translate([0, 0, -1]) ^ keep

    # Einfuehrschraege am Nuteingang
    big = cav2d.offset(LEAD_IN, JoinType.Miter).extrude(0.01).translate([0, 0, -0.5])
    small = cav2d.extrude(0.01).translate([0, 0, LEAD_IN])
    lead = Manifold.batch_hull([big, small]) ^ keep

    # V-Kerben an den vier Kantenmitten zum Ausrichten auf Bleistiftlinien am Schild
    zc = -PLATE_BELOW + PLATE_H / 2
    zt, zb = PLATE_H - PLATE_BELOW, -PLATE_BELOW
    n = 2.5
    notch2d = (CrossSection([[[-1.5, zt + 0.1], [0, zt - n], [1.5, zt + 0.1]]]) +
               CrossSection([[[-1.5, zb - 0.1], [1.5, zb - 0.1], [0, zb + n]]]) +
               CrossSection([[[PLATE_W / 2 + 0.1, zc - 1.5], [PLATE_W / 2 + 0.1, zc + 1.5], [PLATE_W / 2 - n, zc]]]) +
               CrossSection([[[-PLATE_W / 2 - 0.1, zc - 1.5], [-PLATE_W / 2 + n, zc], [-PLATE_W / 2 - 0.1, zc + 1.5]]]))
    notches = plate_frame(notch2d.extrude(BASE_T + 2), FLOOR_Y - 1)

    part = plate + boss - cavity - lead - notches
    txt = label_b()
    if txt is not None:
        part = part + txt if TEXT_MODUS == "erhaben" else part - txt
    return part.translate([0, 0, Z_B])


def to_trimesh(m):
    mesh = m.to_mesh()
    return trimesh.Trimesh(vertices=np.asarray(mesh.vert_properties)[:, :3],
                           faces=np.asarray(mesh.tri_verts), process=False)


def main():
    a = part_a()
    b = part_b()

    # Druckorientierung
    a_print = a.rotate([180, 0, 0])                                         # Deckel auf dem Druckbett (drehen, nicht spiegeln!)
    b_print = b.rotate([-90, 0, 0])                                         # Klebeflaeche aufs Bett
    bb = b_print.bounding_box()
    b_print = b_print.translate([-(bb[0] + bb[3]) / 2, -(bb[1] + bb[4]) / 2, -bb[2]])
    bb = a_print.bounding_box()
    a_print = a_print.translate([-(bb[0] + bb[3]) / 2, -(bb[1] + bb[4]) / 2, -bb[2]])

    for name, m in (("Teil_A_Stangenkappe.stl", a_print), ("Teil_B_Schildplatte.stl", b_print)):
        t = to_trimesh(m)
        t.export(name)
        print(f"{name}: watertight={t.is_watertight}, volume={t.volume/1000:.1f} cm3, "
              f"size={np.round(t.extents, 1)} mm")

    # Montage (nur zur Ansicht): Stab + A + B + Schild
    rod = Manifold.cylinder(A_H - CAP_T + 60, ROD_D / 2, circular_segments=48).translate([0, 0, -60])
    sign = Manifold.cube([180, 3, 180]).translate([-90, GLUE_Y, Z_B - PLATE_BELOW + PLATE_H / 2 - 90])
    return a, b, rod, sign


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description="Schildhalter erzeugen (STL)")
    ap.add_argument("--text-a", default=TEXT_A, help="Text auf Teil A (Rohr-Rueckseite)")
    ap.add_argument("--text-b-links", default=TEXT_B_LINKS, help="Text auf Teil B, linker Streifen")
    ap.add_argument("--text-b-rechts", default=TEXT_B_RECHTS, help="Text auf Teil B, rechter Streifen")
    ap.add_argument("--modus", choices=["gravur", "erhaben"], default=TEXT_MODUS)
    ap.add_argument("--tiefe", type=float, default=TEXT_TIEFE, help="Gravurtiefe / Schrifthoehe in mm")
    ap.add_argument("--schrift", default=TEXT_SCHRIFT, help="Schriftname oder Pfad zu .ttf/.otf")
    args = ap.parse_args()
    TEXT_A, TEXT_B_LINKS, TEXT_B_RECHTS = args.text_a, args.text_b_links, args.text_b_rechts
    TEXT_MODUS, TEXT_TIEFE, TEXT_SCHRIFT = args.modus, args.tiefe, args.schrift
    main()
