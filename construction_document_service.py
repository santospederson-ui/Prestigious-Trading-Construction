"""
==============================================================
CONSTRUCTION DOCUMENT SERVICE
==============================================================

Purpose:
    Generate a new finalized copy of a material-request PDF.

Important:
    - NEVER overwrites the original uploaded PDF.
    - Original PDF remains untouched.
    - Creates a new PDF containing an approval/decline stamp.
    - Designed to work independently from Flask routes.
    - Can later be called by the material-request review route.

Required packages:
    reportlab
    pypdf
    requests
"""

import io
import os
from datetime import datetime

import requests

from pypdf import PdfReader, PdfWriter
from reportlab.pdfgen import canvas
from reportlab.lib.colors import HexColor


# ==============================================================
# CONFIGURATION
# ==============================================================

APPROVAL_RED = HexColor("#7f1d1d")
APPROVAL_GREEN = HexColor("#15803d")
TEXT_DARK = HexColor("#172033")
TEXT_MUTED = HexColor("#64748b")
WHITE = HexColor("#ffffff")


# ==============================================================
# DOWNLOAD ORIGINAL PDF
# ==============================================================

def download_pdf_from_url(file_url):
    """
    Download the original PDF from Cloudinary or another
    publicly accessible URL.

    Returns:
        bytes

    Raises:
        Exception if the PDF cannot be downloaded.
    """

    if not file_url:
        raise ValueError(
            "No PDF URL was provided."
        )

    response = requests.get(
        file_url,
        timeout=60
    )

    response.raise_for_status()

    content_type = (
        response.headers.get(
            "Content-Type",
            ""
        )
        .lower()
    )

    # ----------------------------------------------------------
    # Basic validation
    # ----------------------------------------------------------

    if not response.content:
        raise ValueError(
            "The downloaded PDF is empty."
        )

    # Some Cloudinary URLs may not return a perfect
    # application/pdf content type, so we do not reject
    # the document based only on Content-Type.

    return response.content


# ==============================================================
# CREATE STAMP PAGE
# ==============================================================

def create_stamp_page(
    page_width,
    page_height,
    decision,
    request_number,
    manager_name,
    manager_comment=None,
    decision_date=None
):
    """
    Create a transparent PDF overlay containing the
    approval or decline stamp.

    The returned bytes can be merged onto the original
    PDF's final page.
    """

    packet = io.BytesIO()

    stamp_canvas = canvas.Canvas(
        packet,
        pagesize=(
            page_width,
            page_height
        )
    )

    # ==========================================================
    # NORMALIZE DECISION
    # ==========================================================

    decision = (
        decision or ""
    ).strip().lower()

    if decision in (
        "approve",
        "approved"
    ):

        final_decision = "APPROVED"

        main_color = APPROVAL_GREEN

    else:

        final_decision = "DECLINED"

        main_color = APPROVAL_RED

    # ==========================================================
    # STAMP DIMENSIONS
    # ==========================================================

    stamp_width = min(
        250,
        page_width * 0.42
    )

    stamp_height = 145

    margin = 35

    x = (
        page_width
        - stamp_width
        - margin
    )

    y = margin

    # ==========================================================
    # SHADOW / BACKGROUND
    # ==========================================================

    stamp_canvas.setFillColor(
        HexColor("#000000")
    )

    stamp_canvas.setFillAlpha(
        0.08
    )

    stamp_canvas.roundRect(
        x + 4,
        y - 4,
        stamp_width,
        stamp_height,
        12,
        fill=1,
        stroke=0
    )

    # ==========================================================
    # WHITE STAMP BODY
    # ==========================================================

    stamp_canvas.setFillColor(
        WHITE
    )

    stamp_canvas.setFillAlpha(
        0.96
    )

    stamp_canvas.roundRect(
        x,
        y,
        stamp_width,
        stamp_height,
        12,
        fill=1,
        stroke=0
    )

    # ==========================================================
    # BORDER
    # ==========================================================

    stamp_canvas.setStrokeColor(
        main_color
    )

    stamp_canvas.setLineWidth(
        3
    )

    stamp_canvas.setFillAlpha(
        1
    )

    stamp_canvas.roundRect(
        x,
        y,
        stamp_width,
        stamp_height,
        12,
        fill=0,
        stroke=1
    )

    # ==========================================================
    # HEADER
    # ==========================================================

    stamp_canvas.setFillColor(
        main_color
    )

    stamp_canvas.setFont(
        "Helvetica-Bold",
        22
    )

    stamp_canvas.drawCentredString(
        x + stamp_width / 2,
        y + stamp_height - 35,
        final_decision
    )

    # ==========================================================
    # DIVIDER
    # ==========================================================

    stamp_canvas.setStrokeColor(
        main_color
    )

    stamp_canvas.setLineWidth(
        1
    )

    stamp_canvas.line(
        x + 20,
        y + stamp_height - 48,
        x + stamp_width - 20,
        y + stamp_height - 48
    )

    # ==========================================================
    # REQUEST NUMBER
    # ==========================================================

    stamp_canvas.setFillColor(
        TEXT_DARK
    )

    stamp_canvas.setFont(
        "Helvetica-Bold",
        9
    )

    stamp_canvas.drawString(
        x + 20,
        y + stamp_height - 66,
        "REQUEST:"
    )

    stamp_canvas.setFont(
        "Helvetica",
        9
    )

    stamp_canvas.drawRightString(
        x + stamp_width - 20,
        y + stamp_height - 66,
        str(
            request_number
            or "N/A"
        )
    )

    # ==========================================================
    # MANAGER
    # ==========================================================

    stamp_canvas.setFont(
        "Helvetica-Bold",
        9
    )

    stamp_canvas.drawString(
        x + 20,
        y + stamp_height - 82,
        "REVIEWED BY:"
    )

    stamp_canvas.setFont(
        "Helvetica",
        9
    )

    stamp_canvas.drawRightString(
        x + stamp_width - 20,
        y + stamp_height - 82,
        str(
            manager_name
            or "Manager"
        )[:35]
    )

    # ==========================================================
    # DATE
    # ==========================================================

    if decision_date is None:

        decision_date = datetime.now()

    if hasattr(
        decision_date,
        "strftime"
    ):

        formatted_date = (
            decision_date.strftime(
                "%d %b %Y %H:%M"
            )
        )

    else:

        formatted_date = str(
            decision_date
        )

    stamp_canvas.setFont(
        "Helvetica-Bold",
        9
    )

    stamp_canvas.drawString(
        x + 20,
        y + stamp_height - 98,
        "DATE:"
    )

    stamp_canvas.setFont(
        "Helvetica",
        9
    )

    stamp_canvas.drawRightString(
        x + stamp_width - 20,
        y + stamp_height - 98,
        formatted_date
    )

    # ==========================================================
    # DECLINE REASON
    # ==========================================================

    if final_decision == "DECLINED":

        stamp_canvas.setFillColor(
            main_color
        )

        stamp_canvas.setFont(
            "Helvetica-Bold",
            8
        )

        stamp_canvas.drawString(
            x + 20,
            y + 28,
            "REASON:"
        )

        reason = (
            manager_comment
            or "No reason provided."
        )

        # Keep the stamp compact.
        reason = str(reason)

        if len(reason) > 55:

            reason = (
                reason[:52]
                + "..."
            )

        stamp_canvas.setFillColor(
            TEXT_DARK
        )

        stamp_canvas.setFont(
            "Helvetica",
            8
        )

        stamp_canvas.drawString(
            x + 20,
            y + 15,
            reason
        )

    # ==========================================================
    # APPROVED MARK
    # ==========================================================

    else:

        stamp_canvas.setFillColor(
            main_color
        )

        stamp_canvas.setFont(
            "Helvetica-Bold",
            8
        )

        stamp_canvas.drawCentredString(
            x + stamp_width / 2,
            y + 17,
            "MANAGEMENT APPROVAL"
        )

    # ==========================================================
    # FINISH
    # ==========================================================

    stamp_canvas.save()

    packet.seek(0)

    return packet.read()


# ==============================================================
# GENERATE FINALIZED PDF
# ==============================================================

def generate_finalized_material_request_pdf(
    original_pdf_bytes,
    decision,
    request_number,
    manager_name,
    manager_comment=None,
    decision_date=None
):
    """
    Generate a new finalized material-request PDF.

    The original PDF bytes are never modified.

    Returns:
        bytes containing the finalized PDF.
    """

    if not original_pdf_bytes:

        raise ValueError(
            "Original PDF data is empty."
        )

    # ==========================================================
    # READ ORIGINAL
    # ==========================================================

    original_stream = io.BytesIO(
        original_pdf_bytes
    )

    reader = PdfReader(
        original_stream
    )

    if not reader.pages:

        raise ValueError(
            "The original PDF contains no pages."
        )

    writer = PdfWriter()

    # ==========================================================
    # COPY ORIGINAL PAGES
    # ==========================================================

    for page in reader.pages:

        writer.add_page(
            page
        )

    # ==========================================================
    # FINAL PAGE
    # ==========================================================

    final_page = writer.pages[-1]

    page_width = float(
        final_page.mediabox.width
    )

    page_height = float(
        final_page.mediabox.height
    )

    # ==========================================================
    # CREATE STAMP
    # ==========================================================

    stamp_bytes = create_stamp_page(
        page_width=page_width,
        page_height=page_height,
        decision=decision,
        request_number=request_number,
        manager_name=manager_name,
        manager_comment=manager_comment,
        decision_date=decision_date
    )

    stamp_reader = PdfReader(
        io.BytesIO(
            stamp_bytes
        )
    )

    stamp_page = stamp_reader.pages[0]

    # ==========================================================
    # MERGE STAMP WITH FINAL PAGE
    # ==========================================================

    final_page.merge_page(
        stamp_page
    )

    # ==========================================================
    # WRITE NEW PDF
    # ==========================================================

    output_stream = io.BytesIO()

    writer.write(
        output_stream
    )

    output_stream.seek(0)

    return output_stream.read()


# ==============================================================
# DOWNLOAD + GENERATE
# ==============================================================

def generate_finalized_material_request_pdf_from_url(
    file_url,
    decision,
    request_number,
    manager_name,
    manager_comment=None,
    decision_date=None
):
    """
    Convenience function.

    Downloads the original PDF and creates a new finalized
    copy without changing the original.
    """

    original_pdf = (
        download_pdf_from_url(
            file_url
        )
    )

    finalized_pdf = (
        generate_finalized_material_request_pdf(
            original_pdf_bytes=original_pdf,
            decision=decision,
            request_number=request_number,
            manager_name=manager_name,
            manager_comment=manager_comment,
            decision_date=decision_date
        )
    )

    return finalized_pdf


# ==============================================================
# LOCAL TEST HELPER
# ==============================================================

def save_finalized_pdf(
    pdf_bytes,
    output_path
):
    """
    Optional helper for local testing.

    This does not upload anything to Cloudinary.
    """

    if not pdf_bytes:

        raise ValueError(
            "No PDF data was supplied."
        )

    directory = os.path.dirname(
        output_path
    )

    if directory:

        os.makedirs(
            directory,
            exist_ok=True
        )

    with open(
        output_path,
        "wb"
    ) as output_file:

        output_file.write(
            pdf_bytes
        )

    return output_path