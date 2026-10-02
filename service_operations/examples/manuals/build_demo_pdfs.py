"""Build the two project-authored, one-page demonstration PDF versions."""

from pathlib import Path

ROOT = Path(__file__).resolve().parent

NOTES = {
    "taz_pro_record_v1.pdf": [
        "DEMO SERVICE RECORD NOTE v1 - NOT LULZBOT GUIDANCE",
        "Model: TAZ Pro. Effective 2025-10-01 to 2026-07-01 (Shanghai time).",
        "For internal NOZZLE_WIPE cases, record the asset ID, occurrence time,",
        "original display message, and work order priority.",
        "For troubleshooting, consult the official TAZ Pro User Manual, section 5.1.",
        "This fictional service record note is not a repair instruction.",
    ],
    "taz_pro_record_v2.pdf": [
        "DEMO SERVICE RECORD NOTE v2 - NOT LULZBOT GUIDANCE",
        "Model: TAZ Pro. Effective from 2026-07-01 (Shanghai time).",
        "For internal NOZZLE_WIPE cases, record the asset ID, occurrence time,",
        "original display message, work order priority, printing material,",
        "and Cura LulzBot Edition profile name.",
        "For troubleshooting, consult the official TAZ Pro User Manual, section 5.1.",
        "This fictional service record note is not a repair instruction.",
    ],
}


def pdf_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("(", "\\(").replace(")", "\\)")


def build_pdf(lines: list[str]) -> bytes:
    commands = ["BT", "/F1 12 Tf", "50 750 Td", "18 TL"]
    for line in lines:
        commands.extend([f"({pdf_escape(line)}) Tj", "T*"])
    commands.append("ET")
    stream = ("\n".join(commands) + "\n").encode("ascii")
    objects = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Resources << /Font << /F1 4 0 R >> >> /Contents 5 0 R >>",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
        f"<< /Length {len(stream)} >>\nstream\n".encode() + stream + b"endstream",
    ]
    data = bytearray(b"%PDF-1.4\n")
    offsets = [0]
    for index, obj in enumerate(objects, 1):
        offsets.append(len(data))
        data.extend(f"{index} 0 obj\n".encode() + obj + b"\nendobj\n")
    xref = len(data)
    data.extend(f"xref\n0 {len(objects)+1}\n0000000000 65535 f \n".encode())
    for offset in offsets[1:]:
        data.extend(f"{offset:010d} 00000 n \n".encode())
    data.extend(f"trailer\n<< /Size {len(objects)+1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF\n".encode())
    return bytes(data)


if __name__ == "__main__":
    for name, lines in NOTES.items():
        target = ROOT / name
        target.write_bytes(build_pdf(lines))
        print(target)
