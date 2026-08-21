"""
==============================================================
CONSTRUCTION DECISION DOCUMENT GENERATOR
==============================================================

Purpose:

Generate a new APPROVED or DECLINED copy of a material
request PDF without modifying the original uploaded document.

The original PDF remains untouched.

Workflow:

Original PDF
     |
     v
Download original
     |
     v
Create decision stamp
     |
     v
Overlay stamp onto original
     |
     v
Create NEW PDF
     |
     v
Upload NEW PDF to Cloudinary
     |
     v
Return filename + URL

This module does NOT:
- update the material request table
- update the decision-document table
- change the manager route
- send email
- modify the original PDF
"""

import os
import io
import tempfile
import requests

from datetime import datetime

from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import A4
from reportlab.lib import colors
from reportlab.lib.colors import HexColor

from pypdf import PdfReader, PdfWriter


# ==============================================================
# OPTIONAL CLOUDINARY IMPORT
# ==============================================================

try:

    import cloudinary
    import cloudinary.uploader

except ImportError:

    cloudinary = None


# ==============================================================
# CONSTANTS
# ==============================================================

COMPANY_NAME = "Prestigious Trading & Constructions"

DOCUMENT_FOLDER = "construction/procurement/decision_documents"


# ==============================================================
# DOWNLOAD ORIGINAL PDF
# ==============================================================

def _download_original_pdf(file_url):
    """
    Download the original PDF from Cloudinary or another
    publicly accessible URL.

    Returns:
        bytes

    Raises:
        ValueError
        requests.RequestException
    """

    if not file_url:

        raise ValueError(
            "Original PDF URL is missing."
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
        ).lower()
    )

    if (
        "pdf" not in content_type
        and not file_url.lower().split("?")[0].endswith(".pdf")
    ):

        # Some Cloudinary URLs may not return a conventional
        # application/pdf content type, so we don't immediately
        # reject the file if it contains valid PDF bytes.

        if not response.content.startswith(b"%PDF"):

            raise ValueError(
                "The material request document does not appear "
                "to be a valid PDF."
            )

    if not response.content.startswith(b"%PDF"):

        raise ValueError(
            "The downloaded material request document is not "
            "a valid PDF."
        )

    return response.content


# ==============================================================
# CREATE DECISION STAMP
# ==============================================================

def _create_decision_stamp(
    decision,
    request_number,
    manager_name,
    manager_comment,
    decision_date
):
    """
    Create a transparent PDF overlay containing the approval
    or decline stamp.

    The returned bytes contain a one-page PDF overlay.

    The overlay is designed for A4 documents.
    """

    decision = (
        decision or ""
    ).strip().lower()

    if decision not in (
        "approved",
        "declined"
    ):

        raise ValueError(
            "Decision must be Approved or Declined."
        )

    if not decision_date:

        decision_date = datetime.now()

    # ----------------------------------------------------------
    # A4 dimensions
    # ----------------------------------------------------------

    page_width, page_height = A4

    # ----------------------------------------------------------
    # Memory PDF
    # ----------------------------------------------------------

    buffer = io.BytesIO()

    pdf = canvas.Canvas(
        buffer,
        pagesize=A4
    )

    # ----------------------------------------------------------
    # Stamp positioning
    #
    # We place the stamp in the upper-right portion of the
    # document so it is highly visible but normally does not
    # cover the main body of the request.
    # ----------------------------------------------------------

    stamp_width = 225

    stamp_height = 135

    stamp_x = (
        page_width
        - stamp_width
        - 35
    )

    stamp_y = (
        page_height
        - stamp_height
        - 35
    )

    # ----------------------------------------------------------
    # Colors
    # ----------------------------------------------------------

    if decision == "approved":

        main_color = HexColor(
            "#15803D"
        )

        light_color = HexColor(
            "#F0FDF4"
        )

        border_color = HexColor(
            "#16A34A"
        )

        decision_text = "APPROVED"

    else:

        main_color = HexColor(
            "#B91C1C"
        )

        light_color = HexColor(
            "#FEF2F2"
        )

        border_color = HexColor(
            "#DC2626"
        )

        decision_text = "DECLINED"

    # ----------------------------------------------------------
    # Background
    # ----------------------------------------------------------

    pdf.setFillColor(
        light_color
    )

    pdf.setStrokeColor(
        border_color
    )

    pdf.setLineWidth(
        3
    )

    pdf.roundRect(
        stamp_x,
        stamp_y,
        stamp_width,
        stamp_height,
        12,
        stroke=1,
        fill=1
    )

    # ----------------------------------------------------------
    # Decision title
    # ----------------------------------------------------------

    pdf.setFillColor(
        main_color
    )

    pdf.setFont(
        "Helvetica-Bold",
        24
    )

    pdf.drawCentredString(
        stamp_x + stamp_width / 2,
        stamp_y + stamp_height - 34,
        decision_text
    )

    # ----------------------------------------------------------
    # Company
    # ----------------------------------------------------------

    pdf.setFillColor(
        HexColor("#334155")
    )

    pdf.setFont(
        "Helvetica-Bold",
        8
    )

    pdf.drawCentredString(
        stamp_x + stamp_width / 2,
        stamp_y + stamp_height - 51,
        COMPANY_NAME
    )

    # ----------------------------------------------------------
    # Divider
    # ----------------------------------------------------------

    pdf.setStrokeColor(
        border_color
    )

    pdf.setLineWidth(
        1
    )

    pdf.line(
        stamp_x + 15,
        stamp_y + stamp_height - 60,
        stamp_x + stamp_width - 15,
        stamp_y + stamp_height - 60
    )

    # ----------------------------------------------------------
    # Request number
    # ----------------------------------------------------------

    pdf.setFillColor(
        HexColor("#172033")
    )

    pdf.setFont(
        "Helvetica-Bold",
        8
    )

    pdf.drawString(
        stamp_x + 16,
        stamp_y + stamp_height - 78,
        "REQUEST:"
    )

    pdf.setFont(
        "Helvetica",
        8
    )

    pdf.drawRightString(
        stamp_x + stamp_width - 16,
        stamp_y + stamp_height - 78,
        str(request_number or "")
    )

    # ----------------------------------------------------------
    # Manager
    # ----------------------------------------------------------

    pdf.setFont(
        "Helvetica-Bold",
        8
    )

    pdf.drawString(
        stamp_x + 16,
        stamp_y + stamp_height - 94,
        "REVIEWED BY:"
    )

    pdf.setFont(
        "Helvetica",
        8
    )

    pdf.drawRightString(
        stamp_x + stamp_width - 16,
        stamp_y + stamp_height - 94,
        str(manager_name or "Manager")
    )

    # ----------------------------------------------------------
    # Decision date
    # ----------------------------------------------------------

    formatted_date = decision_date.strftime(
        "%d %b %Y %H:%M"
    )

    pdf.setFont(
        "Helvetica-Bold",
        8
    )

    pdf.drawString(
        stamp_x + 16,
        stamp_y + stamp_height - 110,
        "DATE:"
    )

    pdf.setFont(
        "Helvetica",
        8
    )

    pdf.drawRightString(
        stamp_x + stamp_width - 16,
        stamp_y + stamp_height - 110,
        formatted_date
    )

    # ----------------------------------------------------------
    # Comment / reason
    # ----------------------------------------------------------

    comment = (
        manager_comment
        or "No comment provided."
    )

    # Keep the stamp compact. The complete comment will also
    # remain stored in the database during Stage 3.

    if len(comment) > 52:

        comment = (
            comment[:49]
            + "..."
        )

    pdf.setFont(
        "Helvetica-Bold",
        7
    )

    pdf.drawString(
        stamp_x + 16,
        stamp_y + 17,
        "COMMENT:"
    )

    pdf.setFont(
        "Helvetica",
        7
    )

    pdf.drawRightString(
        stamp_x + stamp_width - 16,
        stamp_y + 17,
        comment
    )

    pdf.save()

    buffer.seek(0)

    return buffer.getvalue()


# ==============================================================
# APPLY STAMP TO ORIGINAL PDF
# ==============================================================

def _apply_stamp_to_pdf(
    original_pdf_bytes,
    stamp_pdf_bytes
):
    """
    Overlay the decision stamp onto every page of the original
    PDF.

    The original bytes are never modified.

    Returns:
        bytes
    """

    original_stream = io.BytesIO(
        original_pdf_bytes
    )

    stamp_stream = io.BytesIO(
        stamp_pdf_bytes
    )

    reader = PdfReader(
        original_stream
    )

    stamp_reader = PdfReader(
        stamp_stream
    )

    if not reader.pages:

        raise ValueError(
            "The original PDF contains no pages."
        )

    stamp_page = (
        stamp_reader.pages[0]
    )

    writer = PdfWriter()

    # ----------------------------------------------------------
    # Apply the stamp to every page.
    #
    # This ensures that if the original request contains
    # multiple pages, the decision identification remains
    # attached to the complete document.
    # ----------------------------------------------------------

    for original_page in reader.pages:

        original_page.merge_page(
            stamp_page
        )

        writer.add_page(
            original_page
        )

    output_stream = io.BytesIO()

    writer.write(
        output_stream
    )

    output_stream.seek(0)

    return output_stream.getvalue()


# ==============================================================
# UPLOAD DECISION PDF TO CLOUDINARY
# ==============================================================

def _upload_decision_pdf(
    pdf_bytes,
    public_id
):
    """
    Upload generated PDF to Cloudinary.

    Returns:
        Cloudinary upload result dictionary.
    """

    if cloudinary is None:

        raise RuntimeError(
            "Cloudinary is not installed. "
            "Install the cloudinary package before using "
            "the decision document generator."
        )

    if not public_id:

        raise ValueError(
            "Cloudinary public_id is missing."
        )

    result = cloudinary.uploader.upload(
        io.BytesIO(pdf_bytes),
        resource_type="raw",
        public_id=public_id,
        overwrite=True
    )

    return result


# ==============================================================
# MAIN GENERATOR
# ==============================================================

def generate_material_request_decision_document(
    original_file_url,
    original_file_name,
    request_number,
    decision,
    manager_name,
    manager_comment=None,
    decision_date=None
):
    """
    Main public function.

    Parameters
    ----------
    original_file_url:
        Original Cloudinary PDF URL.

    original_file_name:
        Original uploaded filename.

    request_number:
        Material request number.

    decision:
        Approved or Declined.

    manager_name:
        Manager who made the decision.

    manager_comment:
        Manager comment or decline reason.

    decision_date:
        datetime object. If omitted, current datetime is used.

    Returns
    -------
    dict

        {
            "success": True,
            "decision": "Approved",
            "file_name": "...",
            "file_url": "...",
            "public_id": "..."
        }
    """

    # ==========================================================
    # VALIDATE DECISION
    # ==========================================================

    normalized_decision = (
        decision or ""
    ).strip().lower()

    if normalized_decision in (
        "approve",
        "approved"
    ):

        normalized_decision = "approved"

        display_decision = "Approved"

    elif normalized_decision in (
        "decline",
        "declined"
    ):

        normalized_decision = "declined"

        display_decision = "Declined"

    else:

        raise ValueError(
            "Decision must be Approved or Declined."
        )

    # ==========================================================
    # DATE
    # ==========================================================

    if decision_date is None:

        decision_date = datetime.now()

    # ==========================================================
    # DOWNLOAD ORIGINAL
    # ==========================================================

    print(
        "DECISION DOCUMENT: "
        "Downloading original PDF..."
    )

    original_pdf = _download_original_pdf(
        original_file_url
    )

    print(
        "DECISION DOCUMENT: "
        f"Original PDF downloaded "
        f"({len(original_pdf):,} bytes)"
    )

    # ==========================================================
    # CREATE STAMP
    # ==========================================================

    print(
        "DECISION DOCUMENT: "
        f"Creating {display_decision} stamp..."
    )

    stamp_pdf = _create_decision_stamp(
        decision=normalized_decision,
        request_number=request_number,
        manager_name=manager_name,
        manager_comment=manager_comment,
        decision_date=decision_date
    )

    # ==========================================================
    # APPLY STAMP
    # ==========================================================

    print(
        "DECISION DOCUMENT: "
        "Applying stamp to original PDF..."
    )

    decision_pdf = _apply_stamp_to_pdf(
        original_pdf_bytes=original_pdf,
        stamp_pdf_bytes=stamp_pdf
    )

    print(
        "DECISION DOCUMENT: "
        f"Generated PDF "
        f"({len(decision_pdf):,} bytes)"
    )

    # ==========================================================
    # CREATE SAFE FILE NAME
    # ==========================================================

    safe_request_number = (
        str(request_number or "material-request")
        .strip()
        .replace("/", "-")
        .replace("\\", "-")
        .replace(" ", "-")
    )

    decision_file_name = (
        f"{safe_request_number}_"
        f"{normalized_decision.upper()}.pdf"
    )

    # ==========================================================
    # CLOUDINARY PUBLIC ID
    # ==========================================================

    public_id = (
        f"{DOCUMENT_FOLDER}/"
        f"{safe_request_number}_"
        f"{normalized_decision.upper()}"
    )

    # ==========================================================
    # UPLOAD
    # ==========================================================

    print(
        "DECISION DOCUMENT: "
        "Uploading generated PDF to Cloudinary..."
    )

    upload_result = _upload_decision_pdf(
        pdf_bytes=decision_pdf,
        public_id=public_id
    )

    # ==========================================================
    # GET URL
    # ==========================================================

    decision_file_url = (
        upload_result.get(
            "secure_url"
        )
        or upload_result.get(
            "url"
        )
    )

    if not decision_file_url:

        raise RuntimeError(
            "Cloudinary uploaded the decision document but "
            "did not return a usable URL."
        )

    # ==========================================================
    # SUCCESS
    # ==========================================================

    print(
        "=============================================="
    )

    print(
        "DECISION DOCUMENT GENERATED SUCCESSFULLY"
    )

    print(
        "Request Number:",
        request_number
    )

    print(
        "Decision:",
        display_decision
    )

    print(
        "Manager:",
        manager_name
    )

    print(
        "File:",
        decision_file_name
    )

    print(
        "Cloudinary URL:",
        decision_file_url
    )

    print(
        "=============================================="
    )

    return {

        "success": True,

        "decision": display_decision,

        "file_name": decision_file_name,

        "file_url": decision_file_url,

        "public_id": public_id,

        "original_file_name": (
            original_file_name
        ),

        "original_file_url": (
            original_file_url
        )
    }