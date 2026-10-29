#!/usr/bin/env python3
"""RSS de tránsito en formato resumen (solo inconvenientes).

Genera hasta 4 entradas, una por sección, con viñetas:
  🚧 Obras y cortes
  🚗 Accesos y autopistas (incluye AUBASA / Autopista Buenos Aires–La Plata)
  🏖 Costa Atlántica
  🚆 Transporte público (subte, trenes, colectivos, paros)

Fuentes:
  - https://www.alertastransito.com/p/alertas-de-transito-y-tiempo-manana-y.html
  - feeds Atom por etiqueta del propio blog (transporte público)
  - lista EXTRAS (obras en curso de AUBASA y la Costa; editala a mano)

Se descartan: tramos normales, avisos vencidos y datos de calles/barrios de CABA
(solo quedan autopistas, accesos y rutas).

Antigüedad máxima: 96 hs (MAX_AGE_H). Cada viñeta lleva su fecha en data-ts.

Uso:  python3 transito_rss.py [salida.xml]
      python3 transito_rss.py rss.xml --file pagina.html   (probar con HTML local)
Solo usa la librería estándar de Python 3.8+.
"""
import hashlib
import html
import json
import os
import re
import sys
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta, timezone
from email.utils import format_datetime
from xml.sax.saxutils import escape

PAGE_URL = "https://www.alertastransito.com/p/alertas-de-transito-y-tiempo-manana-y.html"
ATOM_LABEL = "https://www.alertastransito.com/feeds/posts/default/-/{label}?max-results=6"
TRANSPORT_LABELS = ["subtes", "trenes", "colectivos", "paros"]
FEED_TITLE = "Resumen de tránsito: obras, accesos, AUBASA y Costa Atlántica"
AR = timezone(timedelta(hours=-3))  # Argentina no usa horario de verano
MAX_BULLET = 300
MAX_AGE_H = 96  # no se usan alertas "fijadas" con más de 96 hs de antigüedad

SECTIONS = [
    ("obras", "🚧 Obras y cortes"),
    ("accesos", "🚗 Accesos y autopistas"),
    ("costa", "🏖 Costa Atlántica"),
    ("transporte", "🚆 Transporte público"),
]

# Obras en curso (editable). (sección, texto, enlace, fecha de la fuente o None)
# Las que tengan más de MAX_AGE_H horas de antigüedad no se publican.
EXTRAS = [
    ("accesos",
     "Cuarto carril en la Autopista Buenos Aires–La Plata entre el Acceso Sudeste (km 11,5) y "
     "Hudson (km 30,45), sentido La Plata. Posibles interrupciones, sobre todo de noche.",
     "https://gba.gob.ar/infraestructura/noticias/la_provincia_avanza_con_la_construcci%C3%B3n_del_cuarto_carril_de_la_autopista",
     "2026-06-18"),
    ("costa",
     "Repavimentación de la Autovía 2 (146 km, ambas calzadas) en ejecución; avance reportado "
     "del 60 % (fecha de la nota no confirmada).",
     "https://www.lacapitalmdp.com/avanza-la-repavimentacion-de-la-ruta-2-y-comienza-la-construccion-de-otro-tramo-de-la-autovia-11/",
     None),
    ("costa",
     "Obras de autovía en la Ruta 11 entre Villa Gesell y Mar Chiquita (72,4 km). "
     "Finalización estimada entre mediados y fines de 2026; verificar estado.",
     "https://www.gba.gob.ar/node/53253",
     "2026-05-27"),
]

# ------------------------------------------------------------ extracción
START_MARK = re.compile(r"accesos ahora", re.I)
STOP_MARK = re.compile(r"respetar las velocidades|publicit[aá] en nuestras|cafecito", re.I)
PROBLEM_STARTS = ("🐢", "💥", "🚫", "🚨")
EMOJI_START = PROBLEM_STARTS + ("🟢",)
INCONV = re.compile(
    r"(?i)choque|accidente|corte|cierr|cerrad|cerrar[aá]|desv[ií]|obras?\b|trabajos|demora|"
    r"intens|lent[oa]|reducid|bloquead|piquete|manifestaci|incidente|vuelco|incendio|"
    r"inundad|anegad|calzada|baliz"
)
HIGHWAY = re.compile(
    r"(?i)\bau\.|autopista|gral\.? ?paz|general paz|panamericana|acceso (norte|oeste|sudeste)|"
    r"riccheri|camino negro|dellepiane|BALP|AUBASA|\bruta\b|autov[ií]a|camino|peaje")
CAT_COSTA = re.compile(
    r"(?i)ruta (provincial )?(n[º°o]\.?\s?)?(2|11|63|56|74)\b|costa atl|gesell|mar chiquita|"
    r"aut[oó]v[ií]a (2|11)|dolores|mar del plata|pinamar|chascom")
DROP_LINE = re.compile(
    r"(?i)se recomienda|circul[aá] con|debidamente se[ñn]alizado|respetar la se[ñn]al|"
    r"distancia de seguridad|no usar el celular")
BALIZ = re.compile(
    r"(?i)baliz\w+.*?km\s*([\d.,]+)\s*al\s*km\s*([\d.,]+)\s*sentido\s*(\w+)"
    r"(?:.*?carril(?:es)?\s*([\d\s,y]+))?")
TRANSPORT_KW = re.compile(
    r"(?i)paro|huelga|demora|interrump|suspend|limitad|reducid|sin servicio|corte|desv[ií]|"
    r"obras|restricci|afectad|cronograma")


def fetch(url):
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (compatible; rss-transito/2.0)"})
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
    return any(i in t for i in "🐢💥🚧🚫❌🚨") or bool(INCONV.search(t))


def expired(text, today):
    ends = []
    for m in re.finditer(r"(?i)(hasta|finaliz\w*|\bfin\b)[^0-9]{0,40}(\d{1,2})[/-](\d{1,2})", text):
        try:
            ends.append(datetime(today.year, int(m.group(3)), int(m.group(2))).date())
        except ValueError:
            pass
    m = re.search(r"(?i)desde el (\d{1,2})[/-](\d{1,2}).{0,150}?durante (\d+) d[ií]as", text)
    if m:
        try:
            ends.append(datetime(today.year, int(m.group(2)), int(m.group(1))).date() + timedelta(days=int(m.group(3))))
        except ValueError:
            pass
    return bool(ends) and max(ends) < today


def tidy(line):
    t = re.sub(r"[^\w\s,.;:()/%\-–ºª°¿?¡!'\"“”]", " ", line)  # quita emojis y flechas
    t = re.sub(r"\s+", " ", t).strip(" .-–")
    return t


def section_of(text):
    if CAT_COSTA.search(text) and not re.search(r"BALP|AUBASA|Punta Lara", text):
        return "costa"
    if re.search(r"(?i)cierr|cerrad|cerrar[aá]|desv[ií]|\bobras?\b", text):
        return "obras"
    return "accesos"


def make_bullet(blk):
    parts = []
    for l in blk:
        if DROP_LINE.search(l):
            continue
        c = clean(l)
        if len(blk) > 1 and len(c) <= 40 and not re.search(r"\d|[.:]$", c):
            continue  # título suelto
        t = tidy(l)
        if t:
            parts.append(t if t[-1] in ".!?:" else t + ".")
    text = " ".join(parts)
    aubasa = bool(re.match(r"(?i)\s*BALP\b", text))
    text = re.sub(r"(?i)^(BALP:?\s*)?(IMPORTANTE:?\s*)?", "", text).strip()
    if len(text) > MAX_BULLET:
        text = text[:MAX_BULLET].rsplit(" ", 1)[0].rstrip(",;:") + "…"
    out = html.escape(text)
    return ("<b>AUBASA (Bs. As.–La Plata):</b> " + out) if aubasa else out


def collect(raw, today):
    sec = {k: [] for k, _ in SECTIONS}
    baliz = []
    for blk in to_blocks(relevant_lines(page_lines(raw))):
        text = " ".join(blk)
        short_ok = blk[0].startswith(PROBLEM_STARTS)
        if (len(text) < 40 and not short_ok) or not is_problem(text) or expired(text, today):
            continue
        if not HIGHWAY.search(text):  # descarta datos de calles/barrios de CABA
            continue
        m = BALIZ.search(text)
        if m:
            sentido = {"LP": "La Plata"}.get(m.group(3).upper(), m.group(3))
            carriles = re.sub(r"\s+", " ", (m.group(4) or "").strip(" ,"))
            baliz.append(f"km {m.group(1)}→{m.group(2)} sentido {sentido}"
                         + (f" ({'carriles' if re.search(r'[y,]', carriles) else 'carril'} {carriles})" if carriles else ""))
            continue
        sec[section_of(text)].append((make_bullet(blk), None))
    if baliz:
        sec["accesos"].insert(0, ("<b>AUBASA (Bs. As.–La Plata):</b> balizamiento por tareas de "
                                  "mantenimiento: " + html.escape("; ".join(baliz)) + ".", None))
    return sec


def transport_bullets(now):
    out, seen = [], set()
    for label in TRANSPORT_LABELS:
        try:
            root = ET.fromstring(fetch(ATOM_LABEL.format(label=label)).encode("utf-8"))
        except Exception as e:  # el feed es opcional: si falla, se omite
            print(f"Aviso: no se pudo leer el feed '{label}': {e}", file=sys.stderr)
            continue
        ns = {"a": "http://www.w3.org/2005/Atom"}
        for e in root.findall("a:entry", ns):
            title = (e.findtext("a:title", "", ns) or "").strip()
            pub = (e.findtext("a:published", "", ns) or "").strip()
            link = next((l.get("href") for l in e.findall("a:link", ns) if l.get("rel") == "alternate"), None)
            try:
                dt = datetime.fromisoformat(pub)
            except ValueError:
                continue
            if not link or link in seen or now - dt > timedelta(hours=MAX_AGE_H) or not TRANSPORT_KW.search(title):
                continue
            seen.add(link)
            out.append((f'<a href="{escape(link, {chr(34): "&quot;"})}">{html.escape(title)}</a>',
                        dt.astimezone(AR)))
    return out


# ------------------------------------------------------------ feed
def stamp(sec, seen, now):
    """Asigna fecha a cada viñeta (primera vez vista o fecha de la fuente), descarta las de más
    de MAX_AGE_H horas y ordena de la más reciente a la más antigua."""
    out, new_seen = {}, {}
    for key, bullets in sec.items():
        rows = []
        for h, dt in bullets:
            if dt is None:
                k = hashlib.sha1(h.encode()).hexdigest()[:12]
                dt = datetime.fromisoformat(seen[k]) if k in seen else now
                new_seen[k] = dt.isoformat()  # se conserva aunque esté vencida (evita que "reviva")
            if now - dt > timedelta(hours=MAX_AGE_H):
                continue
            rows.append((h, dt))
        out[key] = sorted(rows, key=lambda r: r[1], reverse=True)
    return out, new_seen


def build_items(sec, now):
    items = []
    for key, label in SECTIONS:
        rows = sec.get(key)
        if not rows:
            continue
        body = "<ul>" + "".join(f'<li data-ts="{dt.isoformat()}">{h}</li>' for h, dt in rows) + "</ul>"
        h = hashlib.sha1("|".join(r[0] for r in rows).encode()).hexdigest()[:12]
        newest = rows[0][1]
        items.append({"key": key, "guid": f"{key}-{h}", "dt": newest, "body": body,
                      "title": f"{label} — {newest:%d/%m %H:%M}"})
    return items


def build_rss(items, now, feed_url=None):
    rows = []
    for it in items:
        rows.append(
            "    <item>\n"
            f"      <title>{escape(it['title'])}</title>\n"
            f"      <link>{PAGE_URL}</link>\n"
            f'      <guid isPermaLink="false">{it["guid"]}</guid>\n'
            f"      <pubDate>{format_datetime(it['dt'])}</pubDate>\n"
            f"      <description><![CDATA[{it['body']}]]></description>\n"
            "    </item>"
        )
    ns, atom = "", ""
    if feed_url:
        ns = ' xmlns:atom="http://www.w3.org/2005/Atom"'
        atom = f'    <atom:link href="{escape(feed_url)}" rel="self" type="application/rss+xml"/>\n'
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        f'<rss version="2.0"{ns}>\n  <channel>\n'
        f"    <title>{escape(FEED_TITLE)}</title>\n"
        f"    <link>{PAGE_URL}</link>\n"
        "    <description>Resumen de inconvenientes: obras y cortes, accesos y autopistas "
        "(AUBASA), camino a la Costa Atlántica y transporte público.</description>\n"
        "    <language>es-AR</language>\n"
        f"    <lastBuildDate>{format_datetime(now)}</lastBuildDate>\n"
        "    <ttl>15</ttl>\n" + atom + "\n".join(rows) + "\n  </channel>\n</rss>\n"
    )


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
    seen = state.get("bullets", {}) if state.get("v") == 2 else {}

    sec = collect(raw, now.date())
    for key, text, link, date in EXTRAS:
        dt = datetime.fromisoformat(date).replace(tzinfo=AR) if date else None
        sec[key].append((f"<b>Obra en curso:</b> {html.escape(text)} "
                         f'<a href="{escape(link, {chr(34): "&quot;"})}">Fuente</a>', dt))
    sec["transporte"] = transport_bullets(now)

    sec, new_seen = stamp(sec, seen, now)
    items = build_items(sec, now)
    with open(out, "w", encoding="utf-8") as f:
        f.write(build_rss(items, now, os.environ.get("FEED_URL")))
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump({"v": 2, "bullets": new_seen}, f, ensure_ascii=False, indent=1)
    print(f"{out}: {len(items)} secciones ({', '.join(i['key'] for i in items)})")


if __name__ == "__main__":
    main()
