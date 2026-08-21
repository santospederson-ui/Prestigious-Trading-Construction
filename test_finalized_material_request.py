"""
==============================================================
STAGE 3C
FINALIZED MATERIAL REQUEST DOCUMENT TEST
==============================================================

Purpose:
    Independently test the finalized material-request PDF system.

This test:
    1. Downloads an existing Cloudinary PDF.
    2. Creates an APPROVED copy with a management stamp.
    3. Saves the new PDF locally.
    4. Uploads the finalized PDF to Cloudinary.
    5. Does NOT modify the original PDF.
    6. Does NOT touch the database.
    7. Does NOT modify app.py.
    8. Does NOT modify the material-request route.

After the approval test succeeds, change TEST_DECISION
to "declined" and run it again to test the decline reason.
"""

from pathlib import Path

from construction_document_storage import (
    create_and_upload_finalized_document
)


# ==============================================================
# TEST CONFIGURATION
# ==============================================================

# IMPORTANT:
# Replace this with the ACTUAL Cloudinary URL of an existing
# material-request PDF.
#
# Use the file_url stored in:
#
# construction_purchase_material_requests
#
# Do NOT use the finalized URL.

ORIGINAL_PDF_URL = (
    "PASTE_YOUR_EXISTING_CLOUDINARY_PDF_URL_HERE"
)


# ==============================================================
# MATERIAL REQUEST INFORMATION
# ==============================================================

TEST_REQUEST_NUMBER = (
    "MR-20260819-16D4C4"
)

TEST_MANAGER_NAME = (
    "Test Manager"
)


# ==============================================================
# TEST DECISION
#
# First test:
#
#     approved
#
# Then change to:
#
#     declined
#
# and test again.
# ==============================================================

TEST_DECISION = "approved"


# ==============================================================
# DECLINE REASON
#
# Only used when TEST_DECISION = "declined"
# ==============================================================

TEST_MANAGER_COMMENT = (
    "Test decline reason - supplier price requires review."
)


# ==============================================================
# MAIN TEST
# ==============================================================

def main():

    print()
    print("=" * 70)
    print("STAGE 3C - FINALIZED MATERIAL REQUEST DOCUMENT TEST")
    print("=" * 70)

    print()
    print("Original PDF URL:")
    print(ORIGINAL_PDF_URL)

    print()
    print("Request Number:")
    print(TEST_REQUEST_NUMBER)

    print()
    print("Decision:")
    print(TEST_DECISION)

    print()
    print("Manager:")
    print(TEST_MANAGER_NAME)

    print()
    print("Manager Comment:")
    print(
        TEST_MANAGER_COMMENT
        if TEST_DECISION.lower() in (
            "declined",
            "decline"
        )
        else "Not applicable"
    )

    print()
    print("-" * 70)

    # ==========================================================
    # BASIC URL CHECK
    # ==========================================================

    if not ORIGINAL_PDF_URL:

        print()
        print(
            "ERROR: ORIGINAL_PDF_URL is empty."
        )

        return

    if (
        "PASTE_YOUR_EXISTING"
        in ORIGINAL_PDF_URL
    ):

        print()
        print(
            "ERROR: You have not replaced "
            "ORIGINAL_PDF_URL."
        )

        return

    # ==========================================================
    # GENERATE + UPLOAD
    # ==========================================================

    try:

        print()
        print(
            "Downloading original PDF..."
        )

        print(
            "Generating finalized PDF..."
        )

        print(
            "Uploading finalized PDF to Cloudinary..."
        )

        result = (
            create_and_upload_finalized_document(

                original_pdf_url=(
                    ORIGINAL_PDF_URL
                ),

                decision=(
                    TEST_DECISION
                ),

                request_number=(
                    TEST_REQUEST_NUMBER
                ),

                manager_name=(
                    TEST_MANAGER_NAME
                ),

                manager_comment=(
                    TEST_MANAGER_COMMENT
                ),

                decision_date=None
            )
        )

        print()
        print("=" * 70)
        print("FINALIZED DOCUMENT CREATED SUCCESSFULLY")
        print("=" * 70)

        print()
        print("Decision:")
        print(
            result.get(
                "decision"
            )
        )

        print()
        print("Request Number:")
        print(
            result.get(
                "request_number"
            )
        )

        print()
        print("Cloudinary Public ID:")
        print(
            result.get(
                "public_id"
            )
        )

        print()
        print("Cloudinary URL:")
        print(
            result.get(
                "secure_url"
            )
        )

        print()
        print("Filename:")
        print(
            result.get(
                "filename"
            )
        )

        # ======================================================
        # SAVE LOCAL COPY
        # ======================================================

        pdf_bytes = result.get(
            "pdf_bytes"
        )

        if pdf_bytes:

            output_directory = Path(
                "test_output"
            )

            output_directory.mkdir(
                parents=True,
                exist_ok=True
            )

            decision_name = (
                str(
                    TEST_DECISION
                )
                .strip()
                .upper()
            )

            output_file = (
                output_directory
                / (
                    f"{TEST_REQUEST_NUMBER}_"
                    f"{decision_name}_TEST.pdf"
                )
            )

            with open(
                output_file,
                "wb"
            ) as file:

                file.write(
                    pdf_bytes
                )

            print()
            print("Local Test PDF:")
            print(
                output_file.resolve()
            )

            print()
            print(
                "PDF size:"
            )

            print(
                f"{len(pdf_bytes):,} bytes"
            )

        print()
        print("=" * 70)
        print("STAGE 3C TEST COMPLETE")
        print("=" * 70)

        print()
        print(
            "IMPORTANT:"
        )

        print(
            "The original PDF was NOT modified."
        )

        print(
            "A completely new finalized PDF was created."
        )

        print(
            "The finalized PDF was uploaded separately "
            "to Cloudinary."
        )

        print()

    except Exception as error:

        print()
        print("=" * 70)
        print("STAGE 3C TEST FAILED")
        print("=" * 70)

        print()
        print(
            "Error type:"
        )

        print(
            type(error).__name__
        )

        print()
        print(
            "Error:"
        )

        print(
            repr(error)
        )

        print()
        print(
            "The existing material-request system "
            "was not modified by this test."
        )

        print()


# ==============================================================
# RUN
# ==============================================================

if __name__ == "__main__":

    main()