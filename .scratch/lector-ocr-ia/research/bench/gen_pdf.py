"""Generate synthetic 2-page Spanish PDFs: native (text layer) and scanned (image only).

Usage: python gen_pdf.py <out_dir>
Writes native.pdf, scanned.pdf, mixed.pdf (p1 native, p2 scanned) and truth.json.
"""
import io
import json
import random
import sys
from pathlib import Path

import pypdfium2 as pdfium
from PIL import Image, ImageFilter
from reportlab.lib.pagesizes import letter
from reportlab.pdfgen import canvas

OUT = Path(sys.argv[1])
OUT.mkdir(parents=True, exist_ok=True)

FIELDS = {
    "identificacion": "1.023.456.789",
    "nro_comparendo": "11001000000045678912",
    "fecha": "02/01/2024",
}

PAGE1 = [
    ("Helvetica-Bold", 13, "SECRETARÍA DISTRITAL DE MOVILIDAD"),
    ("Helvetica-Bold", 11, "OFICINA DE GESTIÓN DE COBRO - JURISDICCIÓN COACTIVA"),
    ("Helvetica-Bold", 11, "RESOLUCIÓN No. 2024-118734 DEL 15 DE MARZO DE 2024"),
    ("Helvetica", 10, "Por medio de la cual se libra mandamiento de pago dentro del proceso de cobro coactivo"),
    ("Helvetica", 10, ""),
    ("Helvetica-Bold", 10, "EJECUTADO: JOSÉ ÁNGEL MUÑOZ PEÑA"),
    ("Helvetica-Bold", 10, "IDENTIFICACIÓN: C.C. 1.023.456.789"),
    ("Helvetica-Bold", 10, "COMPARENDO No. 11001000000045678912"),
    ("Helvetica-Bold", 10, "FECHA DE IMPOSICIÓN: 02/01/2024"),
    ("Helvetica", 10, "INFRACCIÓN: C29 - Conducir un vehículo a velocidad superior a la máxima permitida."),
    ("Helvetica", 10, "VALOR DE LA OBLIGACIÓN: $ 650.000 (seiscientos cincuenta mil pesos m/cte)."),
    ("Helvetica", 10, ""),
    ("Helvetica", 10, "El suscrito funcionario ejecutor, en uso de las facultades conferidas por el artículo 5"),
    ("Helvetica", 10, "de la Ley 1066 de 2006, el artículo 826 del Estatuto Tributario y el artículo 159 de la"),
    ("Helvetica", 10, "Ley 769 de 2002 (Código Nacional de Tránsito), y teniendo en cuenta que la sanción"),
    ("Helvetica", 10, "impuesta mediante la orden de comparendo citada se encuentra en firme, ejecutoriada y"),
    ("Helvetica", 10, "constituye una obligación clara, expresa y actualmente exigible a cargo del ejecutado,"),
    ("Helvetica", 10, "procede a librar mandamiento de pago por la vía administrativa coactiva."),
    ("Helvetica", 10, ""),
    ("Helvetica", 10, "Que revisado el sistema de información, el señor MUÑOZ PEÑA no acreditó el pago de"),
    ("Helvetica", 10, "la multa ni la celebración de un acuerdo de pago dentro del término legal, razón por la"),
    ("Helvetica", 10, "cual se causan intereses moratorios a la tasa señalada para los impuestos nacionales."),
    ("Helvetica", 10, "Dirección de notificación: Calle 26 Sur No. 68-45, Bogotá D.C., Teléfono 601 3649400."),
]
PAGE2 = [
    ("Helvetica-Bold", 11, "RESUELVE"),
    ("Helvetica", 10, ""),
    ("Helvetica", 10, "ARTÍCULO PRIMERO: Librar mandamiento de pago a favor de la Secretaría Distrital de"),
    ("Helvetica", 10, "Movilidad y en contra de JOSÉ ÁNGEL MUÑOZ PEÑA, identificado con C.C. 1.023.456.789,"),
    ("Helvetica", 10, "por la suma de seiscientos cincuenta mil pesos ($ 650.000), más los intereses de mora"),
    ("Helvetica", 10, "causados desde la fecha de imposición del comparendo 11001000000045678912."),
    ("Helvetica", 10, ""),
    ("Helvetica", 10, "ARTÍCULO SEGUNDO: Notificar personalmente el presente acto al ejecutado o a su"),
    ("Helvetica", 10, "apoderado, previa citación para que comparezca dentro de los diez (10) días siguientes."),
    ("Helvetica", 10, "Si no comparece, la notificación se surtirá por correo conforme al artículo 826 del E.T."),
    ("Helvetica", 10, ""),
    ("Helvetica", 10, "ARTÍCULO TERCERO: Advertir al ejecutado que dispone de quince (15) días después de la"),
    ("Helvetica", 10, "notificación para pagar o proponer las excepciones del artículo 831 del Estatuto Tributario."),
    ("Helvetica", 10, ""),
    ("Helvetica", 10, "ARTÍCULO CUARTO: Decretar el embargo y secuestro preventivo de los bienes del deudor"),
    ("Helvetica", 10, "hasta por el doble del valor adeudado, conforme a la ley vigente. Contra la presente"),
    ("Helvetica", 10, "resolución no procede recurso alguno (artículo 833 del Estatuto Tributario)."),
    ("Helvetica", 10, ""),
    ("Helvetica-Bold", 10, "NOTIFÍQUESE Y CÚMPLASE"),
    ("Helvetica", 10, "Dada en Bogotá D.C., a los quince (15) días del mes de marzo de dos mil veinticuatro."),
    ("Helvetica", 10, ""),
    ("Helvetica-Bold", 10, "MARÍA FERNANDA LÓPEZ GÜIZA"),
    ("Helvetica", 10, "Funcionaria Ejecutora - Subdirección de Jurisdicción Coactiva"),
    ("Helvetica", 9, "Proyectó: Andrés Núñez Ibáñez - Revisó: Ángela Rodríguez Peñaloza"),
]


def write_native(path: Path) -> None:
    c = canvas.Canvas(str(path), pagesize=letter)
    for page in (PAGE1, PAGE2):
        y = 740
        for font, size, text in page:
            c.setFont(font, size)
            c.drawString(60, y, text)
            y -= size + 9
        c.showPage()
    c.save()


def degrade(img: Image.Image, seed: int) -> Image.Image:
    """Simulate a mediocre office scan: grayscale, tilt, blur, noise, JPEG."""
    rnd = random.Random(seed)
    g = img.convert("L").rotate(0.8, expand=False, fillcolor=255, resample=Image.BICUBIC)
    g = g.filter(ImageFilter.GaussianBlur(0.6))
    px = g.load()
    w, h = g.size
    for _ in range(w * h // 60):  # salt & pepper speckles
        x, y = rnd.randrange(w), rnd.randrange(h)
        px[x, y] = rnd.choice((0, 90, 200))
    buf = io.BytesIO()
    g.save(buf, "JPEG", quality=65)
    return Image.open(io.BytesIO(buf.getvalue()))


def write_scanned(native: Path, path: Path, dpi: int = 200) -> list[Image.Image]:
    pdf = pdfium.PdfDocument(str(native))
    pages = [degrade(p.render(scale=dpi / 72).to_pil(), i) for i, p in enumerate(pdf)]
    pages[0].save(path, "PDF", resolution=dpi, save_all=True, append_images=pages[1:])
    return pages


native = OUT / "native.pdf"
write_native(native)
scanned_imgs = write_scanned(native, OUT / "scanned.pdf")
for i, im in enumerate(scanned_imgs, 1):
    im.save(OUT / f"scan_p{i}.png")

# mixed: page 1 native + page 2 scanned
mixed = pdfium.PdfDocument.new()
mixed.import_pages(pdfium.PdfDocument(str(native)), [0])
mixed.import_pages(pdfium.PdfDocument(str(OUT / "scanned.pdf")), [1])
mixed.save(str(OUT / "mixed.pdf"))

truth = {
    "fields": FIELDS,
    "pages": ["\n".join(t for _, _, t in PAGE1), "\n".join(t for _, _, t in PAGE2)],
}
(OUT / "truth.json").write_text(json.dumps(truth, ensure_ascii=False, indent=1), encoding="utf-8")
print("ok", [p.name for p in OUT.iterdir()])
