"""
==============================================================
CONSTRUCTION DOCUMENT STORAGE
==============================================================

Purpose:
    Upload finalized construction procurement PDFs to Cloudinary.

Important:
    - Original uploaded documents are NEVER overwritten.
    - Finalized documents receive their own Cloudinary public ID.
    - This module is independent from Flask routes.
    - Uses the existing Cloudinary configuration from app.py.
"""

import io
from datetime import datetime

import cloudinary
import cloudinary.uploader


# ==============================================================
# CLOUDINARY
# ==============================================================

def configure_cloudinary():
    """
    Verify that Cloudinary has already been configured by the
    main Construction application.

    IMPORTANT:
        We intentionally DO NOT call cloudinary.config() here.

    The existing app.py already contains the working Cloudinary
    configuration used by the rest of the Construction system.

    This prevents this independent module from accidentally
    changing or breaking the existing Cloudinary configuration.
    """

    current_config = cloudinary.config()

    if not current_config.cloud_name:

        raise RuntimeError(
            "Cloudinary is not configured. "
            "The main Construction application must configure "
            "Cloudinary before using the document storage module."
        )

    return current_config


# ==============================================================
# CREATE UNIQUE PUBLIC ID
# ==============================================================

def create_finalized_public_id(
    request_number,
    decision
):
    """
    Create a unique Cloudinary public ID for the finalized PDF.

    Example:

        construction/procurement/finalized/
        MR-20260819-16D4C4_APPROVED_20260819_201530
    """

    safe_request_number = (
        str(
            request_number
            or "material_request"
        )
        .strip()
        .replace(
            " ",
            "_"
        )
    )

    decision_name = (
        str(
            decision
            or "decision"
        )
        .strip()
        .upper()
    )

    timestamp = datetime.now().strftime(
        "%Y%m%d_%H%M%S_%f"
    )

    return (
        "construction/"
        "procurement/"
        "finalized/"
        f"{safe_request_number}_"
        f"{decision_name}_"
        f"{timestamp}"
    )


# ==============================================================
# UPLOAD FINALIZED PDF
# ==============================================================

def upload_finalized_material_request(
    pdf_bytes,
    request_number,
    decision
):
    """
    Upload a finalized PDF to Cloudinary.

    Returns a dictionary containing:

        public_id
        secure_url
        resource_type
        original_filename
    """

    if not pdf_bytes:

        raise ValueError(
            "Cannot upload an empty PDF."
        )

    # ==========================================================
    # VERIFY EXISTING CLOUDINARY CONFIGURATION
    # ==========================================================

    configure_cloudinary()

    # ==========================================================
    # NORMALIZE DECISION
    # ==========================================================

    decision = (
        decision
        or ""
    ).strip().lower()

    if decision in (
        "approve",
        "approved"
    ):

        decision_name = "APPROVED"

    elif decision in (
        "decline",
        "declined"
    ):

        decision_name = "DECLINED"

    else:

        raise ValueError(
            "Invalid material request decision."
        )

    # ==========================================================
    # UNIQUE CLOUDINARY PUBLIC ID
    # ==========================================================

    public_id = (
        create_finalized_public_id(
            request_number=request_number,
            decision=decision_name
        )
    )

    # ==========================================================
    # FILE NAME
    # ==========================================================

    filename = (
        f"{request_number}_"
        f"{decision_name}.pdf"
    )

    # ==========================================================
    # UPLOAD
    # ==========================================================

    result = cloudinary.uploader.upload(
        io.BytesIO(
            pdf_bytes
        ),
        resource_type="raw",
        public_id=public_id,
        format="pdf",
        type="upload",
        overwrite=False,
        use_filename=False,
        unique_filename=False
    )

    # ==========================================================
    # VALIDATE RESULT
    # ==========================================================

    secure_url = (
        result.get(
            "secure_url"
        )
        or result.get(
            "url"
        )
    )

    if not secure_url:

        raise RuntimeError(
            "Cloudinary did not return a valid "
            "URL for the finalized PDF."
        )

    return {

        "public_id": result.get(
            "public_id"
        ),

        "secure_url": secure_url,

        "resource_type": result.get(
            "resource_type",
            "raw"
        ),

        "original_filename": filename,

        "decision": decision_name,

        "request_number": request_number
    }


# ==============================================================
# COMPLETE FINALIZED DOCUMENT PROCESS
# ==============================================================

def create_and_upload_finalized_document(
    original_pdf_url,
    decision,
    request_number,
    manager_name,
    manager_comment=None,
    decision_date=None
):
    """
    Complete independent workflow:

        Original Cloudinary PDF
                    ↓
              Download PDF
                    ↓
             Add decision stamp
                    ↓
              Generate NEW PDF
                    ↓
             Upload NEW PDF
                    ↓
              Return new URL

    The original document is never modified.
    """

    # Import here to keep the modules loosely coupled.

    from construction_document_service import (
        generate_finalized_material_request_pdf_from_url
    )

    # ==========================================================
    # GENERATE FINALIZED PDF
    # ==========================================================

    finalized_pdf = (
        generate_finalized_material_request_pdf_from_url(

            file_url=original_pdf_url,

            decision=decision,

            request_number=request_number,

            manager_name=manager_name,

            manager_comment=manager_comment,

            decision_date=decision_date
        )
    )

    # ==========================================================
    # UPLOAD FINALIZED PDF
    # ==========================================================

    upload_result = (
        upload_finalized_material_request(

            pdf_bytes=finalized_pdf,

            request_number=request_number,

            decision=decision
        )
    )

    # ==========================================================
    # RETURN EVERYTHING NEEDED BY FUTURE ROUTE
    # ==========================================================

    return {

        "pdf_bytes": finalized_pdf,

        "public_id": upload_result.get(
            "public_id"
        ),

        "secure_url": upload_result.get(
            "secure_url"
        ),

        "resource_type": upload_result.get(
            "resource_type"
        ),

        "filename": upload_result.get(
            "original_filename"
        ),

        "decision": upload_result.get(
            "decision"
        ),

        "request_number": upload_result.get(
            "request_number"
        )
    }






# ==============================================================
# QUOTATION FINALIZED DOCUMENT
# ==============================================================

def create_and_upload_finalized_quotation_document(
    original_pdf_url,
    decision,
    quotation_number,
    supplier_name,
    manager_name,
    manager_comment=None,
    decision_date=None
):
    """
    Complete quotation finalization workflow.

    Workflow:

        Original supplier quotation
                    ↓
              Download PDF
                    ↓
             Add decision stamp
                    ↓
              Generate new PDF
                    ↓
             Upload to Cloudinary
                    ↓
              Return new URL

    The original quotation is NEVER modified.
    """

    # ----------------------------------------------------------
    # Import document generation service locally
    # ----------------------------------------------------------

    from construction_document_service import (
        generate_finalized_material_request_pdf_from_url
    )

    # ==========================================================
    # GENERATE FINALIZED PDF
    # ==========================================================

    finalized_pdf = (
        generate_finalized_material_request_pdf_from_url(

            file_url=original_pdf_url,

            decision=decision,

            request_number=quotation_number,

            manager_name=manager_name,

            manager_comment=manager_comment,

            decision_date=decision_date
        )
    )

    # ==========================================================
    # NORMALIZE DECISION
    # ==========================================================

    decision_normalized = (
        str(
            decision or ""
        )
        .strip()
        .lower()
    )

    if decision_normalized in (
        "approve",
        "approved"
    ):

        decision_name = "APPROVED"

    elif decision_normalized in (
        "decline",
        "declined"
    ):

        decision_name = "DECLINED"

    else:

        raise ValueError(
            "Invalid quotation decision."
        )

    # ==========================================================
    # CREATE UNIQUE CLOUDINARY PUBLIC ID
    # ==========================================================

    public_id = create_finalized_public_id(

        request_number=quotation_number,

        decision=decision_name
    )

    # ==========================================================
    # CONFIGURE CLOUDINARY
    # ==========================================================

    configure_cloudinary()

    # ==========================================================
    # FILE NAME
    # ==========================================================

    safe_supplier = (
        str(
            supplier_name or "supplier"
        )
        .strip()
        .replace(
            " ",
            "_"
        )
    )

    filename = (
        f"{quotation_number}_"
        f"{safe_supplier}_"
        f"{decision_name}.pdf"
    )

    # ==========================================================
    # UPLOAD FINALIZED QUOTATION
    # ==========================================================

    result = cloudinary.uploader.upload(

        io.BytesIO(
            finalized_pdf
        ),

        resource_type="raw",

        public_id=public_id,

        format="pdf",

        type="upload",

        overwrite=False,

        use_filename=False,

        unique_filename=False
    )

    # ==========================================================
    # GET CLOUDINARY URL
    # ==========================================================

    secure_url = (
        result.get(
            "secure_url"
        )
        or result.get(
            "url"
        )
    )

    if not secure_url:

        raise RuntimeError(
            "Cloudinary did not return a valid URL "
            "for the finalized quotation."
        )

    # ==========================================================
    # RETURN RESULT
    # ==========================================================

    return {

        "pdf_bytes": finalized_pdf,

        "public_id": result.get(
            "public_id"
        ),

        "secure_url": secure_url,

        "resource_type": result.get(
            "resource_type",
            "raw"
        ),

        "filename": filename,

        "decision": decision_name,

        "quotation_number": quotation_number,

        "supplier_name": supplier_name
    }