#!/usr/bin/env python3
"""RSS de tránsito: solo inconvenientes.

Lee https://www.alertastransito.com/p/alertas-de-transito-y-tiempo-manana-y.html,
se queda únicamente con los bloques que reportan problemas (choques, lentitud,
cortes, obras, desvíos) y los clasifica en:
  - Accesos CABA/GBA
  - AUBASA / Zona Sur
  - Costa Atlántica
y suma una lista editable de obras en curso (EXTRAS) de AUBASA y los corredores
de la Costa.

Uso:   python3 transito_rss.py [salida.xml]          (por defecto rss.xml)
       python3 transito_rss.py rss.xml --file p.html  (probar con HTML local)
Estado: guarda state.json para conservar la fecha en que apareció cada alerta
        (así los lectores RSS no duplican avisos). Solo librería estándar.
"""
import hashlib
import html
import json
import os
import re
import sys
import urllib.request
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from xml.sax.saxutils import escape

PAGE_URL = "https://www.alertastransito.com/p/alertas-de-transito-y-tiempo-manana-y.html"
FEED_TITLE = "Tránsito: solo inconvenientes (Accesos, AUBASA/Zona Sur, Costa Atlántica)"
AR = timezone(timedelta(hours=-3))  # Argentina no usa horario de verano
MAX_DESC = 400

# Info complementaria editable: obras en curso. Revisala de vez en cuando.
EXTRAS = [
    {
        "cat": "AUBASA / Zona Sur",
        "title": "Cuarto carril en Autopista Buenos Aires–La Plata (Acceso Sudeste – Hudson)",
        "desc": "AUBASA construye un cuarto carril entre el Acceso Sudeste (km 11,5) y el peaje "
        "Hudson (km 30,45), sentido La Plata. Obra en curso: puede haber interrupciones, "
        "sobre todo de noche.",
        "link": "https://gba.gob.ar/infraestructura/noticias/la_provincia_avanza_con_la_construcci%C3%B3n_del_cuarto_carril_de_la_autopista",
        "date": "2026-06-18",
    },
    {
        "cat": "Costa Atlántica",
        "title": "Repavimentación de la Autovía 2 (146 km) en ejecución",
        "desc": "Obra de repavimentación en ambas calzadas de la Autovía 2, con avance reportado "
        "del 60 % (fecha de la nota no confirmada). Circular con precaución en tramos en obra.",
        "link": "https://www.lacapitalmdp.com/avanza-la-repavimentacion-de-la-ruta-2-y-comienza-la-construccion-de-otro-tramo-de-la-autovia-11/",
        "date": None,
    },
    {
        "cat": "Costa Atlántica",
        "title": "Obras de autovía en Ruta 11 entre Villa Gesell y Mar Chiquita (72,4 km)",
        "desc": "La Provincia continúa la transformación en autovía de este tramo de la Ruta "
        "Provincial 11. Finalización estimada entre mediados y fines de 2026; verificar estado.",
        "link": "https://www.gba.gob.ar/node/53253",
        "date": "2026-05-27",
    },
]

# ---------------------------------------------------------------- scraping
START_MARK = re.compile(r"accesos ahora", re.I)
STOP_MARK = re.compile(r"respetar las velocidades|publicit[aá] en nuestras|cafecito", re.I)
ICONS = "🐢💥🚧🚫❌"
EMOJI_START = ("🐢", "💥", "🚫", "🟢")
INCONV = re.compile(
    r"(?i)choque|accidente|corte|cierr|cerrad|cerrar[aá]|desv[ií]|obras?\b|trabajos|demora|"
    r"intens|lent[oa]|reducid|bloquead|piquete|manifestaci|incidente|vuelco|incendio|"
    r"inundad|anegad|calzada"
)
CAT_AUBASA = re.compile(
    r"AUBASA|Buenos Aires.{0,3}La Plata|BALP|Punta Lara|Acceso Sudeste|Hudson|Sudeste|"
    r"Quilmes|Berazategui|Avellaneda|Gral\.? Belgrano|General Belgrano", re.I)
CAT_COSTA = re.compile(
    r"Ruta (Provincial )?(N[º°o]\.?\s?)?(2|11|63|56|74)\b|Costa Atl|Gesell|Mar Chiquita|"
    r"Aut[oó]v[ií]a (2|11)|Dolores|Mar del Plata|Pinamar|Chascom", re.I)


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; rss-transito/1.0)"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read().decode("utf-8", errors="replace")


def page_lines(raw):
    m = re.search(r"<div[^>]+class=['\"][^'\"]*post-body[^'\"]*['\"][^>]*>(.*)", raw, re.S | re.I)
    body = m.group(1) if m else raw
    body = re.sub(r"(?is)<(script|style|noscript)\b.*?</\1>", " ", body)
    body = re.sub(r"(?i)<br\s*/?>|</(p|div|li|h[1-6]|tr)>", "\n", body)
    body = re.sub(r"<[^>]+>", "", body)
    body = html.unescape(body).replace("\xa0", " ")
    return [re.sub(r"\s+", " ", l).strip() for l in body.splitlines() if l.strip()]


def clean(line):
    return re.sub(r"^[^\w¿¡]+", "", line).strip()


def is_start(line):
    if line.startswith(EMOJI_START):
        return True
    c = clean(line)
    if re.match(r"(?i)(importante|precauci[oó]n|aviso de obras)", c):
        return True
    return 0 < len(c) <= 40 and not re.search(r"\d|[.:]$", c)  # títulos cortos


def relevant_lines(lines):
    """Líneas entre 'Accesos ahora' y el cierre de la sección de tránsito."""
    start = next((i for i, l in enumerate(lines) if START_MARK.search(l)), None)
    if start is None:
        print("Aviso: no se encontró 'Accesos ahora'; se usa toda la página.", file=sys.stderr)
        start = -1
    out = []
    for l in lines[start + 1:]:
        if STOP_MARK.search(l):
            break
        out.append(l)
    return out


def to_blocks(lines):
    blocks, cur = [], []
    for l in lines:
        if is_start(l) and cur:
            blocks.append(cur)
            cur = []
        cur.append(l)
    if cur:
        blocks.append(cur)
    return blocks


def is_problem(text):
    t = re.sub(r"(?i)sin demoras?", "", text)
    return any(i in t for i in ICONS) or bool(INCONV.search(t))


def expired(text, today):
    ends = []
    for m in re.finditer(r"(?i)(hasta|finaliz\w*|\bfin\b)[^0-9]{0,40}(\d{1,2})[/-](\d{1,2})", text):
        try:
            ends.append(datetime(today.year, int(m.group(3)), int(m.group(2))).date())
        except ValueError:
            pass
    return bool(ends) and max(ends) < today


def category(text):
    if CAT_AUBASA.search(text):
        return "AUBASA / Zona Sur"
    if CAT_COSTA.search(text):
        return "Costa Atlántica"
    return "Accesos CABA/GBA"


def make_title(lines, cat):
    c = clean(lines[0])
    for l in lines:
        if len(clean(l)) >= 30 and INCONV.search(re.sub(r"(?i)sin demoras?", "", l)):
            c = clean(l)
            break
    if lines[0].startswith("🐢") and not re.search(r"(?i)tr[aá]nsito|intens|lent|choque", c):
        c = "Lento: " + c
    if len(c) > 110:
        c = c[:107].rstrip() + "..."
    return f"[{cat}] {c}"


def scrape_items(raw, today):
    items = []
    for blk in to_blocks(relevant_lines(page_lines(raw))):
        text = " ".join(blk)
        short_ok = blk[0].startswith(EMOJI_START[:3])  # 🐢 💥 🚫 aunque sean cortos
        if (len(text) < 40 and not short_ok) or not is_problem(text) or expired(text, today):
            continue
        cat = category(text)
        items.append({
            "cat": cat,
            "title": make_title(blk, cat),
            "desc": text if len(text) <= MAX_DESC else text[: MAX_DESC - 3].rstrip() + "...",
            "link": PAGE_URL,
            "date": None,
        })
    return items


# ---------------------------------------------------------------- feed
def guid_for(item):
    key = f"{item['cat']}|{re.sub(r'[^a-z0-9]', '', item['title'].lower())}"
    return hashlib.sha1(key.encode()).hexdigest()[:16]


def build_rss(items, now, feed_url=None):
    rows = []
    for it in sorted(items, key=lambda x: x["_dt"], reverse=True):
        rows.append(
            "    <item>\n"
            f"      <title>{escape(it['title'])}</title>\n"
            f"      <link>{escape(it['link'])}</link>\n"
            f'      <guid isPermaLink="false">{it["_guid"]}</guid>\n'
            f"      <pubDate>{format_datetime(it['_dt'])}</pubDate>\n"
            f"      <category>{escape(it['cat'])}</category>\n"
            f"      <description>{escape(it['desc'])}</description>\n"
            "    </item>"
        )
    atom = ""
    ns = ""
    if feed_url:
        ns = ' xmlns:atom="http://www.w3.org/2005/Atom"'
        atom = f'    <atom:link href="{escape(feed_url)}" rel="self" type="application/rss+xml"/>\n'
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<rss version="2.0"{ns}>\n  <channel>\n'
        f"    <title>{escape(FEED_TITLE)}</title>\n"
        f"    <link>{escape(PAGE_URL)}</link>\n"
        "    <description>Solo inconvenientes: accesos a CABA/GBA, AUBASA y zona sur, "
        "y camino a la Costa Atlántica. Fuente: alertastransito.com y AUBASA/Provincia.</description>\n"
        "    <language>es-AR</language>\n"
        f"    <lastBuildDate>{format_datetime(now)}</lastBuildDate>\n"
        "    <ttl>15</ttl>\n" + atom + "\n".join(rows) + "\n  </channel>\n</rss>\n"
    )


def attach_dates(items, state, now):
    """Fecha de primera aparición persistente por alerta."""
    seen = {}
    for it in items:
        g = guid_for(it)
        it["_guid"] = g
        if it.get("date"):
            it["_dt"] = datetime.fromisoformat(it["date"]).replace(tzinfo=AR)
        else:
            first = state.get(g) or now.isoformat()
            seen[g] = first
            it["_dt"] = datetime.fromisoformat(first)
    return seen


def main():
    args = [a for a in sys.argv[1:] if not a.startswith("--")]
    out = args[0] if args else "rss.xml"
    state_path = os.environ.get("STATE_FILE", "state.json")
    now = datetime.now(AR).replace(microsecond=0)

    if "--file" in sys.argv:
        raw = open(sys.argv[sys.argv.index("--file") + 1], encoding="utf-8").read()
    else:
        raw = fetch(PAGE_URL)  # si falla, sale con error y NO pisa el feed anterior

    try:
        state = json.load(open(state_path, encoding="utf-8"))
    except (OSError, ValueError):
        state = {}

    items = scrape_items(raw, now.date()) + [dict(e) for e in EXTRAS]
    seen = attach_dates(items, state, now)
    with open(out, "w", encoding="utf-8") as f:
        f.write(build_rss(items, now, os.environ.get("FEED_URL")))
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(seen, f, ensure_ascii=False, indent=1)
    print(f"{out}: {len(items)} alertas ({len(items) - len(EXTRAS)} de la página + {len(EXTRAS)} extras)")


if __name__ == "__main__":
    main()
