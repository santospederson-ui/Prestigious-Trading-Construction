import io
import os
import uuid
from datetime import timedelta
from zoneinfo import ZoneInfo

import cloudinary
import cloudinary.uploader
import mysql.connector
import requests
from dotenv import load_dotenv
from flask import Flask, render_template, redirect, url_for, flash, session, request, jsonify
from flask import Response, send_from_directory
from werkzeug.security import check_password_hash
from werkzeug.utils import secure_filename

from decimal import Decimal, InvalidOperation


import base64
from io import BytesIO
from datetime import datetime
import fitz  # PyMuPDF
from PIL import Image, ImageDraw, ImageFont




load_dotenv()




app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "super-secret-key")

app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(minutes=30)








# ==========================================================
# CONSTRUCTION SYSTEM TIME
# ==========================================================

QATAR_TIMEZONE = ZoneInfo("Asia/Qatar")


def get_qatar_now():
    """
    Returns the current date/time in Qatar.

    This avoids depending on the timezone configured on
    the hosting server.
    """
    return datetime.now(QATAR_TIMEZONE)






# Helper for timezone-aware default timestamps if needed
try:
    from app import get_qatar_now
except ImportError:
    def get_qatar_now():
        return datetime.now()


# ==========================================================
# ADD DIGITAL SIGNATURE TO PDF (WITH OFFICIAL STAMP BOX)
# ==========================================================

def create_signed_pdf(
    pdf_bytes,
    signature_data,
    signer_name=None,
    department_title="PURCHASE DEPARTMENT",
    position="left"  # Options: 'left', 'center', 'right'
):
    """
    Creates an official approval stamp containing a department header,
    the raw signature image, and a stamped date/signer footer, then
    embeds it at the designated position (left, center, right) on every PDF page.

    The original PDF is not modified in memory permanently.
    A new signed PDF is returned as bytes.
    """

    if not pdf_bytes:
        raise ValueError("PDF data is empty.")

    if not signature_data:
        raise ValueError("Signature data is empty.")

    # ------------------------------------------------------
    # Extract Base64 portion from data URL
    # ------------------------------------------------------

    if "," in signature_data:
        signature_base64 = signature_data.split(",", 1)[1]
    else:
        signature_base64 = signature_data

    try:
        signature_bytes = base64.b64decode(signature_base64)
    except Exception as e:
        raise ValueError("Invalid digital signature data.") from e

    # ------------------------------------------------------
    # Open raw signature image
    # ------------------------------------------------------

    try:
        raw_signature_image = Image.open(BytesIO(signature_bytes)).convert("RGBA")
    except Exception as e:
        raise ValueError("Unable to read the digital signature image.") from e

    # ------------------------------------------------------
    # BUILD COMPOSITE STAMP CANVAS (PIL)
    # ------------------------------------------------------

    try:
        # Standardize raw signature size inside stamp
        raw_signature_image.thumbnail((220, 60), Image.Resampling.LANCZOS)

        padding = 10
        stamp_width = max(raw_signature_image.width + (padding * 2), 260)
        stamp_height = raw_signature_image.height + 65  # Header + Signature + Footer

        # Create transparent canvas
        stamp_canvas = Image.new("RGBA", (stamp_width, stamp_height), (255, 255, 255, 0))
        draw = ImageDraw.Draw(stamp_canvas)

        # Border and soft background fill (Official Dark Navy)
        border_color = (0, 51, 102, 255)
        bg_fill = (250, 252, 255, 240)

        draw.rectangle(
            [(0, 0), (stamp_width - 1, stamp_height - 1)],
            outline=border_color,
            fill=bg_fill,
            width=2
        )

        # Load fonts safely
        try:
            font_header = ImageFont.truetype("arial.ttf", 11)
            font_footer = ImageFont.truetype("arial.ttf", 9)
        except OSError:
            font_header = ImageFont.load_default()
            font_footer = ImageFont.load_default()

        # 1. Header: Department Stamped Title
        header_text = f"APPROVED: {department_title.upper()}"
        draw.text(
            (padding, padding),
            header_text,
            fill=border_color,
            font=font_header
        )

        # Divider line
        draw.line(
            [(padding, padding + 15), (stamp_width - padding, padding + 15)],
            fill=border_color,
            width=1
        )

        # 2. Paste Signature centered inside canvas
        sig_pos_x = (stamp_width - raw_signature_image.width) // 2
        sig_pos_y = padding + 18

        stamp_canvas.paste(
            raw_signature_image,
            (sig_pos_x, sig_pos_y),
            raw_signature_image
        )

        # 3. Footer: Stamped Execution Date & Signer Name
        stamped_date = get_qatar_now().strftime("%Y-%m-%d %H:%M AST")
        signer_label = signer_name or "Authorized Officer"
        footer_text = f"By: {signer_label} | Date: {stamped_date}"

        draw.text(
            (padding, stamp_height - padding - 10),
            footer_text,
            fill=(60, 60, 60, 255),
            font=font_footer
        )

        # Save composite stamp image to PNG bytes
        stamp_buffer = BytesIO()
        stamp_canvas.save(stamp_buffer, format="PNG")
        stamp_png = stamp_buffer.getvalue()

    except Exception as e:
        raise ValueError("Failed to construct the approval stamp graphic.") from e

    # ------------------------------------------------------
    # Open PDF
    # ------------------------------------------------------

    pdf_document = fitz.open(stream=pdf_bytes, filetype="pdf")

    try:
        # --------------------------------------------------
        # Process every PDF page
        # --------------------------------------------------

        for page in pdf_document:
            page_rect = page.rect
            page_width = page_rect.width
            page_height = page_rect.height

            # ------------------------------------------------
            # Stamp Dimensions on PDF
            # ------------------------------------------------

            max_stamp_width = 170  # Standardized width for 3-across layout
            max_stamp_height = (stamp_height / stamp_width) * max_stamp_width

            margin = 25  # Side padding from page edge
            bottom_margin = 30  # Bottom padding from page edge

            # ------------------------------------------------
            # Horizontal Position Calculation (Left / Center / Right)
            # ------------------------------------------------

            if position == "left":
                x0 = margin
            elif position == "right":
                x0 = page_width - margin - max_stamp_width
            else:  # 'center'
                x0 = (page_width - max_stamp_width) / 2

            y1 = page_height - bottom_margin
            y0 = y1 - max_stamp_height

            # Ensure stamp remains inside page bounds
            if y0 < 10:
                y0 = 10
                y1 = y0 + max_stamp_height

            stamp_rect = fitz.Rect(
                x0,
                y0,
                x0 + max_stamp_width,
                y1
            )

            # ------------------------------------------------
            # Add Composite Stamp
            # ------------------------------------------------

            page.insert_image(
                stamp_rect,
                stream=stamp_png,
                keep_proportion=True,
                overlay=True
            )

        # --------------------------------------------------
        # Save signed PDF
        # --------------------------------------------------

        output_buffer = BytesIO()
        pdf_document.save(
            output_buffer,
            garbage=4,
            deflate=True
        )

        return output_buffer.getvalue()

    finally:
        pdf_document.close()

        # =========================================================
        # CONSTRUCTION ROLE DASHBOARD ROUTER
        # =========================================================
        def construction_role_dashboard():
            """
            Returns the correct dashboard URL for the currently
            authenticated construction admin role.

            Each construction role has its own independent dashboard.
            Unauthorized users are never redirected to another
            department's dashboard.
            """

            role = (
                    session.get("construction_admin_role") or ""
            ).strip().lower()

            dashboard_map = {
                "super_admin": "construction_admin_dashboard",
                "admin": "construction_admin_dashboard",

                "purchase": "construction_purchase_dashboard",

                "engineer": "construction_engineer_dashboard",

                "manager": "construction_manager_dashboard",

                "account": "construction_account_dashboard",
            }

            endpoint = dashboard_map.get(role)

            if endpoint:
                return url_for(endpoint)

            return url_for("admin_login")











# =========================================================
# ACCOUNT AUDIT TRAIL HELPER
# =========================================================

def _construction_log_account_action(
    cursor,
    action,
    description=None,
    material_request_id=None,
    procurement_id=None,
    financial_transaction_id=None,
    transaction_number=None,
    old_status=None,
    new_status=None,
    user_id=None,
    user_name=None,
    notes=None
):

    # =====================================================
    # USER DETAILS
    # =====================================================

    if user_id is None:

        user_id = session.get(
            "construction_admin_id"
        )

    if not user_name:

        user_name = (
            session.get(
                "construction_admin_name"
            )
            or "Account Officer"
        )

    # =====================================================
    # IP ADDRESS
    # =====================================================

    ip_address = (
        request.headers.get(
            "X-Forwarded-For"
        )
        or request.remote_addr
    )

    if ip_address:

        # If behind a proxy, only retain the first address.
        ip_address = (
            str(ip_address)
            .split(",")[0]
            .strip()
        )

    # =====================================================
    # INSERT AUDIT RECORD
    # =====================================================

    cursor.execute("""
        INSERT INTO construction_account_audit_logs
        (
            material_request_id,
            procurement_id,
            financial_transaction_id,

            user_id,
            user_name,

            action,
            description,

            old_status,
            new_status,

            transaction_number,

            ip_address,

            notes,

            created_at
        )

        VALUES
        (
            %s,
            %s,
            %s,

            %s,
            %s,

            %s,
            %s,

            %s,
            %s,

            %s,

            %s,

            %s,

            NOW()
        )
    """, (

        material_request_id,
        procurement_id,
        financial_transaction_id,

        user_id,
        user_name,

        action,
        description,

        old_status,
        new_status,

        transaction_number,

        ip_address,

        notes
    ))














# ============================================================
# CONSTRUCTION PROCUREMENT HELPERS
# ============================================================

def _construction_purchase_current_user():
    """
    Return the currently logged-in construction admin user's
    ID, name and role.
    """

    user_id = session.get("construction_admin_id")

    if not user_id:
        return None, "", ""

    user_name = (
        session.get("construction_admin_name")
        or session.get("name")
        or session.get("full_name")
        or session.get("username")
        or "Unknown User"
    )

    role = (
        session.get("construction_admin_role")
        or ""
    ).strip().lower()

    return user_id, user_name, role


def _construction_log_procurement_action(
    cursor,
    procurement_id,
    action_type,
    action_description=None,
    performed_by=None,
    performed_by_name=None,
    from_status=None,
    to_status=None,

    notes=None
):
    """
    Write one immutable audit entry for a procurement action.
    """

    cursor.execute(
        """
        INSERT INTO construction_purchase_procurement_actions (
            procurement_id,
            action_type,
            action_description,
            from_status,
            to_status,
            performed_by,
            performed_by_name,
            performed_at,
            notes
        )
        VALUES (
            %s, %s, %s, %s, %s,
            %s, %s, NOW(), %s
        )
        """,
        (
            procurement_id,
            action_type,
            action_description,
            from_status,
            to_status,
            performed_by,
            performed_by_name,
            notes
        )
    )


def _construction_parse_amount(value):
    """
    Safely convert a submitted amount to Decimal.
    """

    if value is None:
        return None

    value = str(value).strip().replace(",", "")

    if not value:
        return None

    try:
        amount = Decimal(value)

        if amount < 0:
            return None

        return amount

    except (InvalidOperation, ValueError):
        return None


def _construction_allowed_procurement_method(value):
    """
    Normalize procurement method.
    """

    value = (value or "").strip().lower()

    if value == "card":
        return "Card"

    if value in ("quotation", "quotation/lpo", "quotation_lpo"):
        return "Quotation"

    return None


def _construction_procurement_document_folder(
    request_number,
    procurement_id
):
    """
    Cloudinary folder used for procurement documents.
    """

    safe_request_number = "".join(
        char if char.isalnum() or char in "-_" else "_"
        for char in str(request_number)
    )

    return (
        f"construction/procurement/"
        f"{safe_request_number}/"
        f"procurement_{procurement_id}"
    )

















# =========================================================
# CONSTRUCTION ROLE DASHBOARD ROUTER
# =========================================================
def construction_role_dashboard():
    """
    Returns the correct dashboard URL for the currently
    authenticated construction admin role.

    Each construction role has its own independent dashboard.
    Unauthorized users are never redirected to another
    department's dashboard.
    """

    role = (
        session.get("construction_admin_role") or ""
    ).strip().lower()

    dashboard_map = {
        "super_admin": "construction_admin_dashboard",
        "admin": "construction_admin_dashboard",

        "purchase": "construction_purchase_dashboard",

        "engineer": "construction_engineer_dashboard",

        "manager": "construction_manager_dashboard",

        "account": "construction_account_dashboard",
    }

    endpoint = dashboard_map.get(role)

    if endpoint:
        return url_for(endpoint)

    return url_for("admin_login")








# ==========================================
# CONSTRUCTION ADMIN SESSION TIMEOUT
# ==========================================
@app.before_request
def session_timeout():

    if request.endpoint == "static":
        return

    if "construction_admin_id" in session:

        now = datetime.utcnow()

        last_activity = session.get(
            "construction_last_activity"
        )

        if last_activity:

            last_activity_time = datetime.fromisoformat(
                last_activity
            )

            if now - last_activity_time > timedelta(minutes=30):

                session.clear()

                flash(
                    "Your session expired. Please login again.",
                    "warning"
                )

                return redirect(
                    url_for("admin_login")
                )

        session["construction_last_activity"] = now.isoformat()



# ==========================================
# MYSQL CONNECTION
# ==========================================
def get_db_connection():

    return mysql.connector.connect(
        host=os.getenv("DB_HOST"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        database=os.getenv("DB_NAME"),
        port=int(os.getenv("DB_PORT", 3306))
    )





# ==========================================
# CLOUDINARY
# ==========================================

CLOUDINARY_CLOUD_NAME="da8y4zqz5"
CLOUDINARY_API_KEY="551545451643298"
CLOUDINARY_API_SECRET="CtN8D84Db81NFkhUwGUm8W2cvEU"


cloudinary.config(
    cloud_name=CLOUDINARY_CLOUD_NAME,
    api_key=CLOUDINARY_API_KEY,
    api_secret=CLOUDINARY_API_SECRET
)




# ==========================================
# ROBOTS GOOGLE
# ==========================================
@app.route("/robots.txt")
def robots():

    return send_from_directory(
        "static",
        "robots.txt",
        mimetype="text/plain"
    )







# ==========================================
# SITEMAP GOOGLE
# ==========================================

@app.route("/sitemap.xml")
def sitemap():

    pages = []

    # =========================
    # STATIC WEBSITE PAGES
    # =========================

    pages.append(
        url_for("home", _external=True)
    )

    pages.append(
        url_for("about", _external=True)
    )

    pages.append(
        url_for("projects", _external=True)
    )

    pages.append(
        url_for("services", _external=True)
    )

    pages.append(
        url_for("contact", _external=True)
    )


    # =========================
    # PROJECT DETAILS PAGES
    # =========================

    conn = None
    cursor = None

    try:

        conn = get_db_connection()

        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT
                slug
            FROM construction_projects
            WHERE slug IS NOT NULL
              AND slug != ''
            ORDER BY created_at DESC
        """)

        projects = cursor.fetchall()


        for project in projects:

            pages.append(
                url_for(
                    "project_details",
                    slug=project["slug"],
                    _external=True
                )
            )


    except Exception as e:

        print(
            "SITEMAP ERROR:",
            e
        )


    finally:

        if cursor:
            cursor.close()

        if conn:
            conn.close()


    # =========================
    # GENERATE XML
    # =========================

    xml = render_template(
        "sitemap.xml",
        pages=pages
    )


    return Response(
        xml,
        mimetype="application/xml"
    )








# ==========================================
# SEND EMAIL USING BREVO SMTP
# ==========================================

def send_email(to_email, subject, html_message):

    print("******** BREVO API EMAIL START ********")

    try:

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT *
            FROM construction_email_settings
            LIMIT 1
        """)

        settings = cursor.fetchone()

        cursor.close()
        conn.close()

        if not settings:

            print("ERROR: Email settings not configured.")
            return False

        api_key = settings["smtp_password"]

        sender_email = settings["from_email"]

        sender_name = settings["sender_name"]


        headers = {
            "accept": "application/json",
            "api-key": api_key,
            "content-type": "application/json"
        }


        payload = {

            "sender": {
                "name": sender_name,
                "email": sender_email
            },

            "to": [
                {
                    "email": to_email
                }
            ],

            "subject": subject,

            "htmlContent": html_message

        }


        response = requests.post(

            "https://api.brevo.com/v3/smtp/email",

            headers=headers,

            json=payload,

            timeout=30

        )


        print("Brevo Status:", response.status_code)
        print("Brevo Response:", response.text)


        if response.status_code in [200, 201]:

            print("EMAIL SENT SUCCESSFULLY")

            return True

        else:

            print("EMAIL FAILED")

            return False


    except Exception as e:

        print("BREVO EMAIL ERROR")

        print(type(e).__name__)

        print(e)

        return False




# =====================================
# TEST BREVO
# =====================================
@app.route("/test-brevo")
def test_brevo():

    result = send_email(

        "santospederson@gmail.com",

        "Prestigious Trading & Constructions Email Test",

        """
        <h2>Email Test</h2>
        <p>Brevo SMTP is working.</p>
        """

    )

    print("TEST EMAIL RESULT:", result)

    return str(result)








# =====================================================
# CONSTRUCTION EMAIL SETTINGS
# =====================================================

@app.route("/construction/admin/email-settings", methods=["GET", "POST"])
def construction_email_settings():

    # ==========================
    # CHECK CONSTRUCTION ADMIN
    # ==========================

    if "construction_admin_id" not in session:

        return redirect(
            url_for("admin_login")
        )


    conn = None
    cursor = None


    try:

        conn = get_db_connection()

        cursor = conn.cursor(dictionary=True)


        # =================================================
        # SAVE SETTINGS
        # =================================================

        if request.method == "POST":

            smtp_server = request.form.get(
                "smtp_server",
                "smtp-relay.brevo.com"
            ).strip()


            smtp_port = request.form.get(
                "smtp_port",
                "587"
            ).strip()


            smtp_username = request.form.get(
                "smtp_username",
                ""
            ).strip()


            smtp_password = request.form.get(
                "smtp_password",
                ""
            ).strip()


            from_email = request.form.get(
                "from_email",
                ""
            ).strip()


            sender_name = request.form.get(
                "sender_name",
                "Prestigious Trading & Constructions"
            ).strip()


            use_tls = (
                1
                if request.form.get("use_tls")
                else 0
            )


            # ==========================
            # BASIC VALIDATION
            # ==========================

            if not from_email:

                flash(
                    "Sender email is required.",
                    "danger"
                )

                return redirect(
                    url_for("construction_email_settings")
                )


            # =================================================
            # CHECK EXISTING SETTINGS
            # =================================================

            cursor.execute("""
                SELECT
                    id,
                    smtp_password
                FROM construction_email_settings
                LIMIT 1
            """)

            existing = cursor.fetchone()


            # =================================================
            # UPDATE EXISTING SETTINGS
            # =================================================

            if existing:

                # Keep existing Brevo key if
                # administrator leaves password empty.

                if not smtp_password:

                    smtp_password = existing[
                        "smtp_password"
                    ]


                cursor.execute("""
                    UPDATE construction_email_settings

                    SET
                        smtp_server = %s,
                        smtp_port = %s,
                        smtp_username = %s,
                        smtp_password = %s,
                        from_email = %s,
                        sender_name = %s,
                        use_tls = %s

                    WHERE id = %s

                """, (

                    smtp_server,
                    smtp_port,
                    smtp_username,
                    smtp_password,
                    from_email,
                    sender_name,
                    use_tls,
                    existing["id"]

                ))


            # =================================================
            # INSERT FIRST SETTINGS
            # =================================================

            else:

                if not smtp_password:

                    flash(
                        "Brevo SMTP Key is required.",
                        "danger"
                    )

                    return redirect(
                        url_for("construction_email_settings")
                    )


                cursor.execute("""
                    INSERT INTO construction_email_settings
                    (
                        smtp_server,
                        smtp_port,
                        smtp_username,
                        smtp_password,
                        from_email,
                        sender_name,
                        use_tls
                    )

                    VALUES
                    (
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s
                    )

                """, (

                    smtp_server,
                    smtp_port,
                    smtp_username,
                    smtp_password,
                    from_email,
                    sender_name,
                    use_tls

                ))


            conn.commit()


            flash(
                "Construction email settings saved successfully.",
                "success"
            )


            return redirect(
                url_for("construction_email_settings")
            )


        # =================================================
        # LOAD SETTINGS
        # =================================================

        cursor.execute("""
            SELECT *
            FROM construction_email_settings
            LIMIT 1
        """)

        settings = cursor.fetchone()


        return render_template(
            "construction_admin/email_settings.html",
            settings=settings
        )


    except Exception as e:

        if conn:

            conn.rollback()


        print(
            "========================================"
        )

        print(
            "CONSTRUCTION EMAIL SETTINGS ERROR"
        )

        print(
            type(e).__name__
        )

        print(
            str(e)
        )

        print(
            "========================================"
        )


        flash(
            "Unable to save email settings.",
            "danger"
        )


        return redirect(
            url_for("construction_email_settings")
        )


    finally:

        if cursor:

            cursor.close()

        if conn:

            conn.close()




# =====================================
# GET CONSTRUCTION EMAIL SETTINGS
# =====================================

def get_construction_email_settings():

    try:

        conn = get_db_connection()

        cursor = conn.cursor(dictionary=True)

        cursor.execute(
            """
            SELECT *
            FROM construction_email_settings
            ORDER BY id ASC
            LIMIT 1
            """
        )

        settings = cursor.fetchone()

        cursor.close()
        conn.close()

        return settings

    except Exception as e:

        print("Construction Email Settings Error:", e)

        return None









# =========================================================
# QID EXPIRY MONITORING ENGINE
# =========================================================

def run_qid_expiry_monitoring():

    print("")
    print("=========================================================")
    print("STARTING CONSTRUCTION QID EXPIRY MONITORING")
    print("=========================================================")

    conn = None
    cursor = None

    results = {
        "checked": 0,
        "notifications_sent": 0,
        "already_sent": 0,
        "no_manager_email": 0,
        "no_staff_email": 0,
        "no_admin_email": 0,
        "errors": 0
    }

    try:

        # =================================================
        # DATABASE CONNECTION
        # =================================================

        conn = get_db_connection()

        cursor = conn.cursor(dictionary=True)


        # =================================================
        # GET ALL QID RECORDS
        # =================================================

        cursor.execute("""
            SELECT

                id,

                staff_name,

                qid_number,

                qid_issue_date,

                qid_expiry_date,

                staff_email,

                manager_name,

                manager_email,

                department,

                position,

                status

            FROM construction_staff_qids

            WHERE qid_expiry_date IS NOT NULL

            ORDER BY qid_expiry_date ASC

        """)

        qids = cursor.fetchall()


        today = datetime.utcnow().date()


        # =================================================
        # GET ADMIN EMAIL FROM EXISTING EMAIL SETTINGS
        # =================================================

        email_settings = get_construction_email_settings()


        admin_email = ""


        if email_settings:

            admin_email = (
                email_settings.get("from_email") or ""
            ).strip()


        if admin_email:

            print(
                "QID ADMIN EMAIL:",
                admin_email
            )

        else:

            print(
                "WARNING: No admin email found in "
                "construction_email_settings."
            )


        # =================================================
        # PROCESS EACH QID
        # =================================================

        for qid in qids:

            results["checked"] += 1


            # =================================================
            # BASIC DATA
            # =================================================

            qid_id = qid["id"]

            staff_name = (
                qid.get("staff_name")
                or "Staff Member"
            )

            expiry_date = qid["qid_expiry_date"]


            staff_email = (
                qid.get("staff_email")
                or ""
            ).strip()


            manager_email = (
                qid.get("manager_email")
                or ""
            ).strip()


            print("")
            print("-----------------------------------------")
            print("QID:", qid.get("qid_number"))
            print("Staff:", staff_name)
            print("Expiry:", expiry_date)


            # =================================================
            # CALCULATE DAYS REMAINING
            # =================================================

            days_remaining = (
                expiry_date - today
            ).days


            print(
                "Days Remaining:",
                days_remaining
            )


            # =================================================
            # DETERMINE NOTIFICATION TYPE
            # =================================================

            notification_type = None


            # -------------------------------------------------
            # EXPIRED
            # -------------------------------------------------

            if days_remaining < 0:

                notification_type = "expired"


            # -------------------------------------------------
            # 7 DAYS OR LESS
            # -------------------------------------------------

            elif days_remaining <= 7:

                notification_type = "7_days"


            # -------------------------------------------------
            # 30 DAYS OR LESS
            # -------------------------------------------------

            elif days_remaining <= 30:

                notification_type = "30_days"


            # -------------------------------------------------
            # 90 DAYS OR LESS
            # -------------------------------------------------

            elif days_remaining <= 90:

                notification_type = "90_days"


            # -------------------------------------------------
            # MORE THAN 90 DAYS
            # -------------------------------------------------

            else:

                print(
                    "Status: More than 90 days - no notification."
                )

                continue


            print(
                "Notification Type:",
                notification_type
            )


            # =================================================
            # BUILD NOTIFICATION CONTENT
            # =================================================

            if notification_type == "90_days":

                subject = (
                    f"QID Expiry Early Warning - "
                    f"{staff_name}"
                )

                heading = (
                    "QID Expiry Early Warning"
                )

                message = f"""
                <p>
                    This is an early warning that the
                    Qatar ID (QID) of the following staff
                    member will expire in approximately
                    <strong>90 days or less</strong>.
                </p>

                <p>
                    There are approximately
                    <strong>
                        {days_remaining}
                        days
                    </strong>
                    remaining.
                </p>
                """

                alert_color = "#3F6B57"


            elif notification_type == "30_days":

                subject = (
                    f"QID Expiry Warning - "
                    f"{staff_name}"
                )

                heading = (
                    "QID Expiry Warning"
                )

                message = f"""
                <p>
                    The Qatar ID (QID) of the following
                    staff member is approaching expiry.
                </p>

                <p>
                    There are approximately
                    <strong>
                        {days_remaining}
                        days
                    </strong>
                    remaining.
                </p>
                """

                alert_color = "#8A6A20"


            elif notification_type == "7_days":

                subject = (
                    f"URGENT: QID Expires Soon - "
                    f"{staff_name}"
                )

                heading = (
                    "Urgent QID Expiry Notification"
                )

                message = f"""
                <p>
                    <strong>
                        Immediate attention is required.
                    </strong>
                </p>

                <p>
                    The Qatar ID (QID) of the following
                    staff member will expire in
                    <strong>
                        {days_remaining}
                        day
                        {"s" if days_remaining != 1 else ""}
                    </strong>.
                </p>
                """

                alert_color = "#A85B22"


            else:

                subject = (
                    f"EXPIRED: Staff QID - "
                    f"{staff_name}"
                )

                heading = (
                    "QID Expired"
                )

                message = """
                <p>
                    <strong>
                        Immediate action is required.
                    </strong>
                </p>

                <p>
                    The Qatar ID (QID) of the following
                    staff member has already expired.
                </p>
                """

                alert_color = "#A63838"


            # =================================================
            # DATE FORMATTING
            # =================================================

            issue_date_text = (

                qid["qid_issue_date"].strftime(
                    "%d %B %Y"
                )

                if qid.get("qid_issue_date")

                else "N/A"

            )


            expiry_date_text = (

                expiry_date.strftime(
                    "%d %B %Y"
                )

                if expiry_date

                else "N/A"

            )


            days_remaining_text = (

                str(days_remaining)

                if days_remaining >= 0

                else "Expired"

            )


            # =================================================
            # COMPLETE EMAIL
            # =================================================

            html_message = f"""

            <div style="
                font-family:Arial,Helvetica,sans-serif;
                max-width:700px;
                margin:auto;
                color:#263238;
            ">

                <div style="
                    background:#234236;
                    padding:22px 25px;
                    color:#ffffff;
                ">

                    <h2 style="
                        margin:0;
                        font-size:20px;
                    ">

                        Prestigious Trading & Constructions

                    </h2>

                    <p style="
                        margin:5px 0 0;
                        color:#D9E7DE;
                        font-size:12px;
                    ">

                        Construction Administration

                    </p>

                </div>


                <div style="
                    padding:28px 25px;
                    border:1px solid #DCE7DE;
                    border-top:none;
                ">

                    <div style="
                        background:{alert_color};
                        color:#ffffff;
                        padding:12px 15px;
                        border-radius:6px;
                        font-weight:bold;
                        margin-bottom:22px;
                    ">

                        {heading}

                    </div>


                    {message}


                    <hr style="
                        border:none;
                        border-top:1px solid #E5E5E5;
                        margin:25px 0;
                    ">


                    <h3 style="
                        color:#3F6B57;
                        margin-bottom:15px;
                    ">

                        Staff Information

                    </h3>


                    <p>
                        <strong>Staff Name:</strong>
                        {staff_name}
                    </p>


                    <p>
                        <strong>QID Number:</strong>
                        {qid.get("qid_number") or "N/A"}
                    </p>


                    <p>
                        <strong>Department:</strong>
                        {qid.get("department") or "N/A"}
                    </p>


                    <p>
                        <strong>Position:</strong>
                        {qid.get("position") or "N/A"}
                    </p>


                    <p>
                        <strong>Manager:</strong>
                        {qid.get("manager_name") or "N/A"}
                    </p>


                    <p>
                        <strong>QID Issue Date:</strong>
                        {issue_date_text}
                    </p>


                    <p>
                        <strong>QID Expiry Date:</strong>
                        {expiry_date_text}
                    </p>


                    <p>
                        <strong>Days Remaining:</strong>
                        {days_remaining_text}
                    </p>


                    <hr style="
                        border:none;
                        border-top:1px solid #E5E5E5;
                        margin:25px 0;
                    ">


                    <p style="
                        color:#687A70;
                        font-size:12px;
                    ">

                        This notification was automatically
                        generated by the Prestigious Trading &
                        Constructions QID Expiry Monitoring System.

                    </p>


                </div>

            </div>

            """


            # =================================================
            # BUILD RECIPIENT LIST
            # =================================================

            recipients = []


            # =================================================
            # MANAGER
            # =================================================

            if manager_email:

                recipients.append({

                    "type": "manager",

                    "email": manager_email

                })

            else:

                print(
                    "WARNING: No manager email configured."
                )

                results["no_manager_email"] += 1


            # =================================================
            # STAFF
            # =================================================

            if staff_email:

                recipients.append({

                    "type": "staff",

                    "email": staff_email

                })

            else:

                print(
                    "WARNING: No staff email configured."
                )

                results["no_staff_email"] += 1


            # =================================================
            # ADMIN
            # =================================================

            if admin_email:

                recipients.append({

                    "type": "admin",

                    "email": admin_email

                })

            else:

                results["no_admin_email"] += 1


            # =================================================
            # PROCESS EACH RECIPIENT INDEPENDENTLY
            # =================================================

            for recipient in recipients:

                recipient_type = (
                    recipient["type"]
                )

                recipient_email = (
                    recipient["email"]
                )


                print("")
                print(
                    "Checking notification:",
                    recipient_type
                )

                print(
                    "Recipient:",
                    recipient_email
                )


                # =================================================
                # CHECK IF THIS RECIPIENT ALREADY RECEIVED THIS
                # NOTIFICATION
                # =================================================

                cursor.execute("""
                    SELECT
                        id

                    FROM construction_qid_notifications

                    WHERE qid_id = %s

                      AND notification_type = %s

                      AND expiry_date = %s

                      AND recipient_type = %s

                    LIMIT 1

                """, (

                    qid_id,

                    notification_type,

                    expiry_date,

                    recipient_type

                ))


                existing_notification = (
                    cursor.fetchone()
                )


                if existing_notification:

                    print(
                        "Notification already sent to",
                        recipient_type
                    )

                    results["already_sent"] += 1

                    continue


                # =================================================
                # SEND EMAIL
                # =================================================

                print(
                    "Sending notification to:",
                    recipient_email
                )


                email_sent = send_email(

                    recipient_email,

                    subject,

                    html_message

                )


                # =================================================
                # RECORD SUCCESSFUL EMAIL
                # =================================================

                if email_sent:

                    cursor.execute("""
                        INSERT INTO construction_qid_notifications
                        (
                            qid_id,
                            notification_type,
                            recipient_type,
                            expiry_date,
                            sent_to,
                            sent_at
                        )

                        VALUES
                        (
                            %s,
                            %s,
                            %s,
                            %s,
                            %s,
                            %s
                        )

                    """, (

                        qid_id,

                        notification_type,

                        recipient_type,

                        expiry_date,

                        recipient_email,

                        datetime.utcnow()

                    ))


                    conn.commit()


                    results["notifications_sent"] += 1


                    print(
                        "EMAIL SENT AND NOTIFICATION RECORDED."
                    )


                else:

                    print(
                        "EMAIL FAILED - "
                        "NOTIFICATION NOT RECORDED."
                    )

                    results["errors"] += 1


        # =================================================
        # FINAL RESULT
        # =================================================

        print("")
        print("=========================================================")
        print("QID EXPIRY MONITORING COMPLETED")
        print("=========================================================")

        print(
            "QIDs Checked:",
            results["checked"]
        )

        print(
            "Notifications Sent:",
            results["notifications_sent"]
        )

        print(
            "Already Sent:",
            results["already_sent"]
        )

        print(
            "Missing Manager Email:",
            results["no_manager_email"]
        )

        print(
            "Missing Staff Email:",
            results["no_staff_email"]
        )

        print(
            "Missing Admin Email:",
            results["no_admin_email"]
        )

        print(
            "Errors:",
            results["errors"]
        )

        print("=========================================================")


        return results


    # =====================================================
    # ERROR HANDLING
    # =====================================================

    except Exception as e:

        if conn:

            try:
                conn.rollback()

            except Exception:
                pass


        print("")
        print("=========================================================")
        print("QID EXPIRY MONITORING ERROR")
        print("=========================================================")

        print(
            "Error Type:",
            type(e).__name__
        )

        print(
            "Error:",
            str(e)
        )

        print("=========================================================")


        results["errors"] += 1


        return results


    finally:

        if cursor:

            try:
                cursor.close()

            except Exception:
                pass


        if conn:

            try:
                conn.close()

            except Exception:
                pass








# =========================================================
# TEST QID EXPIRY MONITORING
# =========================================================

@app.route("/test-qid-monitoring")
def test_qid_monitoring():

    # =====================================================
    # CONSTRUCTION ADMIN ONLY
    # =====================================================

    if "construction_admin_id" not in session:

        return redirect(
            url_for("admin_login")
        )


    # =====================================================
    # RUN MONITORING
    # =====================================================

    results = run_qid_expiry_monitoring()


    # =====================================================
    # SHOW RESULT
    # =====================================================

    return jsonify({

        "success": True,

        "message": (
            "QID expiry monitoring completed."
        ),

        "results": results

    })



# =========================================================
# AUTOMATIC QID MONITORING
# =========================================================

@app.route("/run-qid-monitoring", methods=["GET"])
def run_qid_monitoring():

    # =====================================================
    # GET SCHEDULER SECRET
    # =====================================================

    scheduler_key = os.getenv(
        "QID_MONITORING_SECRET",
        ""
    ).strip()


    # =====================================================
    # GET KEY FROM REQUEST
    # =====================================================

    request_key = request.args.get(
        "key",
        ""
    ).strip()


    # =====================================================
    # CHECK SECRET CONFIGURATION
    # =====================================================

    if not scheduler_key:

        print(
            "QID MONITORING ERROR: "
            "QID_MONITORING_SECRET is not configured."
        )

        return jsonify({
            "success": False
        }), 500


    # =====================================================
    # CHECK REQUEST KEY
    # =====================================================

    if not request_key:

        print(
            "QID MONITORING BLOCKED: "
            "No scheduler key supplied."
        )

        return jsonify({
            "success": False
        }), 401


    # =====================================================
    # VALIDATE REQUEST KEY
    # =====================================================

    if request_key != scheduler_key:

        print(
            "QID MONITORING BLOCKED: "
            "Invalid scheduler key."
        )

        return jsonify({
            "success": False
        }), 401


    # =====================================================
    # RUN MONITORING
    # =====================================================

    print("")
    print("=========================================================")
    print("AUTOMATIC QID MONITORING TRIGGERED")
    print("=========================================================")


    try:

        results = run_qid_expiry_monitoring()


        # =================================================
        # PRINT FULL RESULTS TO RENDER LOG
        # =================================================

        print("")
        print("=========================================================")
        print("AUTOMATIC QID MONITORING FINISHED")
        print("=========================================================")

        print(
            "Results:",
            results
        )

        print("=========================================================")


        # =================================================
        # SMALL RESPONSE FOR CRON-JOB.ORG
        # =================================================

        return jsonify({
            "success": True
        })


    except Exception as e:

        print("")
        print("=========================================================")
        print("AUTOMATIC QID MONITORING ERROR")
        print("=========================================================")

        print(
            "Error Type:",
            type(e).__name__
        )

        print(
            "Error:",
            str(e)
        )

        print("=========================================================")


        # =================================================
        # SMALL ERROR RESPONSE
        # =================================================

        return jsonify({
            "success": False
        }), 500







# =====================================================
# FILE UPLOAD ROUTE
# =====================================================
UPLOAD_FOLDER = "static/uploads/properties"

app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER


ALLOWED_EXTENSIONS = {
    "png",
    "jpg",
    "jpeg",
    "webp"
}



# =====================================================
# HELPER FUNCTION ROUTE
# =====================================================
def allowed_file(filename):

    return (
        "." in filename
        and
        filename.rsplit(".",1)[1].lower()
        in ALLOWED_EXTENSIONS
    )







# =====================================================
# HOMEPAGE ROUTE
# =====================================================
@app.route("/")
def home():

    conn = get_db_connection()

    cursor = conn.cursor(dictionary=True)

    cursor.execute("""
        SELECT *
        FROM construction_projects
        ORDER BY
            is_featured DESC,
            created_at DESC
        LIMIT 12
    """)

    projects = cursor.fetchall()

    cursor.close()
    conn.close()

    return render_template(
        "home.html",
        projects=projects
    )



# =====================================================
# ABOUT US ROUTE
# =====================================================
@app.route("/about")
def about():
    return render_template("about.html")







# =====================================================
# CONSTRUCTION PROJECTS ROUTE
# =====================================================
@app.route("/projects")
def projects():

    conn = None
    cursor = None

    try:

        conn = get_db_connection()

        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT *
            FROM construction_projects
            ORDER BY
                is_featured DESC,
                created_at DESC
        """)

        projects = cursor.fetchall()

        print("========================================")
        print("PROJECTS LOADED:", len(projects))
        print("========================================")

        return render_template(
            "projects.html",
            projects=projects
        )

    except Exception as e:

        print("========================================")
        print("PROJECTS PAGE ERROR")
        print(type(e).__name__)
        print(str(e))
        print("========================================")

        flash(
            "Unable to load projects.",
            "danger"
        )

        return render_template(
            "projects.html",
            projects=[]
        )

    finally:

        if cursor:
            cursor.close()

        if conn:
            conn.close()






# =====================================================
# PUBLIC CAREERS PAGE ROUTE
# =====================================================
@app.route("/careers")
def careers():

    conn = None
    cursor = None

    try:

        conn = get_db_connection()

        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT
                id,
                title,
                department,
                location,
                employment_type,
                experience,
                description,
                responsibilities,
                requirements,
                deadline,
                status,
                created_at,
                updated_at
            FROM construction_job_vacancies
            WHERE status = 'open'
            ORDER BY created_at DESC
        """)

        vacancies = cursor.fetchall()

        return render_template(
            "careers.html",
            vacancies=vacancies
        )

    except Exception as e:

        print("========================================")
        print("CAREERS PAGE ERROR")
        print(type(e).__name__)
        print(str(e))
        print("========================================")

        return render_template(
            "careers.html",
            vacancies=[]
        )

    finally:

        if cursor:
            cursor.close()

        if conn:
            conn.close()





# =====================================================
# SERVICES ROUTE
# =====================================================
@app.route("/services")
def services():
    return render_template("services.html")




# =====================================================
# MAINTENANCE ROUTE
# =====================================================
@app.route("/maintenance")
def maintenance():
    return render_template("maintenance.html")






# =====================================================
# WHY CHOOSE US ROUTE
# =====================================================
@app.route("/why-choose-us")
def why_choose_us():
    return render_template("why_choose_us.html")







# =====================================================
# CONSTRUCTION CONTACT ROUTE
# =====================================================
@app.route("/contact", methods=["GET", "POST"])
def contact():

    if request.method == "POST":

        # =========================
        # GET FORM DATA
        # =========================

        fullname = request.form.get("name", "").strip()
        phone = request.form.get("phone", "").strip()
        email = request.form.get("email", "").strip()
        service = request.form.get("service", "").strip()
        subject = request.form.get("subject", "").strip()
        message = request.form.get("message", "").strip()


        # =========================
        # BASIC VALIDATION
        # =========================

        if (
            not fullname
            or not phone
            or not email
            or not service
            or not subject
            or not message
        ):

            flash(
                "Please fill in all required fields.",
                "error"
            )

            return redirect(url_for("contact"))


        conn = None
        cursor = None

        try:

            # =========================
            # CONSTRUCTION DATABASE
            # =========================

            conn = get_db_connection()

            cursor = conn.cursor()


            # =========================
            # SAVE CONSTRUCTION MESSAGE
            # =========================

            cursor.execute(
                """
                INSERT INTO construction_contact_messages
                (
                    fullname,
                    email,
                    phone,
                    subject,
                    service,
                    message,
                    is_read
                )
                VALUES (%s, %s, %s, %s, %s, %s, 0)
                """,
                (
                    fullname,
                    email,
                    phone,
                    subject,
                    service,
                    message
                )
            )

            conn.commit()


            # =========================
            # CONSOLE CONFIRMATION
            # =========================

            print("========================================")
            print("CONSTRUCTION CONTACT MESSAGE SAVED")
            print("Name:", fullname)
            print("Email:", email)
            print("Phone:", phone)
            print("Service:", service)
            print("Subject:", subject)
            print("========================================")


            # =========================
            # ADMIN EMAIL
            # =========================

            email_result = send_email(

                "santospederson@gmail.com",

                "New Website Enquiry - Prestigious Trading & Constructions",

                f"""
                <h2>New Website Enquiry</h2>

                <hr>

                <p>
                    <b>Name:</b> {fullname}
                </p>

                <p>
                    <b>Email:</b> {email}
                </p>

                <p>
                    <b>Phone:</b> {phone}
                </p>

                <p>
                    <b>Service Required:</b> {service}
                </p>

                <p>
                    <b>Subject:</b> {subject}
                </p>

                <hr>

                <h3>Message</h3>

                <p>
                    {message}
                </p>

                <hr>

                <p>
                    Sent from Prestigious Trading & Constructions Website
                </p>
                """
            )


            print(
                "CONTACT ADMIN EMAIL STATUS:",
                email_result
            )


            # =========================
            # CUSTOMER CONFIRMATION
            # =========================

            customer_email_result = send_email(

                email,

                "Thank You for Contacting Prestigious Trading & Constructions",

                f"""
                <h2>Hello {fullname},</h2>

                <p>
                    Thank you for contacting
                    <b>Prestigious Trading & Constructions</b>.
                </p>

                <p>
                    We have received your enquiry regarding:
                </p>

                <p>
                    <b>{service}</b>
                </p>

                <p>
                    Our team will review your request and
                    contact you shortly.
                </p>

                <br>

                <p>
                    Regards,<br>
                    <b>Prestigious Trading & Constructions Team</b>
                </p>
                """
            )


            print(
                "CUSTOMER EMAIL STATUS:",
                customer_email_result
            )


            # =========================
            # SUCCESS MESSAGE
            # =========================

            flash(
                "Thank you for contacting us. Our team will get back to you shortly.",
                "success"
            )


        except Exception as e:

            if conn:
                conn.rollback()


            print("========================================")
            print("CONSTRUCTION CONTACT ERROR")
            print(type(e).__name__)
            print(str(e))
            print("========================================")


            flash(
                "Unable to send your enquiry. Please try again.",
                "error"
            )


        finally:

            if cursor:
                cursor.close()

            if conn:
                conn.close()


        return redirect(
            url_for("contact")
        )


    return render_template(
        "contact.html"
    )

# =====================================================
# CONSTRUCTION CONTACT MESSAGES
# =====================================================
@app.route("/construction/admin/contact-messages")
def construction_contact_messages():

    if "construction_admin_id" not in session:
        return redirect(
            url_for("admin_login")
        )


    conn = get_db_connection()

    cursor = conn.cursor(dictionary=True)


    # =========================
    # PAGINATION SETTINGS
    # =========================

    page = request.args.get(
        "page",
        1,
        type=int
    )

    per_page = 10

    offset = (page - 1) * per_page


    # =========================
    # TOTAL MESSAGES
    # =========================

    cursor.execute(
        """
        SELECT COUNT(*) AS total
        FROM construction_contact_messages
        """
    )

    total_messages = cursor.fetchone()["total"]


    total_pages = (
        (total_messages + per_page - 1)
        // per_page
    )


    # =========================
    # GET MESSAGES
    # =========================

    cursor.execute(
        """
        SELECT *
        FROM construction_contact_messages
        ORDER BY created_at DESC
        LIMIT %s OFFSET %s
        """,
        (
            per_page,
            offset
        )
    )

    messages = cursor.fetchall()


    cursor.close()

    conn.close()


    # =========================
    # RENDER
    # =========================

    return render_template(
        "construction_admin/contact_messages.html",

        messages=messages,

        page=page,

        total_pages=total_pages,

        total_messages=total_messages,

        unread_count=sum(
            1 for message in messages
            if not message.get("is_read")
        )

    )





# =====================================================
# VIEW CONSTRUCTION CONTACT MESSAGE
# =====================================================

@app.route("/construction/admin/contact-message/<int:id>")
def view_construction_contact_message(id):

    if "construction_admin_id" not in session:
        return redirect(
            url_for("admin_login")
        )


    conn = get_db_connection()

    cursor = conn.cursor(dictionary=True)


    # =========================
    # MARK MESSAGE AS READ
    # =========================

    cursor.execute(
        """
        UPDATE construction_contact_messages
        SET is_read = 1
        WHERE id = %s
        """,
        (id,)
    )

    conn.commit()


    # =========================
    # GET MESSAGE DETAILS
    # =========================

    cursor.execute(
        """
        SELECT *
        FROM construction_contact_messages
        WHERE id = %s
        """,
        (id,)
    )

    message = cursor.fetchone()


    cursor.close()
    conn.close()


    # =========================
    # MESSAGE NOT FOUND
    # =========================

    if not message:

        flash(
            "Message not found",
            "danger"
        )

        return redirect(
            url_for("construction_contact_messages")
        )


    # =========================
    # DISPLAY MESSAGE
    # =========================

    return render_template(
        "construction_admin/view_contact_message.html",
        message=message
    )

# =====================================================
# DELETE CONSTRUCTION CONTACT MESSAGE
# =====================================================
@app.route(
    "/construction/admin/delete-contact-message/<int:id>"
)
def delete_construction_contact_message(id):

    if "construction_admin_id" not in session:
        return redirect(
            url_for("admin_login")
        )


    conn = get_db_connection()

    cursor = conn.cursor()


    # =========================
    # DELETE MESSAGE
    # =========================

    cursor.execute(
        """
        DELETE FROM construction_contact_messages
        WHERE id=%s
        """,
        (id,)
    )


    conn.commit()


    cursor.close()

    conn.close()


    # =========================
    # SUCCESS MESSAGE
    # =========================

    flash(
        "Message deleted successfully",
        "success"
    )


    return redirect(
        url_for(
            "construction_contact_messages"
        )
    )




# -------------------------------------------------
# LOGIN ROUTE
# -------------------------------------------------
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():

    # -------------------------------------------------
    # ALREADY LOGGED IN
    # -------------------------------------------------
    if "construction_admin_id" in session:

        role = (session.get("construction_admin_role") or "").strip().lower()

        if role == "admin":
            return redirect(url_for("admin_dashboard"))

        elif role == "manager":
            return redirect(url_for("construction_manager_dashboard"))

        elif role == "purchase":
            return redirect(url_for("construction_purchase_dashboard"))

        elif role == "account":
            return redirect(url_for("construction_account_dashboard"))

        elif role == "engineer":
            return redirect(url_for("construction_engineer_dashboard"))

        session.clear()

    # -------------------------------------------------
    # LOGIN
    # -------------------------------------------------
    if request.method == "POST":

        selected_role = (
            request.form.get("role", "").strip().lower()
        )

        username = (
            request.form.get("username", "").strip()
        )

        password = request.form.get("password", "")

        # ---------------------------------------------
        # VALIDATION
        # ---------------------------------------------
        if not selected_role:
            flash(
                "Please select your department / role.",
                "danger"
            )
            return render_template(
                "construction_admin/login.html"
            )

        if not username or not password:
            flash(
                "Please enter your username and password.",
                "danger"
            )
            return render_template(
                "construction_admin/login.html"
            )

        # ---------------------------------------------
        # ALLOWED ROLES
        # ---------------------------------------------
        allowed_roles = {
            "admin",
            "manager",
            "purchase",
            "account",
            "engineer"
        }

        if selected_role not in allowed_roles:
            flash(
                "Invalid department selected.",
                "danger"
            )
            return render_template(
                "construction_admin/login.html"
            )

        conn = None
        cursor = None

        try:

            conn = get_db_connection()

            cursor = conn.cursor(dictionary=True)

            # -----------------------------------------
            # FIND USER
            # -----------------------------------------
            cursor.execute("""
                SELECT
                    id,
                    fullname,
                    username,
                    email,
                    password,
                    role
                FROM construction_admins
                WHERE username = %s
                LIMIT 1
            """, (username,))

            user = cursor.fetchone()

            if not user:

                flash(
                    "Invalid username or password.",
                    "danger"
                )

                return render_template(
                    "construction_admin/login.html"
                )

            # -----------------------------------------
            # CHECK PASSWORD
            # -----------------------------------------
            if not check_password_hash(
                user["password"],
                password
            ):

                flash(
                    "Invalid username or password.",
                    "danger"
                )

                return render_template(
                    "construction_admin/login.html"
                )

            # -----------------------------------------
            # DATABASE ROLE
            # -----------------------------------------
            database_role = (
                user.get("role") or ""
            ).strip().lower()

            if database_role not in allowed_roles:

                flash(
                    "Your account has not been assigned a valid "
                    "system role. Please contact the administrator.",
                    "danger"
                )

                return render_template(
                    "construction_admin/login.html"
                )

            # -----------------------------------------
            # SELECTED ROLE MUST MATCH DATABASE ROLE
            # -----------------------------------------
            if selected_role != database_role:

                flash(
                    "The selected department does not match your "
                    "account. Please select the correct role.",
                    "danger"
                )

                return render_template(
                    "construction_admin/login.html"
                )

            # -----------------------------------------
            # CREATE SESSION
            # -----------------------------------------
            session.permanent = True

            session["construction_admin_id"] = user["id"]
            session["construction_admin_name"] = user["fullname"]
            session["construction_admin_username"] = user["username"]
            session["construction_admin_email"] = user["email"]
            session["construction_admin_role"] = database_role
            session["construction_last_activity"] = (
                datetime.utcnow().isoformat()
            )

            # -----------------------------------------
            # ROLE REDIRECTION
            # -----------------------------------------
            if database_role == "admin":

                return redirect(
                    url_for("admin_dashboard")
                )

            elif database_role == "manager":

                return redirect(
                    url_for("construction_manager_dashboard")
                )

            elif database_role == "purchase":

                return redirect(
                    url_for("construction_purchase_dashboard")
                )

            elif database_role == "account":

                return redirect(
                    url_for("construction_account_dashboard")
                )

            elif database_role == "engineer":

                return redirect(
                    url_for("construction_engineer_dashboard")
                )

            # -----------------------------------------
            # FALLBACK
            # -----------------------------------------
            session.clear()

            flash(
                "Unable to determine your department.",
                "danger"
            )

        except Exception as e:

            print(
                "CONSTRUCTION LOGIN ERROR:",
                e
            )

            flash(
                "Unable to process login. Please try again.",
                "danger"
            )

        finally:

            if cursor:
                cursor.close()

            if conn:
                conn.close()

    return render_template(
        "construction_admin/login.html"
    )

# ==========================================================
# LOGOUT ROUTE
# ==========================================================
@app.route("/admin/logout")
def admin_logout():

    # ==========================================================
    # LOGOUT CURRENT CONSTRUCTION USER
    # ==========================================================

    session.clear()

    flash(
        "You have been successfully signed out.",
        "success"
    )

    return redirect(
        url_for("admin_login")
    )




# ============================================================
# CONSTRUCTION ADMIN DASHBOARD
# ============================================================

@app.route("/admin/dashboard")
def admin_dashboard():

    # ========================================================
    # CHECK CONSTRUCTION ADMIN LOGIN
    # ========================================================

    if "construction_admin_id" not in session:
        return redirect(
            url_for("admin_login")
        )


    # ========================================================
    # CHECK ADMIN ROLE
    #
    # ONLY:
    #   admin
    #   super_admin
    #
    # CAN ACCESS THIS DASHBOARD.
    #
    # Purchase, Engineer, Manager and Account users
    # must NOT be able to access this route.
    # ========================================================

    role = (
        session.get("construction_admin_role") or ""
    ).strip().lower()


    if role not in ("admin", "super_admin"):

        flash(
            "You are not authorized to access the Admin dashboard.",
            "danger"
        )

        # Send the user back to THEIR OWN dashboard
        return redirect(
            construction_role_dashboard()
        )


    conn = None
    cursor = None

    try:

        # ====================================================
        # DATABASE CONNECTION
        # ====================================================

        conn = get_db_connection()

        cursor = conn.cursor(dictionary=True)


        # ====================================================
        # TOTAL CONSTRUCTION PROJECTS
        # ====================================================

        cursor.execute("""
            SELECT COUNT(*) AS total_projects
            FROM construction_projects
        """)

        project_result = cursor.fetchone()

        total_projects = (
            project_result["total_projects"]
            if project_result
            else 0
        )


        # ====================================================
        # TOTAL CONSTRUCTION CONTACT MESSAGES
        # ====================================================

        cursor.execute("""
            SELECT COUNT(*) AS total_messages
            FROM construction_contact_messages
        """)

        message_result = cursor.fetchone()

        total_messages = (
            message_result["total_messages"]
            if message_result
            else 0
        )


        # ====================================================
        # UNREAD CONSTRUCTION CONTACT MESSAGES
        #
        # is_read = 0 -> UNREAD
        # is_read = 1 -> READ
        # ====================================================

        cursor.execute("""
            SELECT COUNT(*) AS unread_messages
            FROM construction_contact_messages
            WHERE is_read = 0
        """)

        unread_result = cursor.fetchone()

        unread_messages = (
            unread_result["unread_messages"]
            if unread_result
            else 0
        )


        # ====================================================
        # RECENT CONSTRUCTION CONTACT MESSAGES
        # ====================================================

        cursor.execute("""
            SELECT
                id,
                fullname,
                email,
                phone,
                subject,
                message,
                created_at,
                is_read

            FROM construction_contact_messages

            ORDER BY created_at DESC

            LIMIT 5
        """)

        recent_messages = cursor.fetchall()


        # ====================================================
        # CLOSE DATABASE BEFORE RENDERING
        # ====================================================

        cursor.close()
        cursor = None

        conn.close()
        conn = None


        # ====================================================
        # DASHBOARD
        # ====================================================

        return render_template(

            "construction_admin/dashboard.html",

            admin_name=session.get(
                "construction_admin_name",
                "Administrator"
            ),

            admin_username=session.get(
                "construction_admin_username",
                ""
            ),

            admin_role=session.get(
                "construction_admin_role",
                "Admin"
            ),

            total_projects=total_projects,

            total_messages=total_messages,

            unread_messages=unread_messages,

            recent_messages=recent_messages

        )


    # ========================================================
    # ERROR HANDLING
    # ========================================================

    except Exception as e:

        print("=" * 50)

        print("CONSTRUCTION DASHBOARD ERROR")

        print("ERROR TYPE:")
        print(type(e).__name__)

        print("ERROR:")
        print(str(e))

        print("=" * 50)


        if conn:
            try:
                conn.rollback()
            except:
                pass


        flash(
            "Unable to load dashboard statistics.",
            "danger"
        )


        return render_template(

            "construction_admin/dashboard.html",

            admin_name=session.get(
                "construction_admin_name",
                "Administrator"
            ),

            admin_username=session.get(
                "construction_admin_username",
                "Administrator"
            ),

            admin_role=session.get(
                "construction_admin_role",
                "Admin"
            ),

            total_projects=0,

            total_messages=0,

            unread_messages=0,

            recent_messages=[]

        )


    finally:

        if cursor:

            try:
                cursor.close()
            except:
                pass


        if conn:

            try:
                conn.close()
            except:
                pass



# =====================================================
# ADD CONSTRUCTION PROJECT
# =====================================================

@app.route("/admin/add-project", methods=["GET", "POST"])
def add_project():

    # ==========================
    # CONSTRUCTION ADMIN AUTH
    # ==========================

    if "construction_admin_id" not in session:

        return redirect(
            url_for("admin_login")
        )


    # ==========================
    # POST REQUEST
    # ==========================

    if request.method == "POST":

        title = request.form.get(
            "title",
            ""
        ).strip()

        category = request.form.get(
            "category",
            ""
        ).strip()

        location = request.form.get(
            "location",
            ""
        ).strip()

        client = request.form.get(
            "client",
            ""
        ).strip()

        description = request.form.get(
            "description",
            ""
        ).strip()

        start_date = (
            request.form.get("start_date")
            or None
        )

        completion_date = (
            request.form.get("completion_date")
            or None
        )

        status = request.form.get(
            "status",
            "Upcoming"
        )

        is_featured = (
            1
            if request.form.get("is_featured")
            else 0
        )


        # ==========================
        # VALIDATION
        # ==========================

        if not title:

            flash(
                "Project title is required.",
                "danger"
            )

            return redirect(
                url_for("add_project")
            )


        # ==========================
        # CREATE SLUG
        # ==========================

        slug = (
            title.lower()
            .replace(" ", "-")
            + "-"
            + str(uuid.uuid4())[:6]
        )


        conn = None
        cursor = None


        try:

            conn = get_db_connection()

            cursor = conn.cursor()


            # ==========================================
            # UPLOAD PROJECT IMAGES
            # ==========================================

            main_image = None

            images = request.files.getlist(
                "images"
            )


            uploaded_images = []


            MAX_IMAGE_SIZE = (
                10 * 1024 * 1024
            )


            for image in images:

                if not image:
                    continue


                if not image.filename:
                    continue


                if not allowed_file(
                    image.filename
                ):

                    continue


                # ==========================
                # CHECK IMAGE SIZE
                # ==========================

                if (
                    hasattr(
                        image,
                        "content_length"
                    )
                    and image.content_length
                    and image.content_length
                    > MAX_IMAGE_SIZE
                ):

                    flash(
                        f"Image '{image.filename}' "
                        "is too large. Maximum size is 10MB.",
                        "danger"
                    )

                    return redirect(
                        url_for(
                            "add_project"
                        )
                    )


                # ==========================
                # CLOUDINARY UPLOAD
                # ==========================

                result = cloudinary.uploader.upload(
                    image,
                    folder="prestigious_construction/projects",
                    resource_type="image",
                    transformation=[
                        {
                            "width": 1600,
                            "height": 1200,
                            "crop": "limit",
                            "quality": "auto",
                            "fetch_format": "auto"
                        }
                    ]
                )


                image_url = result[
                    "secure_url"
                ]


                # ==========================
                # FIRST IMAGE = COVER IMAGE
                # ==========================

                if main_image is None:

                    main_image = image_url


                # Store URL for gallery
                uploaded_images.append(
                    image_url
                )


            # ==========================================
            # INSERT PROJECT
            # ==========================================

            cursor.execute(
                """
                INSERT INTO construction_projects
                (
                    title,
                    slug,
                    category,
                    location,
                    client,
                    description,
                    image,
                    start_date,
                    completion_date,
                    status,
                    is_featured
                )

                VALUES
                (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s
                )
                """,

                (
                    title,
                    slug,
                    category,
                    location,
                    client,
                    description,
                    main_image,
                    start_date,
                    completion_date,
                    status,
                    is_featured
                )
            )


            # ==========================================
            # GET NEW PROJECT ID
            # ==========================================

            project_id = cursor.lastrowid


            # ==========================================
            # SAVE ALL IMAGES TO GALLERY TABLE
            # ==========================================

            for image_url in uploaded_images:

                cursor.execute(
                    """
                    INSERT INTO construction_project_images
                    (
                        project_id,
                        image_url
                    )

                    VALUES
                    (
                        %s,
                        %s
                    )
                    """,

                    (
                        project_id,
                        image_url
                    )
                )


            # ==========================================
            # COMMIT EVERYTHING
            # ==========================================

            conn.commit()


            flash(
                "Construction project added successfully.",
                "success"
            )


            return redirect(
                url_for(
                    "admin_dashboard"
                )
            )


        except Exception as e:

            if conn:

                conn.rollback()


            print(
                "ADD CONSTRUCTION PROJECT ERROR:",
                e
            )


            flash(
                "Unable to add project. Please try again.",
                "danger"
            )


        finally:

            if cursor:

                cursor.close()


            if conn:

                conn.close()


    # ==========================
    # ADD PROJECT PAGE
    # ==========================

    return render_template(
        "construction_admin/add_project.html"
    )







# =====================================================
# PROJECT DETAILS
# =====================================================

@app.route("/projects/<slug>")
def project_details(slug):

    conn = None
    cursor = None

    try:

        conn = get_db_connection()

        cursor = conn.cursor(dictionary=True)


        # =================================================
        # GET PROJECT
        # =================================================

        cursor.execute(
            """
            SELECT
                id,
                title,
                slug,
                category,
                location,
                client,
                description,
                image,
                start_date,
                completion_date,
                status,
                is_featured,
                created_at

            FROM construction_projects

            WHERE slug = %s

            LIMIT 1
            """,

            (slug,)
        )


        project = cursor.fetchone()


        # =================================================
        # PROJECT NOT FOUND
        # =================================================

        if not project:

            return render_template(
                "404.html"
            ), 404


        # =================================================
        # GET PROJECT GALLERY
        # =================================================

        cursor.execute(
            """
            SELECT
                id,
                project_id,
                image_url,
                created_at

            FROM construction_project_images

            WHERE project_id = %s

            ORDER BY id ASC
            """,

            (project["id"],)
        )


        project_images = cursor.fetchall()


        # =================================================
        # DISPLAY PROJECT
        # =================================================

        return render_template(
            "project_details.html",

            project=project,

            project_images=project_images
        )


    except Exception as e:

        print(
            "PROJECT DETAILS ERROR:",
            e
        )


        return "Unable to load project.", 500


    finally:

        if cursor:
            cursor.close()

        if conn:
            conn.close()






# =====================================================
# MANAGE CONSTRUCTION ROUTE
# =====================================================

# =====================================================
# MANAGE CONSTRUCTION PROJECTS
# =====================================================

@app.route("/construction/projects/manage")
def manage_construction_projects():

    # ==========================================
    # CONSTRUCTION ADMIN AUTHENTICATION
    # ==========================================

    if "construction_admin_id" not in session:

        return redirect(
            url_for("admin_login")
        )


    conn = None
    cursor = None


    try:

        conn = get_db_connection()

        cursor = conn.cursor(dictionary=True)


        # ==========================================
        # GET CONSTRUCTION PROJECTS
        # ==========================================

        cursor.execute(
            """
            SELECT
                p.*,

                COUNT(pi.id) AS image_count

            FROM construction_projects p

            LEFT JOIN construction_project_images pi
                ON p.id = pi.project_id

            GROUP BY
                p.id

            ORDER BY
                p.id DESC
            """
        )


        projects = cursor.fetchall()


        return render_template(
            "construction_admin/manage_projects.html",
            projects=projects
        )


    except Exception as e:

        print(
            "MANAGE CONSTRUCTION PROJECTS ERROR:",
            e
        )


        flash(
            "Unable to load construction projects.",
            "danger"
        )


        return redirect(
            url_for("admin_dashboard")
        )


    finally:

        if cursor:

            cursor.close()


        if conn:

            conn.close()








# =====================================================
# EDIT CONSTRUCTION PROJECT
# =====================================================

@app.route("/admin/edit-project/<int:project_id>", methods=["GET", "POST"])
def edit_project(project_id):

    # ==========================
    # CONSTRUCTION ADMIN AUTH
    # ==========================

    if "construction_admin_id" not in session:

        return redirect(
            url_for("admin_login")
        )


    conn = None
    cursor = None


    try:

        conn = get_db_connection()

        cursor = conn.cursor(dictionary=True)


        # ==========================
        # GET PROJECT
        # ==========================

        cursor.execute(
            """
            SELECT *
            FROM construction_projects
            WHERE id = %s
            LIMIT 1
            """,
            (project_id,)
        )

        project = cursor.fetchone()


        if not project:

            flash(
                "Construction project not found.",
                "danger"
            )

            return redirect(
                url_for("manage_construction_projects")
            )


        # ==========================
        # POST - UPDATE PROJECT
        # ==========================

        if request.method == "POST":

            title = request.form.get(
                "title",
                ""
            ).strip()

            category = request.form.get(
                "category",
                ""
            ).strip()

            location = request.form.get(
                "location",
                ""
            ).strip()

            client = request.form.get(
                "client",
                ""
            ).strip()

            description = request.form.get(
                "description",
                ""
            ).strip()

            start_date = (
                request.form.get("start_date")
                or None
            )

            completion_date = (
                request.form.get("completion_date")
                or None
            )

            status = request.form.get(
                "status",
                "Upcoming"
            ).strip()

            is_featured = (
                1
                if request.form.get("is_featured")
                else 0
            )


            # ==========================
            # VALIDATION
            # ==========================

            if not title:

                flash(
                    "Project title is required.",
                    "danger"
                )

                return render_template(
                    "construction_admin/edit_project.html",
                    project=project
                )


            # ==========================
            # UPDATE DATABASE
            # ==========================

            cursor.execute(
                """
                UPDATE construction_projects

                SET
                    title = %s,
                    category = %s,
                    location = %s,
                    client = %s,
                    description = %s,
                    start_date = %s,
                    completion_date = %s,
                    status = %s,
                    is_featured = %s

                WHERE id = %s
                """,

                (
                    title,
                    category,
                    location,
                    client,
                    description,
                    start_date,
                    completion_date,
                    status,
                    is_featured,
                    project_id
                )
            )


            # ==========================
            # COMMIT
            # ==========================

            conn.commit()


            flash(
                "Construction project updated successfully.",
                "success"
            )


            return redirect(
                url_for(
                    "manage_construction_projects"
                )
            )


        # ==========================
        # DISPLAY EDIT PAGE
        # ==========================

        return render_template(
            "construction_admin/edit_project.html",
            project=project
        )


    except Exception as e:

        if conn:

            conn.rollback()


        print(
            "EDIT CONSTRUCTION PROJECT ERROR:",
            e
        )


        flash(
            "Unable to update project.",
            "danger"
        )


        return redirect(
            url_for(
                "manage_construction_projects"
            )
        )


    finally:

        if cursor:

            cursor.close()


        if conn:

            conn.close()







# =====================================================
# MANAGE CONSTRUCTION PROJECT GALLERY
# =====================================================

@app.route("/admin/project/<int:project_id>/gallery")
def manage_construction_gallery(project_id):

    # ==========================
    # CONSTRUCTION ADMIN AUTH
    # ==========================

    if "construction_admin_id" not in session:

        return redirect(
            url_for("admin_login")
        )


    conn = None
    cursor = None


    try:

        conn = get_db_connection()

        cursor = conn.cursor(dictionary=True)


        # ==========================
        # GET PROJECT
        # ==========================

        cursor.execute(
            """
            SELECT *
            FROM construction_projects
            WHERE id = %s
            LIMIT 1
            """,
            (project_id,)
        )

        project = cursor.fetchone()


        if not project:

            flash(
                "Construction project not found.",
                "danger"
            )

            return redirect(
                url_for("manage_construction_projects")
            )


        # ==========================
        # GET PROJECT IMAGES
        # ==========================

        cursor.execute(
            """
            SELECT
                id,
                project_id,
                image_url,
                created_at
            FROM construction_project_images
            WHERE project_id = %s
            ORDER BY id ASC
            """,
            (project_id,)
        )

        images = cursor.fetchall()


        return render_template(
            "construction_admin/manage_gallery.html",
            project=project,
            images=images
        )


    except Exception as e:

        print(
            "MANAGE CONSTRUCTION GALLERY ERROR:",
            e
        )


        flash(
            "Unable to load project gallery.",
            "danger"
        )


        return redirect(
            url_for(
                "manage_construction_projects"
            )
        )


    finally:

        if cursor:

            cursor.close()


        if conn:

            conn.close()







# =====================================================
# SET CONSTRUCTION PROJECT COVER IMAGE ROUTE
# =====================================================

# =====================================================
# SET PROJECT COVER IMAGE
# =====================================================

@app.route(
    "/admin/construction/project/<int:project_id>/set-cover/<int:image_id>",
    methods=["POST"]
)
def set_project_cover(project_id, image_id):

    # =================================================
    # ADMIN AUTHENTICATION
    # =================================================

    if "construction_admin_id" not in session:

        return redirect(
            url_for("admin_login")
        )


    conn = None
    cursor = None


    try:

        # =================================================
        # DATABASE CONNECTION
        # =================================================

        conn = get_db_connection()

        cursor = conn.cursor(dictionary=True)


        # =================================================
        # CHECK PROJECT EXISTS
        # =================================================

        cursor.execute(
            """
            SELECT
                id,
                title
            FROM construction_projects
            WHERE id = %s
            LIMIT 1
            """,
            (project_id,)
        )

        project = cursor.fetchone()


        if not project:

            flash(
                "Project not found.",
                "danger"
            )

            return redirect(
                url_for(
                    "manage_construction_projects"
                )
            )


        # =================================================
        # GET IMAGE
        # =================================================

        cursor.execute(
            """
            SELECT
                id,
                project_id,
                image_url
            FROM construction_project_images
            WHERE id = %s
              AND project_id = %s
            LIMIT 1
            """,
            (
                image_id,
                project_id
            )
        )

        image = cursor.fetchone()


        if not image:

            flash(
                "Project image not found.",
                "danger"
            )

            return redirect(
                url_for(
                    "manage_construction_gallery",
                    project_id=project_id
                )
            )


        # =================================================
        # SET IMAGE AS PROJECT COVER
        # =================================================

        cursor.execute(
            """
            UPDATE construction_projects
            SET image = %s
            WHERE id = %s
            """,
            (
                image["image_url"],
                project_id
            )
        )


        # =================================================
        # SAVE CHANGES
        # =================================================

        conn.commit()


        # =================================================
        # SUCCESS MESSAGE
        # =================================================

        flash(
            "Cover image updated successfully.",
            "success"
        )


        # =================================================
        # RETURN TO GALLERY
        # =================================================

        return redirect(
            url_for(
                "manage_construction_gallery",
                project_id=project_id
            )
        )


    except Exception as e:

        # =================================================
        # ROLLBACK
        # =================================================

        if conn:

            conn.rollback()


        print(
            "SET PROJECT COVER ERROR:",
            e
        )


        flash(
            "Unable to set cover image.",
            "danger"
        )


        # =================================================
        # RETURN TO GALLERY
        # =================================================

        return redirect(
            url_for(
                "manage_construction_gallery",
                project_id=project_id
            )
        )


    finally:

        # =================================================
        # CLOSE DATABASE
        # =================================================

        if cursor:

            cursor.close()


        if conn:

            conn.close()








# =====================================================
# DELETE PROJECT GALLERY IMAGE
# =====================================================

@app.route(
    "/admin/construction/project/<int:project_id>/delete-image/<int:image_id>",
    methods=["POST"]
)
def delete_project_image(project_id, image_id):

    # =================================================
    # ADMIN AUTHENTICATION
    # =================================================

    if "construction_admin_id" not in session:

        return redirect(
            url_for("admin_login")
        )


    conn = None
    cursor = None


    try:

        # =================================================
        # DATABASE CONNECTION
        # =================================================

        conn = get_db_connection()

        cursor = conn.cursor(dictionary=True)


        # =================================================
        # GET PROJECT
        # =================================================

        cursor.execute(
            """
            SELECT
                id,
                title,
                image
            FROM construction_projects
            WHERE id = %s
            LIMIT 1
            """,
            (project_id,)
        )

        project = cursor.fetchone()


        # =================================================
        # PROJECT NOT FOUND
        # =================================================

        if not project:

            flash(
                "Project not found.",
                "danger"
            )

            return redirect(
                url_for(
                    "manage_construction_projects"
                )
            )


        # =================================================
        # GET IMAGE
        # =================================================

        cursor.execute(
            """
            SELECT
                id,
                project_id,
                image_url
            FROM construction_project_images
            WHERE id = %s
              AND project_id = %s
            LIMIT 1
            """,
            (
                image_id,
                project_id
            )
        )

        image = cursor.fetchone()


        # =================================================
        # IMAGE NOT FOUND
        # =================================================

        if not image:

            flash(
                "Project image not found.",
                "danger"
            )

            return redirect(
                url_for(
                    "manage_construction_gallery",
                    project_id=project_id
                )
            )


        # =================================================
        # PREVENT DELETING CURRENT COVER
        # =================================================

        if project["image"] == image["image_url"]:

            flash(
                "You cannot delete the current cover image. "
                "Please set another image as the cover first.",
                "warning"
            )

            return redirect(
                url_for(
                    "manage_construction_gallery",
                    project_id=project_id
                )
            )


        # =================================================
        # DELETE IMAGE FROM DATABASE
        # =================================================

        cursor.execute(
            """
            DELETE FROM construction_project_images
            WHERE id = %s
              AND project_id = %s
            """,
            (
                image_id,
                project_id
            )
        )


        # =================================================
        # COMMIT DATABASE CHANGE
        # =================================================

        conn.commit()


        # =================================================
        # DELETE IMAGE FROM CLOUDINARY
        # =================================================

        try:

            image_url = image["image_url"]

            # ---------------------------------------------
            # Extract Cloudinary public ID from URL
            # ---------------------------------------------

            if "res.cloudinary.com" in image_url:

                parts = image_url.split("/upload/")

                if len(parts) == 2:

                    public_path = parts[1]

                    # Remove transformation information
                    public_path = public_path.split("/")[-1]

                    # Remove file extension
                    public_id = os.path.splitext(
                        public_path
                    )[0]


                    # Cloudinary folder + filename
                    public_id = (
                        "prestigious_construction/projects/"
                        + public_id
                    )


                    cloudinary.uploader.destroy(
                        public_id,
                        resource_type="image"
                    )


        except Exception as cloudinary_error:

            print(
                "CLOUDINARY DELETE ERROR:",
                cloudinary_error
            )

            # Database deletion has already succeeded.
            # We do not undo the database deletion
            # because the gallery image is already removed.


        # =================================================
        # SUCCESS
        # =================================================

        flash(
            "Project image deleted successfully.",
            "success"
        )


        return redirect(
            url_for(
                "manage_construction_gallery",
                project_id=project_id
            )
        )


    except Exception as e:

        # =================================================
        # ROLLBACK
        # =================================================

        if conn:

            conn.rollback()


        print(
            "DELETE PROJECT IMAGE ERROR:",
            e
        )


        flash(
            "Unable to delete project image.",
            "danger"
        )


        return redirect(
            url_for(
                "manage_construction_gallery",
                project_id=project_id
            )
        )


    finally:

        # =================================================
        # CLOSE DATABASE
        # =================================================

        if cursor:

            cursor.close()


        if conn:

            conn.close()









# =====================================================
# DELETE COMPLETE CONSTRUCTION PROJECT
# =====================================================

@app.route(
    "/admin/construction/project/<int:project_id>/delete",
    methods=["POST"]
)
def delete_construction_project(project_id):

    # =================================================
    # ADMIN AUTHENTICATION
    # =================================================

    if "construction_admin_id" not in session:

        return redirect(
            url_for("admin_login")
        )


    conn = None
    cursor = None


    try:

        # =================================================
        # DATABASE CONNECTION
        # =================================================

        conn = get_db_connection()

        cursor = conn.cursor(dictionary=True)


        # =================================================
        # GET PROJECT
        # =================================================

        cursor.execute(
            """
            SELECT
                id,
                title
            FROM construction_projects
            WHERE id = %s
            LIMIT 1
            """,
            (project_id,)
        )

        project = cursor.fetchone()


        # =================================================
        # PROJECT NOT FOUND
        # =================================================

        if not project:

            flash(
                "Project not found.",
                "danger"
            )

            return redirect(
                url_for(
                    "manage_construction_projects"
                )
            )


        # =================================================
        # GET ALL PROJECT IMAGES
        # =================================================

        cursor.execute(
            """
            SELECT
                id,
                image_url
            FROM construction_project_images
            WHERE project_id = %s
            """,
            (project_id,)
        )

        images = cursor.fetchall()


        # =================================================
        # DELETE GALLERY RECORDS
        # =================================================

        cursor.execute(
            """
            DELETE FROM construction_project_images
            WHERE project_id = %s
            """,
            (project_id,)
        )


        # =================================================
        # DELETE PROJECT
        # =================================================

        cursor.execute(
            """
            DELETE FROM construction_projects
            WHERE id = %s
            """,
            (project_id,)
        )


        # =================================================
        # COMMIT DATABASE CHANGES
        # =================================================

        conn.commit()


        # =================================================
        # DELETE PROJECT IMAGES FROM CLOUDINARY
        # =================================================

        for image in images:

            try:

                image_url = image["image_url"]


                if (
                    image_url
                    and
                    "res.cloudinary.com" in image_url
                ):

                    parts = image_url.split(
                        "/upload/"
                    )


                    if len(parts) == 2:

                        public_path = parts[1]


                        # Remove Cloudinary version
                        # Example:
                        # v1234567890/folder/image.jpg

                        public_path_parts = (
                            public_path.split("/")
                        )


                        if (
                            public_path_parts
                            and
                            public_path_parts[0].startswith("v")
                            and
                            public_path_parts[0][1:].isdigit()
                        ):

                            public_path_parts = (
                                public_path_parts[1:]
                            )


                        public_path = "/".join(
                            public_path_parts
                        )


                        # Remove file extension

                        public_id = os.path.splitext(
                            public_path
                        )[0]


                        # =================================================
                        # DELETE FROM CLOUDINARY
                        # =================================================

                        cloudinary.uploader.destroy(
                            public_id,
                            resource_type="image"
                        )


            except Exception as cloudinary_error:

                print(
                    "CLOUDINARY PROJECT IMAGE DELETE ERROR:",
                    cloudinary_error
                )


        # =================================================
        # SUCCESS MESSAGE
        # =================================================

        flash(
            f'Project "{project["title"]}" deleted successfully.',
            "success"
        )


        # =================================================
        # RETURN TO PROJECT MAINTENANCE
        # =================================================

        return redirect(
            url_for(
                "manage_construction_projects"
            )
        )


    except Exception as e:

        # =================================================
        # ROLLBACK
        # =================================================

        if conn:

            conn.rollback()


        print(
            "DELETE CONSTRUCTION PROJECT ERROR:",
            e
        )


        flash(
            "Unable to delete project. Please try again.",
            "danger"
        )


        return redirect(
            url_for(
                "manage_construction_projects"
            )
        )


    finally:

        # =================================================
        # CLOSE DATABASE
        # =================================================

        if cursor:

            cursor.close()


        if conn:

            conn.close()











# ============================================================
# VIEW ALL CONSTRUCTION VACANCIES
# ============================================================

@app.route("/admin/construction-vacancies")
def construction_vacancies():

    # =========================
    # CHECK CONSTRUCTION ADMIN
    # =========================

    if "construction_admin_id" not in session:
        return redirect(url_for("admin_login"))

    conn = None
    cursor = None

    try:

        conn = get_db_connection()

        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT
                id,
                title,
                department,
                location,
                employment_type,
                deadline,
                status,
                created_at,
                updated_at
            FROM construction_job_vacancies
            ORDER BY created_at DESC
        """)

        vacancies = cursor.fetchall()

        return render_template(
            "construction_admin/vacancies.html",
            vacancies=vacancies
        )

    except Exception as e:

        print("========================================")
        print("CONSTRUCTION VACANCIES ERROR")
        print(type(e).__name__)
        print(str(e))
        print("========================================")

        flash(
            "Unable to load job vacancies.",
            "danger"
        )

        return redirect(
            url_for("admin_dashboard")
        )

    finally:

        if cursor:
            cursor.close()

        if conn:
            conn.close()




# ============================================================
# ADD CONSTRUCTION VACANCY
# ============================================================

@app.route(
    "/admin/construction-vacancies/add",
    methods=["GET", "POST"]
)
def add_construction_vacancy():

    # =========================
    # CHECK CONSTRUCTION ADMIN
    # =========================

    if "construction_admin_id" not in session:
        return redirect(url_for("admin_login"))

    if request.method == "POST":

        title = request.form.get(
            "title",
            ""
        ).strip()

        department = request.form.get(
            "department",
            ""
        ).strip()

        location = request.form.get(
            "location",
            "Qatar"
        ).strip()

        employment_type = request.form.get(
            "employment_type",
            ""
        ).strip()

        experience = request.form.get(
            "experience",
            ""
        ).strip()

        description = request.form.get(
            "description",
            ""
        ).strip()

        responsibilities = request.form.get(
            "responsibilities",
            ""
        ).strip()

        requirements = request.form.get(
            "requirements",
            ""
        ).strip()

        deadline = request.form.get(
            "deadline",
            ""
        ).strip()

        status = request.form.get(
            "status",
            "draft"
        ).strip().lower()


        # =========================
        # VALIDATION
        # =========================

        if not title:

            flash(
                "Job title is required.",
                "danger"
            )

            return redirect(
                url_for("add_construction_vacancy")
            )


        if status not in [
            "draft",
            "open",
            "closed"
        ]:

            status = "draft"


        # =========================
        # DATABASE
        # =========================

        conn = None
        cursor = None

        try:

            conn = get_db_connection()

            cursor = conn.cursor()


            cursor.execute(
                """
                INSERT INTO construction_job_vacancies
                (
                    title,
                    department,
                    location,
                    employment_type,
                    experience,
                    description,
                    responsibilities,
                    requirements,
                    deadline,
                    status
                )

                VALUES
                (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    NULLIF(%s, ''),
                    %s
                )
                """,
                (
                    title,
                    department,
                    location,
                    employment_type,
                    experience,
                    description,
                    responsibilities,
                    requirements,
                    deadline,
                    status
                )
            )

            conn.commit()


            flash(
                "Job vacancy created successfully.",
                "success"
            )

            return redirect(
                url_for("construction_vacancies")
            )


        except Exception as e:

            if conn:
                conn.rollback()

            print("========================================")
            print("ADD CONSTRUCTION VACANCY ERROR")
            print(type(e).__name__)
            print(str(e))
            print("========================================")

            flash(
                "Unable to create job vacancy.",
                "danger"
            )


        finally:

            if cursor:
                cursor.close()

            if conn:
                conn.close()


    return render_template(
        "construction_admin/add_vacancy.html"
    )


# ============================================================
# EDIT CONSTRUCTION VACANCY
# ============================================================

@app.route(
    "/admin/construction-vacancies/edit/<int:id>",
    methods=["GET", "POST"]
)
def edit_construction_vacancy(id):

    # =========================================================
    # CHECK CONSTRUCTION ADMIN
    # =========================================================

    if "construction_admin_id" not in session:
        return redirect(url_for("admin_login"))

    conn = None
    cursor = None

    try:

        conn = get_db_connection()

        cursor = conn.cursor(dictionary=True)


        # =========================================================
        # GET VACANCY
        # =========================================================

        cursor.execute(
            """
            SELECT
                id,
                title,
                department,
                location,
                employment_type,
                experience,
                description,
                responsibilities,
                requirements,
                deadline,
                status,
                created_at
            FROM construction_job_vacancies
            WHERE id = %s
            """,
            (id,)
        )

        vacancy = cursor.fetchone()


        # =========================================================
        # VACANCY NOT FOUND
        # =========================================================

        if not vacancy:

            flash(
                "Job vacancy not found.",
                "danger"
            )

            return redirect(
                url_for("construction_vacancies")
            )


        # =========================================================
        # UPDATE VACANCY
        # =========================================================

        if request.method == "POST":

            title = request.form.get(
                "title",
                ""
            ).strip()

            department = request.form.get(
                "department",
                ""
            ).strip()

            location = request.form.get(
                "location",
                "Qatar"
            ).strip()

            employment_type = request.form.get(
                "employment_type",
                ""
            ).strip()

            experience = request.form.get(
                "experience",
                ""
            ).strip()

            description = request.form.get(
                "description",
                ""
            ).strip()

            responsibilities = request.form.get(
                "responsibilities",
                ""
            ).strip()

            requirements = request.form.get(
                "requirements",
                ""
            ).strip()

            deadline = request.form.get(
                "deadline",
                ""
            ).strip()

            status = request.form.get(
                "status",
                "draft"
            ).strip().lower()


            # =====================================================
            # VALIDATE TITLE
            # =====================================================

            if not title:

                flash(
                    "Job title is required.",
                    "danger"
                )

                return render_template(
                    "construction_admin/edit_vacancy.html",
                    vacancy=vacancy
                )


            # =====================================================
            # VALIDATE STATUS
            # =====================================================

            if status not in [
                "draft",
                "open",
                "closed"
            ]:

                status = "draft"


            # =====================================================
            # UPDATE DATABASE
            # =====================================================

            cursor.execute(
                """
                UPDATE construction_job_vacancies

                SET
                    title = %s,
                    department = %s,
                    location = %s,
                    employment_type = %s,
                    experience = %s,
                    description = %s,
                    responsibilities = %s,
                    requirements = %s,
                    deadline = NULLIF(%s, ''),
                    status = %s

                WHERE id = %s
                """,
                (
                    title,
                    department,
                    location,
                    employment_type,
                    experience,
                    description,
                    responsibilities,
                    requirements,
                    deadline,
                    status,
                    id
                )
            )


            # =====================================================
            # COMMIT
            # =====================================================

            conn.commit()


            # =====================================================
            # SUCCESS
            # =====================================================

            flash(
                "Job vacancy updated successfully.",
                "success"
            )

            return redirect(
                url_for("construction_vacancies")
            )


        # =========================================================
        # DISPLAY EDIT PAGE
        # =========================================================

        return render_template(
            "construction_admin/edit_vacancy.html",
            vacancy=vacancy
        )


    # =========================================================
    # ERROR HANDLING
    # =========================================================

    except Exception as e:

        if conn:
            conn.rollback()

        print("========================================")
        print("EDIT CONSTRUCTION VACANCY ERROR")
        print(type(e).__name__)
        print(str(e))
        print("========================================")

        flash(
            "Unable to update job vacancy.",
            "danger"
        )

        return redirect(
            url_for("construction_vacancies")
        )


    # =========================================================
    # CLOSE DATABASE
    # =========================================================

    finally:

        if cursor:
            cursor.close()

        if conn:
            conn.close()


# ============================================================
# DELETE CONSTRUCTION VACANCY
# ============================================================

@app.route(
    "/admin/construction-vacancies/delete/<int:id>"
)
def delete_construction_vacancy(id):

    # =========================
    # CHECK CONSTRUCTION ADMIN
    # =========================

    if "construction_admin_id" not in session:
        return redirect(url_for("admin_login"))

    conn = None
    cursor = None

    try:

        conn = get_db_connection()

        cursor = conn.cursor()


        cursor.execute(
            """
            DELETE FROM construction_job_vacancies
            WHERE id=%s
            """,
            (id,)
        )

        conn.commit()


        flash(
            "Job vacancy deleted successfully.",
            "success"
        )


    except Exception as e:

        if conn:
            conn.rollback()

        print("========================================")
        print("DELETE CONSTRUCTION VACANCY ERROR")
        print(type(e).__name__)
        print(str(e))
        print("========================================")

        flash(
            "Unable to delete job vacancy.",
            "danger"
        )


    finally:

        if cursor:
            cursor.close()

        if conn:
            conn.close()


    return redirect(
        url_for("construction_vacancies")
    )


# ============================================================
# CHANGE VACANCY STATUS
# ============================================================

@app.route(
    "/admin/construction-vacancies/status/<int:id>/<status>"
)
def change_construction_vacancy_status(id, status):

    # =========================
    # CHECK CONSTRUCTION ADMIN
    # =========================

    if "construction_admin_id" not in session:
        return redirect(url_for("admin_login"))


    if status not in [
        "draft",
        "open",
        "closed"
    ]:

        flash(
            "Invalid vacancy status.",
            "danger"
        )

        return redirect(
            url_for("construction_vacancies")
        )


    conn = None
    cursor = None

    try:

        conn = get_db_connection()

        cursor = conn.cursor()


        cursor.execute(
            """
            UPDATE construction_job_vacancies

            SET status=%s

            WHERE id=%s
            """,
            (
                status,
                id
            )
        )

        conn.commit()


        if status == "open":

            message = "Job vacancy published successfully."

        elif status == "closed":

            message = "Job vacancy closed successfully."

        else:

            message = "Job vacancy moved to draft."


        flash(
            message,
            "success"
        )


    except Exception as e:

        if conn:
            conn.rollback()

        print("========================================")
        print("CHANGE CONSTRUCTION VACANCY STATUS ERROR")
        print(type(e).__name__)
        print(str(e))
        print("========================================")

        flash(
            "Unable to change vacancy status.",
            "danger"
        )


    finally:

        if cursor:
            cursor.close()

        if conn:
            conn.close()


    return redirect(
        url_for("construction_vacancies")
    )









# ============================================================
# CONSTRUCTION JOB VACANCY DETAILS
# ============================================================

@app.route("/construction/careers/<int:id>")
def construction_vacancy_details(id):

    conn = None
    cursor = None

    try:

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        cursor.execute("""
            SELECT
                id,
                title,
                department,
                location,
                employment_type,
                experience,
                description,
                responsibilities,
                requirements,
                deadline,
                status,
                created_at,
                updated_at
            FROM construction_job_vacancies
            WHERE id = %s
              AND status = 'open'
            LIMIT 1
        """, (id,))

        vacancy = cursor.fetchone()

        if not vacancy:

            return render_template(
                "404.html"
            ), 404

        return render_template(
            "construction_vacancy_details.html",
            vacancy=vacancy
        )

    except Exception as e:

        print("========================================")
        print("CONSTRUCTION VACANCY DETAILS ERROR")
        print("Vacancy ID:", id)
        print("Error Type:", type(e).__name__)
        print("Error:", str(e))
        print("========================================")

        return f"""
        <div style="
            max-width:800px;
            margin:80px auto;
            padding:30px;
            font-family:Arial,sans-serif;
        ">

            <h1 style="color:#b00020;">
                Unable to Load Vacancy
            </h1>

            <p>
                There was a problem loading this career opportunity.
            </p>

            <hr>

            <strong>Error:</strong>

            <pre style="
                background:#f5f5f5;
                padding:20px;
                overflow:auto;
                margin-top:15px;
            ">{str(e)}</pre>

            <br>

            <a
                href="{{ url_for('careers') }}"
                style="
                    display:inline-block;
                    padding:12px 22px;
                    background:#244b3a;
                    color:white;
                    text-decoration:none;
                "
            >
                Back to Careers
            </a>

        </div>
        """, 500

    finally:

        if cursor:
            cursor.close()

        if conn:
            conn.close()



# =====================================================
# APPLY FOR CONSTRUCTION VACANCY
# =====================================================

@app.route(
    "/construction/careers/<int:vacancy_id>/apply",
    methods=["GET", "POST"]
)
def apply_construction_vacancy(vacancy_id):

    conn = None
    cursor = None

    try:

        # =================================================
        # DATABASE CONNECTION
        # =================================================

        conn = get_db_connection()

        cursor = conn.cursor(dictionary=True)


        # =================================================
        # GET VACANCY
        # =================================================

        cursor.execute("""
            SELECT
                id,
                title,
                department,
                location,
                employment_type,
                experience,
                description,
                responsibilities,
                requirements,
                deadline,
                status
            FROM construction_job_vacancies
            WHERE id = %s
              AND status = 'open'
            LIMIT 1
        """, (vacancy_id,))

        vacancy = cursor.fetchone()


        if not vacancy:

            return "Vacancy not found", 404


        # =================================================
        # SHOW APPLICATION FORM
        # =================================================

        if request.method == "GET":

            return render_template(
                "construction_apply.html",
                vacancy=vacancy
            )


        # =================================================
        # GET APPLICATION DATA
        # =================================================

        full_name = request.form.get(
            "full_name",
            ""
        ).strip()

        email = request.form.get(
            "email",
            ""
        ).strip()

        phone = request.form.get(
            "phone",
            ""
        ).strip()

        cover_message = request.form.get(
            "cover_message",
            ""
        ).strip()

        country = request.form.get(
            "country",
            ""
        ).strip()

        years_of_experience = request.form.get(
            "years_of_experience",
            ""
        ).strip()


        # =================================================
        # VALIDATION
        # =================================================

        if not full_name or not email or not phone:

            flash(
                "Please complete all required fields.",
                "error"
            )

            return render_template(
                "construction_apply.html",
                vacancy=vacancy
            )


        # =================================================
        # GET CV
        # =================================================

        cv_file = request.files.get("cv")


        if not cv_file or not cv_file.filename:

            flash(
                "Please upload your CV.",
                "error"
            )

            return render_template(
                "construction_apply.html",
                vacancy=vacancy
            )


        # =================================================
        # CHECK CV EXTENSION
        # =================================================

        original_filename = secure_filename(
            cv_file.filename
        )

        extension = (
            original_filename.rsplit(".", 1)[1].lower()
            if "." in original_filename
            else ""
        )


        allowed_extensions = {
            "pdf",
            "doc",
            "docx"
        }


        if extension not in allowed_extensions:

            flash(
                "Only PDF, DOC and DOCX files are allowed.",
                "error"
            )

            return render_template(
                "construction_apply.html",
                vacancy=vacancy
            )


        # =================================================
        # CHECK FILE SIZE
        # =================================================

        cv_file.seek(0, 2)

        file_size = cv_file.tell()

        cv_file.seek(0)


        if file_size > 5 * 1024 * 1024:

            flash(
                "The CV file must not exceed 5MB.",
                "error"
            )

            return render_template(
                "construction_apply.html",
                vacancy=vacancy
            )


        # =================================================
        # UPLOAD CV TO CLOUDINARY
        # =================================================

        upload_result = cloudinary.uploader.upload(
            cv_file,
            resource_type="raw",
            public_id=(
                "construction/cv/"
                + str(uuid.uuid4())
                + "_"
                + os.path.splitext(original_filename)[0]
            ),
            format=extension
        )


        cv_url = upload_result.get("secure_url")


        if not cv_url:

            raise Exception(
                "Cloudinary did not return a CV URL."
            )


        # =================================================
        # SAVE APPLICATION TO DATABASE
        # =================================================

        insert_query = """
            INSERT INTO construction_job_applications
            (
                vacancy_id,
                applicant_name,
                email,
                phone,
                country,
                years_of_experience,
                cv_filename,
                cv_url,
                cover_letter,
                status
            )
            VALUES
            (
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s
            )
        """


        insert_values = (
            vacancy_id,
            full_name,
            email,
            phone,
            country,
            years_of_experience,
            original_filename,
            cv_url,
            cover_message,
            "new"
        )


        print("========================================")
        print("INSERTING CONSTRUCTION JOB APPLICATION")
        print("Vacancy ID:", vacancy_id)
        print("Applicant:", full_name)
        print("Email:", email)
        print("Phone:", phone)
        print("Country:", country)
        print("Experience:", years_of_experience)
        print("CV:", original_filename)
        print("========================================")


        cursor.execute(
            insert_query,
            insert_values
        )


        # =================================================
        # GET INSERTED ID
        # =================================================

        application_id = cursor.lastrowid


        if not application_id:

            raise Exception(
                "Database INSERT completed but no application ID was returned."
            )


        # =================================================
        # COMMIT DATABASE TRANSACTION
        # =================================================

        conn.commit()


        print("========================================")
        print("DATABASE COMMIT SUCCESSFUL")
        print("Application ID:", application_id)
        print("========================================")


        # =================================================
        # VERIFY APPLICATION WAS SAVED
        # =================================================

        verify_cursor = conn.cursor(dictionary=True)

        try:

            verify_cursor.execute(
                """
                SELECT
                    id,
                    vacancy_id,
                    applicant_name,
                    email,
                    phone,
                    country,
                    years_of_experience,
                    cv_filename,
                    cv_url,
                    cover_letter,
                    status,
                    created_at
                FROM construction_job_applications
                WHERE id = %s
                LIMIT 1
                """,
                (application_id,)
            )

            saved_application = verify_cursor.fetchone()

        finally:

            verify_cursor.close()


        if not saved_application:

            raise Exception(
                f"Application {application_id} was inserted but could not be verified after commit."
            )


        print("========================================")
        print("CONSTRUCTION JOB APPLICATION VERIFIED")
        print("Application ID:", saved_application["id"])
        print("Applicant:", saved_application["applicant_name"])
        print("Email:", saved_application["email"])
        print("========================================")


        # =================================================
        # ADMIN EMAIL
        # =================================================

        admin_email_result = send_email(

            "santospederson@gmail.com",

            f"New Job Application - {vacancy['title']}",

            f"""
            <h2>New Construction Job Application</h2>

            <hr>

            <p>
                <b>Application ID:</b>
                {application_id}
            </p>

            <p>
                <b>Position:</b>
                {vacancy['title']}
            </p>

            <p>
                <b>Department:</b>
                {vacancy.get('department') or 'N/A'}
            </p>

            <p>
                <b>Location:</b>
                {vacancy.get('location') or 'N/A'}
            </p>

            <hr>

            <h3>Applicant Information</h3>

            <p>
                <b>Name:</b>
                {full_name}
            </p>

            <p>
                <b>Email:</b>
                {email}
            </p>

            <p>
                <b>Phone:</b>
                {phone}
            </p>

            <p>
                <b>Country:</b>
                {country or 'N/A'}
            </p>

            <p>
                <b>Years of Experience:</b>
                {years_of_experience or 'N/A'}
            </p>

            <hr>

            <h3>Cover Message</h3>

            <p>
                {cover_message or 'No cover message provided.'}
            </p>

            <hr>

            <h3>CV</h3>

            <p>
                <b>File:</b>
                {original_filename}
            </p>

            <p>
                <a
                    href="{cv_url}"
                    target="_blank"
                    style="
                        display:inline-block;
                        padding:12px 20px;
                        background:#3F6B57;
                        color:#ffffff;
                        text-decoration:none;
                        border-radius:5px;
                    "
                >
                    View / Download CV
                </a>
            </p>

            <hr>

            <p>
                This application was submitted through
                the Prestigious Trading & Constructions website.
            </p>
            """
        )


        print(
            "APPLICATION ADMIN EMAIL STATUS:",
            admin_email_result
        )


        # =================================================
        # APPLICANT CONFIRMATION EMAIL
        # =================================================

        applicant_email_result = send_email(

            email,

            f"Application Received - {vacancy['title']}",

            f"""
            <h2>Hello {full_name},</h2>

            <p>
                Thank you for applying for the
                <b>{vacancy['title']}</b> position at
                <b>Prestigious Trading & Constructions</b>.
            </p>

            <p>
                We have successfully received your
                application and CV.
            </p>

            <hr>

            <p>
                <b>Position:</b>
                {vacancy['title']}
            </p>

            <p>
                <b>Application Reference:</b>
                #{application_id}
            </p>

            <p>
                <b>CV:</b>
                {original_filename}
            </p>

            <hr>

            <p>
                Our recruitment team will review your
                application. If your qualifications and
                experience match our requirements, we will
                contact you regarding the next stage of
                the recruitment process.
            </p>

            <p>
                Please keep your application reference
                for your records.
            </p>

            <br>

            <p>
                Regards,<br>
                <b>Recruitment Team</b><br>
                Prestigious Trading & Constructions
            </p>
            """
        )


        print(
            "APPLICANT CONFIRMATION EMAIL STATUS:",
            applicant_email_result
        )


        # =================================================
        # SUCCESS
        # =================================================

        flash(
            "Application submitted successfully. A confirmation email has been sent to you.",
            "success"
        )


        return redirect(
            url_for(
                "construction_vacancy_details",
                id=vacancy_id
            )
        )


    # =====================================================
    # ERROR HANDLING
    # =====================================================

    except Exception as e:

        if conn:

            try:
                conn.rollback()
            except Exception:
                pass


        print("========================================")
        print("CONSTRUCTION APPLICATION ERROR")
        print("========================================")
        print("Vacancy ID:", vacancy_id)
        print("Error Type:", type(e).__name__)
        print("Error:", str(e))
        print("========================================")


        flash(
            "Unable to submit your application. Please try again.",
            "error"
        )


        return render_template(
            "construction_apply.html",
            vacancy=vacancy if "vacancy" in locals() else None
        ), 500


    # =====================================================
    # CLOSE DATABASE
    # =====================================================

    finally:

        if cursor:

            try:
                cursor.close()
            except Exception:
                pass

        if conn:

            try:
                conn.close()
            except Exception:
                pass










# ============================================================
# CONSTRUCTION JOB APPLICATIONS
# ============================================================

@app.route(
    "/admin/construction-applications",
    methods=["GET"]
)
def construction_applications():

    # =========================================================
    # CHECK CONSTRUCTION ADMIN
    # =========================================================

    if "construction_admin_id" not in session:
        return redirect(
            url_for("admin_login")
        )

    conn = None
    cursor = None

    try:

        # =====================================================
        # DATABASE CONNECTION
        # =====================================================

        conn = get_db_connection()

        cursor = conn.cursor(
            dictionary=True
        )

        # =====================================================
        # SEARCH
        # =====================================================

        search = request.args.get(
            "search",
            ""
        ).strip()

        # =====================================================
        # PAGE
        # =====================================================

        try:

            page = int(
                request.args.get(
                    "page",
                    1
                )
            )

        except (ValueError, TypeError):

            page = 1

        if page < 1:
            page = 1

        per_page = 10

        offset = (
            page - 1
        ) * per_page

        # =====================================================
        # BASE QUERY
        # =====================================================

        base_query = """
            FROM construction_job_applications a

            LEFT JOIN construction_job_vacancies v
                ON a.vacancy_id = v.id
        """

        # =====================================================
        # SEARCH CONDITION
        # =====================================================

        search_condition = ""

        search_params = []

        if search:

            search_condition = """
                WHERE
                    a.applicant_name LIKE %s
                    OR a.email LIKE %s
                    OR a.phone LIKE %s
                    OR a.country LIKE %s
                    OR a.years_of_experience LIKE %s
                    OR v.title LIKE %s
            """

            search_value = f"%{search}%"

            search_params = [
                search_value,
                search_value,
                search_value,
                search_value,
                search_value,
                search_value
            ]

        # =====================================================
        # COUNT APPLICATIONS
        # =====================================================

        cursor.execute(
            f"""
                SELECT COUNT(*) AS total

                {base_query}

                {search_condition}
            """,
            search_params
        )

        count_result = cursor.fetchone()

        total_applications = (
            count_result["total"]
            if count_result
            else 0
        )

        # =====================================================
        # TOTAL PAGES
        # =====================================================

        total_pages = (
            (total_applications + per_page - 1)
            // per_page
        )

        # =====================================================
        # GET APPLICATIONS
        # =====================================================

        cursor.execute(
            f"""
                SELECT
                    a.id,
                    a.vacancy_id,

                    a.applicant_name,
                    a.email,
                    a.phone,
                    a.country,
                    a.years_of_experience,

                    a.cv_filename,
                    a.cv_url,

                    a.cover_letter,

                    a.status,

                    a.created_at,
                    a.updated_at,

                    v.title AS vacancy_title,
                    v.department AS vacancy_department

                {base_query}

                {search_condition}

                ORDER BY
                    a.created_at DESC

                LIMIT %s OFFSET %s
            """,
            search_params + [
                per_page,
                offset
            ]
        )

        applications = cursor.fetchall()

        # =====================================================
        # RENDER
        # =====================================================

        return render_template(
            "construction_admin/applications.html",

            applications=applications,

            search=search,

            page=page,

            per_page=per_page,

            total_applications=total_applications,

            total_pages=total_pages
        )

    except Exception as e:

        if conn:
            conn.rollback()

        print("========================================")
        print("CONSTRUCTION APPLICATIONS ERROR")
        print("Error Type:", type(e).__name__)
        print("Error:", str(e))
        print("========================================")

        flash(
            "Unable to load job applications.",
            "danger"
        )

        return redirect(
            url_for("admin_login")
        )

    finally:

        if cursor:
            cursor.close()

        if conn:
            conn.close()


# ============================================================
# CONSTRUCTION JOB APPLICATION DETAILS
# ============================================================

@app.route(
    "/admin/construction-applications/<int:id>",
    methods=["GET"]
)
def construction_application_details(id):

    # =========================================================
    # CHECK CONSTRUCTION ADMIN
    # =========================================================

    if "construction_admin_id" not in session:
        return redirect(
            url_for("admin_login")
        )

    conn = None
    cursor = None

    try:

        # =====================================================
        # DATABASE CONNECTION
        # =====================================================

        conn = get_db_connection()

        cursor = conn.cursor(
            dictionary=True
        )

        # =====================================================
        # GET APPLICATION
        # =====================================================

        cursor.execute(
            """
            SELECT
                a.id,
                a.vacancy_id,

                a.applicant_name,
                a.email,
                a.phone,
                a.country,
                a.years_of_experience,

                a.cv_filename,
                a.cv_url,

                a.cover_letter,

                a.status,

                a.created_at,
                a.updated_at,

                v.title AS vacancy_title,
                v.department AS vacancy_department,
                v.location AS vacancy_location,
                v.employment_type AS vacancy_employment_type,
                v.experience AS vacancy_experience,
                v.description AS vacancy_description,
                v.responsibilities AS vacancy_responsibilities,
                v.requirements AS vacancy_requirements,
                v.deadline AS vacancy_deadline

            FROM construction_job_applications a

            LEFT JOIN construction_job_vacancies v
                ON a.vacancy_id = v.id

            WHERE a.id = %s

            LIMIT 1
            """,
            (id,)
        )

        application = cursor.fetchone()

        # =====================================================
        # APPLICATION NOT FOUND
        # =====================================================

        if not application:

            flash(
                "Construction job application not found.",
                "danger"
            )

            return redirect(
                url_for(
                    "construction_applications"
                )
            )

        # =====================================================
        # RENDER DETAILS PAGE
        # =====================================================

        return render_template(
            "construction_admin/application_details.html",
            application=application
        )

    except Exception as e:

        if conn:

            try:
                conn.rollback()
            except Exception:
                pass

        print("========================================")
        print("CONSTRUCTION APPLICATION DETAILS ERROR")
        print("Error Type:", type(e).__name__)
        print("Error:", str(e))
        print("========================================")

        flash(
            "Unable to load application details.",
            "danger"
        )

        return redirect(
            url_for(
                "construction_applications"
            )
        )

    finally:

        if cursor:

            try:
                cursor.close()
            except Exception:
                pass

        if conn:

            try:
                conn.close()
            except Exception:
                pass


# =====================================================
# DELETE APPLICATION ROUTE
# =====================================================
@app.route(
    "/admin/construction-applications/delete/<int:id>",
    methods=["POST"]
)
def delete_construction_application(id):

    if "construction_admin_id" not in session:
        return redirect(url_for("admin_login"))

    conn = None
    cursor = None

    try:

        conn = get_db_connection()

        cursor = conn.cursor(dictionary=True)

        cursor.execute(
            """
            SELECT
                id,
                applicant_name
            FROM construction_job_applications
            WHERE id = %s
            LIMIT 1
            """,
            (id,)
        )

        application = cursor.fetchone()

        if not application:

            flash(
                "Application not found.",
                "danger"
            )

            return redirect(
                url_for("construction_applications")
            )

        cursor.execute(
            """
            DELETE FROM construction_job_applications
            WHERE id = %s
            """,
            (id,)
        )

        conn.commit()

        flash(
            "Application deleted successfully.",
            "success"
        )

        return redirect(
            url_for("construction_applications")
        )

    except Exception as e:

        if conn:
            conn.rollback()

        print("========================================")
        print("DELETE CONSTRUCTION APPLICATION ERROR")
        print(type(e).__name__)
        print(str(e))
        print("========================================")

        flash(
            "Unable to delete the application.",
            "danger"
        )

        return redirect(
            url_for("construction_applications")
        )

    finally:

        if cursor:
            cursor.close()

        if conn:
            conn.close()









# ============================================================
# CONSTRUCTION STAFF QID MANAGEMENT
# ============================================================


# ============================================================
# VIEW ALL STAFF QIDS
# ============================================================

# =========================================================
# CONSTRUCTION QID MANAGEMENT
# =========================================================

@app.route(
    "/admin/construction-qids",
    methods=["GET"]
)
def construction_qids():

    # =========================================================
    # CHECK CONSTRUCTION ADMIN
    # =========================================================

    if "construction_admin_id" not in session:

        return redirect(
            url_for("admin_login")
        )


    conn = None
    cursor = None


    try:

        # =====================================================
        # DATABASE CONNECTION
        # =====================================================

        conn = get_db_connection()

        cursor = conn.cursor(
            dictionary=True
        )


        # =====================================================
        # GET ALL STAFF QIDS
        # =====================================================

        cursor.execute(
            """
            SELECT

                id,

                staff_name,

                qid_number,

                qid_issue_date,

                qid_expiry_date,

                staff_email,

                manager_name,

                manager_email,

                department,

                position,

                qid_document,

                notes,

                status,

                created_at,

                updated_at

            FROM construction_staff_qids

            ORDER BY
                qid_expiry_date ASC,

                staff_name ASC
            """
        )


        qids = cursor.fetchall()


        # =====================================================
        # CURRENT DATE
        # =====================================================

        today = datetime.now().date()


        # =====================================================
        # SUMMARY COUNTERS
        # =====================================================

        total_qids = len(qids)

        valid_qids = 0

        warning_qids = 0

        critical_qids = 0

        expired_qids = 0


        # =====================================================
        # CALCULATE EXPIRY STATUS
        # =====================================================

        for qid in qids:

            expiry_date = qid.get(
                "qid_expiry_date"
            )


            # -------------------------------------------------
            # NO EXPIRY DATE
            # -------------------------------------------------

            if not expiry_date:

                qid["days_remaining"] = None

                qid["expiry_status"] = "unknown"

                continue


            # -------------------------------------------------
            # CALCULATE DAYS REMAINING
            # -------------------------------------------------

            days_remaining = (
                expiry_date - today
            ).days


            qid["days_remaining"] = days_remaining


            # -------------------------------------------------
            # EXPIRED
            # -------------------------------------------------

            if days_remaining < 0:

                qid["expiry_status"] = "expired"

                expired_qids += 1


            # -------------------------------------------------
            # CRITICAL
            # 0 - 30 DAYS
            # -------------------------------------------------

            elif days_remaining <= 30:

                qid["expiry_status"] = "critical"

                critical_qids += 1


            # -------------------------------------------------
            # WARNING
            # 31 - 60 DAYS
            # -------------------------------------------------

            elif days_remaining <= 60:

                qid["expiry_status"] = "warning"

                warning_qids += 1


            # -------------------------------------------------
            # VALID
            # MORE THAN 60 DAYS
            # -------------------------------------------------

            else:

                qid["expiry_status"] = "valid"

                valid_qids += 1


        # =====================================================
        # RENDER PAGE
        # =====================================================

        return render_template(

            "construction_admin/construction_qids.html",

            qids=qids,

            total_qids=total_qids,

            valid_qids=valid_qids,

            warning_qids=warning_qids,

            critical_qids=critical_qids,

            expired_qids=expired_qids

        )


    except Exception as e:

        # =====================================================
        # ERROR LOG
        # =====================================================

        print("========================================")

        print(
            "CONSTRUCTION QIDS ERROR"
        )

        print(
            "Error Type:",
            type(e).__name__
        )

        print(
            "Error:",
            str(e)
        )

        print("========================================")


        flash(
            "Unable to load staff QID records.",
            "danger"
        )


        return redirect(
            url_for("admin_dashboard")
        )


    finally:

        # =====================================================
        # CLOSE CURSOR
        # =====================================================

        if cursor:

            cursor.close()


        # =====================================================
        # CLOSE CONNECTION
        # =====================================================

        if conn:

            conn.close()


# ============================================================
# ADD STAFF QID
# ============================================================

@app.route(
    "/admin/construction-qids/add",
    methods=["GET", "POST"]
)
def add_construction_qid():

    # =========================================================
    # CHECK CONSTRUCTION ADMIN
    # =========================================================

    if "construction_admin_id" not in session:

        return redirect(
            url_for("admin_login")
        )


    if request.method == "POST":

        # =====================================================
        # GET FORM DATA
        # =====================================================

        staff_name = request.form.get(
            "staff_name",
            ""
        ).strip()


        qid_number = request.form.get(
            "qid_number",
            ""
        ).strip()


        qid_issue_date = request.form.get(
            "qid_issue_date",
            ""
        ).strip()


        qid_expiry_date = request.form.get(
            "qid_expiry_date",
            ""
        ).strip()


        staff_email = request.form.get(
            "staff_email",
            ""
        ).strip()


        manager_name = request.form.get(
            "manager_name",
            ""
        ).strip()


        manager_email = request.form.get(
            "manager_email",
            ""
        ).strip()


        department = request.form.get(
            "department",
            ""
        ).strip()


        position = request.form.get(
            "position",
            ""
        ).strip()


        notes = request.form.get(
            "notes",
            ""
        ).strip()


        # =====================================================
        # GET QID DOCUMENT
        # =====================================================

        qid_document_file = request.files.get(
            "qid_document"
        )


        # =====================================================
        # VALIDATION
        # =====================================================

        if not staff_name:

            flash(
                "Staff name is required.",
                "danger"
            )

            return render_template(
                "construction_admin/construction_add_qid.html"
            )


        if not qid_number:

            flash(
                "QID number is required.",
                "danger"
            )

            return render_template(
                "construction_admin/construction_add_qid.html"
            )


        if not qid_expiry_date:

            flash(
                "QID expiry date is required.",
                "danger"
            )

            return render_template(
                "construction_admin/construction_add_qid.html"
            )


        if not staff_email:

            flash(
                "Staff email is required.",
                "danger"
            )

            return render_template(
                "construction_admin/construction_add_qid.html"
            )


        # =====================================================
        # QID DOCUMENT VALIDATION
        # =====================================================

        if not qid_document_file:

            flash(
                "Please upload the staff member's QID document.",
                "danger"
            )

            return render_template(
                "construction_admin/construction_add_qid.html"
            )


        if not qid_document_file.filename:

            flash(
                "Please select a QID document.",
                "danger"
            )

            return render_template(
                "construction_admin/construction_add_qid.html"
            )


        # =====================================================
        # ALLOWED FILE TYPES
        # =====================================================

        allowed_extensions = {
            "pdf",
            "jpg",
            "jpeg",
            "png"
        }


        original_filename = secure_filename(
            qid_document_file.filename
        )


        if "." not in original_filename:

            flash(
                "Invalid QID document file.",
                "danger"
            )

            return render_template(
                "construction_admin/construction_add_qid.html"
            )


        file_extension = (
            original_filename
           .rsplit(".", 1)[1]
            .lower()
        )


        if file_extension not in allowed_extensions:

            flash(
                "Invalid QID document format. "
                "Only PDF, JPG, JPEG and PNG files are allowed.",
                "danger"
            )

            return render_template(
                "construction_admin/construction_add_qid.html"
            )


        # =====================================================
        # DATABASE
        # =====================================================

        conn = None
        cursor = None


        try:

            # =================================================
            # UPLOAD DOCUMENT TO CLOUDINARY
            # =================================================

            upload_result = cloudinary.uploader.upload(
                qid_document_file,

                folder="prestigious_construction/qids",

                public_id=(
                    "qid_"
                    + str(uuid.uuid4())
                ),

                resource_type="auto"
            )


            # =================================================
            # GET CLOUDINARY URL
            # =================================================

            qid_document_url = upload_result.get(
                "secure_url"
            )


            if not qid_document_url:

                raise Exception(
                    "Cloudinary did not return a secure URL."
                )


            # =================================================
            # DATABASE CONNECTION
            # =================================================

            conn = get_db_connection()

            cursor = conn.cursor()


            # =================================================
            # INSERT QID
            # =================================================

            cursor.execute(
                """
                INSERT INTO construction_staff_qids
                (
                    staff_name,
                    qid_number,
                    qid_issue_date,
                    qid_expiry_date,
                    staff_email,
                    manager_name,
                    manager_email,
                    department,
                    position,
                    qid_document,
                    notes,
                    status
                )

                VALUES
                (
                    %s,
                    %s,
                    NULLIF(%s, ''),
                    %s,
                    %s,
                    NULLIF(%s, ''),
                    NULLIF(%s, ''),
                    NULLIF(%s, ''),
                    NULLIF(%s, ''),
                    %s,
                    NULLIF(%s, ''),
                    'active'
                )
                """,

                (
                    staff_name,
                    qid_number,
                    qid_issue_date,
                    qid_expiry_date,
                    staff_email,
                    manager_name,
                    manager_email,
                    department,
                    position,
                    qid_document_url,
                    notes
                )
            )


            # =================================================
            # COMMIT
            # =================================================

            conn.commit()


            # =================================================
            # SUCCESS
            # =================================================

            flash(
                "Staff QID and document added successfully.",
                "success"
            )


            return redirect(
                url_for(
                    "construction_qids"
                )
            )


        except Exception as e:

            # =================================================
            # ROLLBACK DATABASE
            # =================================================

            if conn:

                conn.rollback()


            print("========================================")
            print("ADD CONSTRUCTION QID ERROR")
            print("Error Type:", type(e).__name__)
            print("Error:", str(e))
            print("========================================")


            # =================================================
            # DUPLICATE QID
            # =================================================

            if "Duplicate entry" in str(e):

                flash(
                    "This QID number already exists.",
                    "danger"
                )

            else:

                flash(
                    "Unable to add staff QID or upload document.",
                    "danger"
                )


        finally:

            if cursor:

                cursor.close()


            if conn:

                conn.close()


    # =========================================================
    # DISPLAY ADD PAGE
    # =========================================================

    return render_template(
        "construction_admin/construction_add_qid.html"
    )



# ============================================================
# VIEW STAFF QID DETAILS
# ============================================================

# =========================================================
# CONSTRUCTION QID DETAILS
# =========================================================

@app.route(
    "/admin/construction-qids/details/<int:id>",
    methods=["GET"]
)
def construction_qid_details(id):

    # =========================================================
    # CHECK CONSTRUCTION ADMIN
    # =========================================================

    if "construction_admin_id" not in session:

        return redirect(
            url_for("admin_login")
        )


    conn = None
    cursor = None


    try:

        # =====================================================
        # DATABASE CONNECTION
        # =====================================================

        conn = get_db_connection()

        cursor = conn.cursor(
            dictionary=True
        )


        # =====================================================
        # GET QID RECORD
        # =====================================================

        cursor.execute(
            """
            SELECT

                id,

                staff_name,

                qid_number,

                qid_issue_date,

                qid_expiry_date,

                staff_email,

                manager_name,

                manager_email,

                department,

                position,

                qid_document,

                notes,

                status,

                created_at,

                updated_at

            FROM construction_staff_qids

            WHERE id = %s

            LIMIT 1
            """,

            (id,)
        )


        qid = cursor.fetchone()


        # =====================================================
        # NOT FOUND
        # =====================================================

        if not qid:

            flash(
                "Staff QID record not found.",
                "danger"
            )

            return redirect(
                url_for(
                    "construction_qids"
                )
            )


        # =====================================================
        # CALCULATE EXPIRY INFORMATION
        # =====================================================

        today = datetime.utcnow().date()

        expiry_date = qid.get(
            "qid_expiry_date"
        )


        if expiry_date:

            days_remaining = (
                expiry_date - today
            ).days


            # ================================================
            # EXPIRED
            # ================================================

            if days_remaining < 0:

                expiry_status = "expired"


            # ================================================
            # CRITICAL
            # 0 - 30 DAYS
            # ================================================

            elif days_remaining <= 30:

                expiry_status = "critical"


            # ================================================
            # WARNING
            # 31 - 90 DAYS
            # ================================================

            elif days_remaining <= 90:

                expiry_status = "warning"


            # ================================================
            # VALID
            # ================================================

            else:

                expiry_status = "valid"


        else:

            days_remaining = None

            expiry_status = "unknown"


        # =====================================================
        # ADD CALCULATED VALUES TO QID
        # =====================================================

        qid["days_remaining"] = days_remaining

        qid["expiry_status"] = expiry_status


        # =====================================================
        # RENDER DETAILS PAGE
        # =====================================================

        return render_template(
            "construction_admin/construction_qid_details.html",

            qid=qid
        )


    except Exception as e:

        # =====================================================
        # ERROR
        # =====================================================

        print("========================================")
        print("CONSTRUCTION QID DETAILS ERROR")
        print("Error Type:", type(e).__name__)
        print("Error:", str(e))
        print("========================================")


        flash(
            "Unable to load staff QID details.",
            "danger"
        )


        return redirect(
            url_for(
                "construction_qids"
            )
        )


    finally:

        if cursor:

            cursor.close()


        if conn:

            conn.close()


# ============================================================
# EDIT STAFF QID
# ============================================================

# =========================================================
# EDIT CONSTRUCTION STAFF QID
# =========================================================

@app.route(
    "/admin/construction-qids/edit/<int:id>",
    methods=["GET", "POST"]
)
def edit_construction_qid(id):

    # =========================================================
    # CHECK CONSTRUCTION ADMIN
    # =========================================================

    if "construction_admin_id" not in session:

        return redirect(
            url_for("admin_login")
        )


    conn = None
    cursor = None


    try:

        # =====================================================
        # DATABASE CONNECTION
        # =====================================================

        conn = get_db_connection()

        cursor = conn.cursor(
            dictionary=True
        )


        # =====================================================
        # GET EXISTING QID
        # =====================================================

        cursor.execute(
            """
            SELECT *

            FROM construction_staff_qids

            WHERE id = %s

            LIMIT 1
            """,

            (id,)
        )


        qid = cursor.fetchone()


        # =====================================================
        # RECORD NOT FOUND
        # =====================================================

        if not qid:

            flash(
                "Staff QID record not found.",
                "danger"
            )

            return redirect(
                url_for(
                    "construction_qids"
                )
            )


        # =====================================================
        # POST / UPDATE
        # =====================================================

        if request.method == "POST":


            # =================================================
            # GET FORM DATA
            # =================================================

            staff_name = request.form.get(
                "staff_name",
                ""
            ).strip()


            qid_number = request.form.get(
                "qid_number",
                ""
            ).strip()


            qid_issue_date = request.form.get(
                "qid_issue_date",
                ""
            ).strip()


            qid_expiry_date = request.form.get(
                "qid_expiry_date",
                ""
            ).strip()


            staff_email = request.form.get(
                "staff_email",
                ""
            ).strip()


            manager_name = request.form.get(
                "manager_name",
                ""
            ).strip()


            manager_email = request.form.get(
                "manager_email",
                ""
            ).strip()


            department = request.form.get(
                "department",
                ""
            ).strip()


            position = request.form.get(
                "position",
                ""
            ).strip()


            status = request.form.get(
                "status",
                "active"
            ).strip().lower()


            notes = request.form.get(
                "notes",
                ""
            ).strip()


            # =================================================
            # GET NEW QID DOCUMENT
            # =================================================

            qid_document_file = request.files.get(
                "qid_document"
            )


            # =================================================
            # VALIDATION
            # =================================================

            if not staff_name:

                flash(
                    "Staff name is required.",
                    "danger"
                )

                return render_template(
                    "construction_admin/construction_edit_qid.html",
                    qid=qid
                )


            if not qid_number:

                flash(
                    "QID number is required.",
                    "danger"
                )

                return render_template(
                    "construction_admin/construction_edit_qid.html",
                    qid=qid
                )


            if not qid_expiry_date:

                flash(
                    "QID expiry date is required.",
                    "danger"
                )

                return render_template(
                    "construction_admin/construction_edit_qid.html",
                    qid=qid
                )


            if not staff_email:

                flash(
                    "Staff email is required.",
                    "danger"
                )

                return render_template(
                    "construction_admin/construction_edit_qid.html",
                    qid=qid
                )


            # =================================================
            # STATUS VALIDATION
            # =================================================

            if status not in [
                "active",
                "expired",
                "inactive"
            ]:

                status = "active"


            # =================================================
            # DOCUMENT VARIABLES
            # =================================================

            qid_document_url = qid.get(
                "qid_document"
            )


            old_qid_document_url = qid_document_url


            # =================================================
            # CHECK IF NEW DOCUMENT WAS PROVIDED
            # =================================================

            new_document_uploaded = False


            if qid_document_file:

                if qid_document_file.filename:

                    new_document_uploaded = True


            # =================================================
            # HANDLE NEW DOCUMENT
            # =================================================

            if new_document_uploaded:


                # =============================================
                # SECURE ORIGINAL FILENAME
                # =============================================

                original_filename = secure_filename(
                    qid_document_file.filename
                )


                # =============================================
                # CHECK EXTENSION
                # =============================================

                if "." not in original_filename:

                    flash(
                        "Invalid QID document file.",
                        "danger"
                    )

                    return render_template(
                        "construction_admin/construction_edit_qid.html",
                        qid=qid
                    )


                file_extension = (
                    original_filename
                    .rsplit(".", 1)[1]
                    .lower()
                )


                # =============================================
                # ALLOWED FILE TYPES
                # =============================================

                allowed_extensions = {
                    "pdf",
                    "jpg",
                    "jpeg",
                    "png"
                }


                if file_extension not in allowed_extensions:

                    flash(
                        "Invalid QID document format. "
                        "Only PDF, JPG, JPEG and PNG files are allowed.",
                        "danger"
                    )

                    return render_template(
                        "construction_admin/construction_edit_qid.html",
                        qid=qid
                    )


                # =============================================
                # UPLOAD NEW DOCUMENT TO CLOUDINARY
                # =============================================

                upload_result = cloudinary.uploader.upload(

                    qid_document_file,

                    folder="prestigious_construction/qids",

                    public_id=(
                        "qid_"
                        + str(uuid.uuid4())
                    ),

                    resource_type="auto"
                )


                # =============================================
                # GET NEW CLOUDINARY URL
                # =============================================

                qid_document_url = upload_result.get(
                    "secure_url"
                )


                if not qid_document_url:

                    raise Exception(
                        "Cloudinary did not return a secure URL."
                    )


            # =================================================
            # UPDATE DATABASE
            # =================================================

            cursor.execute(
                """
                UPDATE construction_staff_qids

                SET

                    staff_name = %s,

                    qid_number = %s,

                    qid_issue_date =
                        NULLIF(%s, ''),

                    qid_expiry_date = %s,

                    staff_email = %s,

                    manager_name =
                        NULLIF(%s, ''),

                    manager_email =
                        NULLIF(%s, ''),

                    department =
                        NULLIF(%s, ''),

                    position =
                        NULLIF(%s, ''),

                    qid_document = %s,

                    notes =
                        NULLIF(%s, ''),

                    status = %s,

                    updated_at = CURRENT_TIMESTAMP

                WHERE id = %s
                """,

                (
                    staff_name,
                    qid_number,
                    qid_issue_date,
                    qid_expiry_date,
                    staff_email,
                    manager_name,
                    manager_email,
                    department,
                    position,
                    qid_document_url,
                    notes,
                    status,
                    id
                )
            )


            # =================================================
            # COMMIT
            # =================================================

            conn.commit()


            # =================================================
            # SUCCESS MESSAGE
            # =================================================

            if new_document_uploaded:

                flash(
                    "Staff QID and document updated successfully.",
                    "success"
                )

            else:

                flash(
                    "Staff QID updated successfully.",
                    "success"
                )


            # =================================================
            # RETURN TO QID MANAGEMENT
            # =================================================

            return redirect(
                url_for(
                    "construction_qids"
                )
            )


        # =====================================================
        # DISPLAY EDIT PAGE
        # =====================================================

        return render_template(

            "construction_admin/construction_edit_qid.html",

            qid=qid
        )


    except Exception as e:

        # =====================================================
        # ROLLBACK
        # =====================================================

        if conn:

            conn.rollback()


        # =====================================================
        # ERROR LOG
        # =====================================================

        print("========================================")

        print(
            "EDIT CONSTRUCTION QID ERROR"
        )

        print(
            "Error Type:",
            type(e).__name__
        )

        print(
            "Error:",
            str(e)
        )

        print("========================================")


        # =====================================================
        # DUPLICATE QID
        # =====================================================

        if "Duplicate entry" in str(e):

            flash(
                "This QID number already exists.",
                "danger"
            )

        else:

            flash(
                "Unable to update staff QID.",
                "danger"
            )


        return redirect(
            url_for(
                "construction_qids"
            )
        )


    finally:

        # =====================================================
        # CLOSE CURSOR
        # =====================================================

        if cursor:

            cursor.close()


        # =====================================================
        # CLOSE CONNECTION
        # =====================================================

        if conn:

            conn.close()


# ============================================================
# DELETE STAFF QID
# ============================================================

@app.route(
    "/admin/construction-qids/delete/<int:id>",
    methods=["POST"]
)
def delete_construction_qid(id):

    # =========================================================
    # CHECK CONSTRUCTION ADMIN
    # =========================================================

    if "construction_admin_id" not in session:

        return redirect(
            url_for("admin_login")
        )


    conn = None
    cursor = None


    try:

        conn = get_db_connection()

        cursor = conn.cursor(
            dictionary=True
        )


        # =====================================================
        # GET RECORD
        # =====================================================

        cursor.execute(
            """
            SELECT

                id,

                staff_name

            FROM construction_staff_qids

            WHERE id = %s

            LIMIT 1
            """,

            (id,)
        )


        qid = cursor.fetchone()


        if not qid:

            flash(
                "Staff QID record not found.",
                "danger"
            )

            return redirect(
                url_for(
                    "construction_qids"
                )
            )


        # =====================================================
        # DELETE
        # =====================================================

        cursor.execute(
            """
            DELETE FROM construction_staff_qids

            WHERE id = %s
            """,

            (id,)
        )


        conn.commit()


        # =====================================================
        # SUCCESS
        # =====================================================

        flash(
            f'QID record for "{qid["staff_name"]}" '
            'was deleted successfully.',
            "success"
        )


        return redirect(
            url_for(
                "construction_qids"
            )
        )


    except Exception as e:

        if conn:

            conn.rollback()


        print("========================================")
        print("DELETE CONSTRUCTION QID ERROR")
        print("Error Type:", type(e).__name__)
        print("Error:", str(e))
        print("========================================")


        flash(
            "Unable to delete staff QID.",
            "danger"
        )


        return redirect(
            url_for(
                "construction_qids"
            )
        )


    finally:

        if cursor:

            cursor.close()


        if conn:

            conn.close()








# ==========================================================
# PURCHASE DEPARTMENT - PURCHASE DASHBOARD
# ==========================================================

@app.route("/construction/purchase/dashboard")
def construction_purchase_dashboard():

    # ==================================================
    # LOGIN PROTECTION
    # ==================================================

    if "construction_admin_id" not in session:
        flash(
            "Please sign in to access the Purchase Department.",
            "warning"
        )
        return redirect(url_for("admin_login"))

    # ==================================================
    # ROLE PROTECTION
    # ==================================================

    role = (
        session.get("construction_admin_role") or ""
    ).strip().lower()

    if role != "purchase":
        flash(
            "You are not authorized to access the Purchase Department.",
            "danger"
        )

        # construction_role_dashboard does not exist
        # in the current application, so use the existing
        # login endpoint instead.
        return redirect(url_for("admin_login"))

    conn = None
    cursor = None

    try:

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        # ==================================================
        # MATERIAL REQUEST STATISTICS
        # ==================================================

        cursor.execute("""
            SELECT COUNT(*) AS total
            FROM construction_purchase_material_requests
        """)
        total_material_requests = (
            cursor.fetchone()["total"] or 0
        )

        cursor.execute("""
            SELECT COUNT(*) AS total
            FROM construction_purchase_material_requests
            WHERE status = 'Pending Engineer Review'
        """)
        pending_engineer_review = (
            cursor.fetchone()["total"] or 0
        )

        cursor.execute("""
            SELECT COUNT(*) AS total
            FROM construction_purchase_material_requests
            WHERE status = 'Engineer Correction Required'
        """)
        engineer_correction_required = (
            cursor.fetchone()["total"] or 0
        )

        cursor.execute("""
            SELECT COUNT(*) AS total
            FROM construction_purchase_material_requests
            WHERE status = 'Pending Manager Approval'
        """)
        pending_manager_approval = (
            cursor.fetchone()["total"] or 0
        )

        cursor.execute("""
            SELECT COUNT(*) AS total
            FROM construction_purchase_material_requests
            WHERE status = 'Manager Correction Required'
        """)
        manager_correction_required = (
            cursor.fetchone()["total"] or 0
        )

        cursor.execute("""
            SELECT COUNT(*) AS total
            FROM construction_purchase_material_requests
            WHERE status = 'Approved'
        """)
        approved_material_requests = (
            cursor.fetchone()["total"] or 0
        )

        cursor.execute("""
            SELECT COUNT(*) AS total
            FROM construction_purchase_material_requests
            WHERE status = 'Declined'
        """)
        declined_material_requests = (
            cursor.fetchone()["total"] or 0
        )

        cursor.execute("""
            SELECT COUNT(*) AS total
            FROM construction_purchase_material_requests
            WHERE status IN (
                'Approved',
                'Pending Procurement'
            )
        """)
        pending_procurement = (
            cursor.fetchone()["total"] or 0
        )

        cursor.execute("""
            SELECT COUNT(*) AS total
            FROM construction_purchase_material_requests
            WHERE status = 'Completed'
        """)
        completed_material_requests = (
            cursor.fetchone()["total"] or 0
        )

        # ==================================================
        # PROCUREMENT STATISTICS
        # ==================================================

        cursor.execute("""
            SELECT COUNT(*) AS total
            FROM construction_purchase_procurement
        """)
        total_procurement = (
            cursor.fetchone()["total"] or 0
        )

        cursor.execute("""
            SELECT COUNT(*) AS total
            FROM construction_purchase_procurement
            WHERE procurement_method = 'Card'
        """)
        card_procurement = (
            cursor.fetchone()["total"] or 0
        )

        cursor.execute("""
            SELECT COUNT(*) AS total
            FROM construction_purchase_procurement
            WHERE procurement_method = 'Quotation'
        """)
        quotation_procurement = (
            cursor.fetchone()["total"] or 0
        )

        cursor.execute("""
            SELECT COUNT(*) AS total
            FROM construction_purchase_procurement
            WHERE status = 'Pending Procurement Documents'
        """)
        pending_procurement_documents = (
            cursor.fetchone()["total"] or 0
        )

        cursor.execute("""
            SELECT COUNT(*) AS total
            FROM construction_purchase_procurement
            WHERE status = 'Procurement Correction Required'
        """)
        procurement_correction_required = (
            cursor.fetchone()["total"] or 0
        )

        cursor.execute("""
            SELECT COUNT(*) AS total
            FROM construction_purchase_procurement
            WHERE status = 'Pending Account Review'
        """)
        pending_account_review = (
            cursor.fetchone()["total"] or 0
        )

        cursor.execute("""
            SELECT COUNT(*) AS total
            FROM construction_purchase_procurement
            WHERE status = 'Account Correction Required'
        """)
        account_correction_required = (
            cursor.fetchone()["total"] or 0
        )

        cursor.execute("""
            SELECT COUNT(*) AS total
            FROM construction_purchase_procurement
            WHERE status = 'Pending Manager Review'
        """)
        procurement_pending_manager = (
            cursor.fetchone()["total"] or 0
        )

        cursor.execute("""
            SELECT COUNT(*) AS total
            FROM construction_purchase_procurement
            WHERE status = 'Manager Correction Required'
        """)
        manager_procurement_correction_required = (
            cursor.fetchone()["total"] or 0
        )

        cursor.execute("""
            SELECT COUNT(*) AS total
            FROM construction_purchase_procurement
            WHERE status = 'Pending Final Procurement'
        """)
        pending_final_procurement = (
            cursor.fetchone()["total"] or 0
        )

        cursor.execute("""
            SELECT COUNT(*) AS total
            FROM construction_purchase_procurement
            WHERE status = 'Final Procurement Correction Required'
        """)
        final_procurement_correction_required = (
            cursor.fetchone()["total"] or 0
        )

        cursor.execute("""
            SELECT COUNT(*) AS total
            FROM construction_purchase_procurement
            WHERE status = 'Completed'
        """)
        completed_procurement = (
            cursor.fetchone()["total"] or 0
        )

        # ==================================================
        # RECENT MATERIAL REQUESTS
        # ==================================================

        cursor.execute("""
            SELECT
                mr.id,
                mr.request_number,
                mr.project_name,
                mr.requested_by,
                mr.requested_by_name,
                mr.request_date,
                mr.description,

                mr.original_file_name,
                mr.original_file_url,
                mr.original_public_id,

                mr.signed_file_name,
                mr.signed_file_url,
                mr.signed_public_id,

                mr.purchase_signature_data,
                mr.purchase_signed_at,

                mr.status,

                mr.engineer_id,
                mr.engineer_name,
                mr.engineer_reviewed_at,
                mr.engineer_comment,

                mr.manager_id,
                mr.manager_name,
                mr.manager_approved_at,
                mr.manager_comment,

                mr.procurement_method,

                mr.created_at,
                mr.updated_at,

                p.id AS procurement_id,
                p.procurement_method AS procurement_record_method,
                p.workflow_stage AS procurement_workflow_stage,
                p.status AS procurement_status,
                p.initiated_by AS procurement_initiated_by,
                p.initiated_by_name AS procurement_initiated_by_name,
                p.initiated_at AS procurement_initiated_at,
                p.updated_at AS procurement_updated_at

            FROM construction_purchase_material_requests mr

            LEFT JOIN construction_purchase_procurement p
                ON p.id = (
                    SELECT MAX(p2.id)
                    FROM construction_purchase_procurement p2
                    WHERE p2.material_request_id = mr.id
                )

            ORDER BY
                mr.created_at DESC,
                mr.id DESC

            LIMIT 100
        """)

        recent_requests = cursor.fetchall() or []

        # ==================================================
        # NORMALIZE MATERIAL REQUEST PROCUREMENT DATA
        # ==================================================

        for material_request in recent_requests:

            material_request["procurement_id"] = (
                material_request.get("procurement_id")
            )

            material_request["procurement_status"] = (
                material_request.get("procurement_status") or ""
            ).strip()

            material_request["procurement_method"] = (
                material_request.get(
                    "procurement_record_method"
                )
                or material_request.get(
                    "procurement_method"
                )
                or ""
            ).strip()

            material_request["procurement_workflow_stage"] = (
                material_request.get(
                    "procurement_workflow_stage"
                )
                or ""
            ).strip()

            material_request["is_pending_final_procurement"] = (
                material_request["procurement_status"]
                == "Pending Final Procurement"
            )

            material_request[
                "is_final_procurement_correction_required"
            ] = (
                material_request["procurement_status"]
                == "Final Procurement Correction Required"
            )

        # ==================================================
        # RECENT PROCUREMENT RECORDS
        # ==================================================

        cursor.execute("""
            SELECT
                p.id,
                p.material_request_id,
                p.procurement_method,
                p.workflow_stage,

                p.initiated_by,
                p.initiated_by_name,
                p.initiated_at,

                p.status,
                p.notes,

                p.supplier_name,
                p.supplier_contact,

                p.procurement_amount,
                p.currency,

                p.payment_date,

                p.card_paid_by,
                p.card_paid_by_name,
                p.card_paid_at,
                p.card_payment_reference,

                p.quotation_number,
                p.quotation_date,
                p.lpo_number,

                p.pre_purchase_submitted_by,
                p.pre_purchase_submitted_by_name,
                p.pre_purchase_submitted_at,

                p.submitted_to_account_at,
                p.account_reviewed_at,
                p.account_reviewer_id,
                p.account_reviewer_name,
                p.account_comment,
                p.account_signature_data,
                p.account_signed_at,

                p.manager_reviewed_at,
                p.manager_reviewer_id,
                p.manager_reviewer_name,
                p.manager_comment,
                p.manager_signature_data,
                p.manager_signed_at,

                p.final_purchase_started_by,
                p.final_purchase_started_by_name,
                p.final_purchase_started_at,

                p.final_payment_method,

                p.final_card_paid_by,
                p.final_card_paid_by_name,
                p.final_card_paid_at,
                p.final_card_payment_reference,

                p.cheque_required,
                p.cheque_number,
                p.cheque_date,
                p.cheque_bank,

                p.account_cheque_issued_by,
                p.account_cheque_issued_by_name,
                p.account_cheque_issued_at,

                p.account_cheque_number,
                p.account_cheque_date,
                p.account_cheque_bank,

                p.final_documents_submitted_by,
                p.final_documents_submitted_by_name,
                p.final_documents_submitted_at,

                p.completed_by,
                p.completed_by_name,
                p.completed_at,

                p.created_at,
                p.updated_at,

                mr.request_number,
                mr.project_name,
                mr.requested_by_name

            FROM construction_purchase_procurement p

            INNER JOIN construction_purchase_material_requests mr
                ON mr.id = p.material_request_id

            ORDER BY
                CASE
                    WHEN p.status = 'Pending Final Procurement'
                        THEN 1

                    WHEN p.status = 'Final Procurement Correction Required'
                        THEN 2

                    WHEN p.status = 'Pending Manager Review'
                        THEN 3

                    WHEN p.status = 'Pending Account Review'
                        THEN 4

                    WHEN p.status = 'Procurement Correction Required'
                        THEN 5

                    WHEN p.status = 'Completed'
                        THEN 6

                    ELSE 7
                END,

                COALESCE(
                    p.updated_at,
                    p.created_at
                ) DESC,

                p.id DESC

            LIMIT 200
        """)

        recent_procurement = cursor.fetchall() or []

        # ==================================================
        # LOAD ALL PROCUREMENT DOCUMENTS
        # ==================================================

        procurement_ids = [
            procurement.get("id")
            for procurement in recent_procurement
            if procurement.get("id") is not None
        ]

        document_map = {}

        if procurement_ids:

            placeholders = ",".join(
                ["%s"] * len(procurement_ids)
            )

            cursor.execute(
                f"""
                    SELECT
                        id,
                        procurement_id,
                        document_type,
                        workflow_stage,
                        document_title,
                        original_file_name,
                        file_url,
                        public_id,
                        uploaded_by,
                        uploaded_by_name,
                        uploaded_at,
                        notes

                    FROM construction_purchase_procurement_documents

                    WHERE procurement_id IN ({placeholders})

                    ORDER BY
                        uploaded_at DESC,
                        id DESC
                """,
                tuple(procurement_ids)
            )

            procurement_documents = (
                cursor.fetchall() or []
            )

            for document in procurement_documents:

                procurement_id = document.get(
                    "procurement_id"
                )

                if procurement_id not in document_map:

                    document_map[procurement_id] = {
                        "all_documents": [],

                        "quotation_document": None,
                        "lpo_document": None,
                        "card_payment_document": None,

                        "manager_quotation_document": None,
                        "manager_lpo_document": None,
                        "manager_card_payment_document": None,

                        "final_receipt_document": None,
                        "final_invoice_document": None,
                        "final_payment_evidence_document": None
                    }

                document_map[procurement_id][
                    "all_documents"
                ].append(document)

                document_type = (
                    document.get("document_type") or ""
                ).strip().lower()

                document_type = (
                    document_type
                    .replace("_", " ")
                    .replace("-", " ")
                )

                document_type = " ".join(
                    document_type.split()
                )

                workflow_stage = (
                    document.get("workflow_stage") or ""
                ).strip().lower()

                workflow_stage = (
                    workflow_stage
                    .replace("_", " ")
                    .replace("-", " ")
                )

                workflow_stage = " ".join(
                    workflow_stage.split()
                )

                # ------------------------------------------
                # ACCOUNT STAMP
                # ------------------------------------------

                if workflow_stage == "account stamp":

                    if (
                        document_type == "quotation"
                        and document_map[procurement_id][
                            "quotation_document"
                        ] is None
                    ):
                        document_map[procurement_id][
                            "quotation_document"
                        ] = document

                    elif (
                        document_type == "lpo"
                        and document_map[procurement_id][
                            "lpo_document"
                        ] is None
                    ):
                        document_map[procurement_id][
                            "lpo_document"
                        ] = document

                    elif (
                        document_type == "card payment evidence"
                        and document_map[procurement_id][
                            "card_payment_document"
                        ] is None
                    ):
                        document_map[procurement_id][
                            "card_payment_document"
                        ] = document

                # ------------------------------------------
                # MANAGER SIGNED
                # ------------------------------------------

                elif workflow_stage == "manager signed":

                    if (
                        document_type == "quotation"
                        and document_map[procurement_id][
                            "manager_quotation_document"
                        ] is None
                    ):
                        document_map[procurement_id][
                            "manager_quotation_document"
                        ] = document

                    elif (
                        document_type == "lpo"
                        and document_map[procurement_id][
                            "manager_lpo_document"
                        ] is None
                    ):
                        document_map[procurement_id][
                            "manager_lpo_document"
                        ] = document

                    elif (
                        document_type == "card payment evidence"
                        and document_map[procurement_id][
                            "manager_card_payment_document"
                        ] is None
                    ):
                        document_map[procurement_id][
                            "manager_card_payment_document"
                        ] = document

                # ------------------------------------------
                # FINAL PROCUREMENT
                # ------------------------------------------

                elif workflow_stage == "final procurement":

                    if (
                        document_type == "receipt"
                        and document_map[procurement_id][
                            "final_receipt_document"
                        ] is None
                    ):
                        document_map[procurement_id][
                            "final_receipt_document"
                        ] = document

                    elif (
                        document_type == "invoice"
                        and document_map[procurement_id][
                            "final_invoice_document"
                        ] is None
                    ):
                        document_map[procurement_id][
                            "final_invoice_document"
                        ] = document

                    elif document_type in (
                        "payment evidence",
                        "final payment evidence"
                    ):

                        if document_map[procurement_id][
                            "final_payment_evidence_document"
                        ] is None:

                            document_map[procurement_id][
                                "final_payment_evidence_document"
                            ] = document

        # ==================================================
        # ATTACH DOCUMENTS + FLAGS
        # ==================================================

        for procurement in recent_procurement:

            procurement_id = procurement.get("id")

            document_data = document_map.get(
                procurement_id,
                {
                    "all_documents": [],
                    "quotation_document": None,
                    "lpo_document": None,
                    "card_payment_document": None,
                    "manager_quotation_document": None,
                    "manager_lpo_document": None,
                    "manager_card_payment_document": None,
                    "final_receipt_document": None,
                    "final_invoice_document": None,
                    "final_payment_evidence_document": None
                }
            )

            procurement["documents"] = list(
                document_data["all_documents"]
            )

            procurement["quotation_document"] = (
                document_data["quotation_document"]
            )

            procurement["lpo_document"] = (
                document_data["lpo_document"]
            )

            procurement["card_payment_document"] = (
                document_data["card_payment_document"]
            )

            procurement["manager_quotation_document"] = (
                document_data["manager_quotation_document"]
            )

            procurement["manager_lpo_document"] = (
                document_data["manager_lpo_document"]
            )

            procurement["manager_card_payment_document"] = (
                document_data["manager_card_payment_document"]
            )

            procurement["final_receipt_document"] = (
                document_data["final_receipt_document"]
            )

            procurement["final_invoice_document"] = (
                document_data["final_invoice_document"]
            )

            procurement["final_payment_evidence_document"] = (
                document_data[
                    "final_payment_evidence_document"
                ]
            )

            procurement["final_documents"] = list(
                document_data["all_documents"]
            )

            procurement_status = (
                procurement.get("status") or ""
            ).strip()

            procurement["is_pending_final_procurement"] = (
                procurement_status
                == "Pending Final Procurement"
            )

            procurement[
                "is_final_procurement_correction_required"
            ] = (
                procurement_status
                == "Final Procurement Correction Required"
            )

            procurement["is_pending_account_review"] = (
                procurement_status
                == "Pending Account Review"
            )

            procurement["is_pending_manager_review"] = (
                procurement_status
                == "Pending Manager Review"
            )

            procurement["is_completed"] = (
                procurement_status == "Completed"
            )

            # IMPORTANT:
            # Keep the Final Procurement action based on
            # the procurement ID + actual status.
            procurement["can_start_final_procurement"] = (
                procurement_status
                in (
                    "Pending Final Procurement",
                    "Final Procurement Correction Required"
                )
            )

        # ==================================================
        # PURCHASE USER
        # ==================================================

        purchase_user = {
            "id": session.get(
                "construction_admin_id"
            ),

            "fullname": session.get(
                "construction_admin_name",
                "Purchase Officer"
            ),

            "username": session.get(
                "construction_admin_username",
                ""
            ),

            "email": session.get(
                "construction_admin_email",
                ""
            ),

            "role": session.get(
                "construction_admin_role",
                "purchase"
            )
        }

        # ==================================================
        # RENDER
        # ==================================================

        return render_template(
            "construction_admin/construction_purchase_dashboard.html",

            purchase_user=purchase_user,

            total_material_requests=total_material_requests,
            pending_engineer_review=pending_engineer_review,
            engineer_correction_required=engineer_correction_required,
            pending_manager_approval=pending_manager_approval,
            manager_correction_required=manager_correction_required,
            approved_material_requests=approved_material_requests,
            declined_material_requests=declined_material_requests,
            pending_procurement=pending_procurement,
            completed_material_requests=completed_material_requests,

            total_procurement=total_procurement,
            card_procurement=card_procurement,
            quotation_procurement=quotation_procurement,

            pending_procurement_documents=(
                pending_procurement_documents
            ),

            procurement_correction_required=(
                procurement_correction_required
            ),

            pending_account_review=(
                pending_account_review
            ),

            account_correction_required=(
                account_correction_required
            ),

            procurement_pending_manager=(
                procurement_pending_manager
            ),

            manager_procurement_correction_required=(
                manager_procurement_correction_required
            ),

            pending_final_procurement=(
                pending_final_procurement
            ),

            final_procurement_correction_required=(
                final_procurement_correction_required
            ),

            completed_procurement=(
                completed_procurement
            ),

            card_pending_documents=(
                pending_procurement_documents
                if card_procurement
                else 0
            ),

            quotation_lpo_pending_documents=(
                pending_procurement_documents
                if quotation_procurement
                else 0
            ),

            approved_for_final_purchase=(
                pending_final_procurement
            ),

            final_purchase_in_progress=(
                pending_final_procurement
            ),

            final_documents_pending=(
                pending_final_procurement
            ),

            recent_requests=recent_requests,
            recent_procurement=recent_procurement
        )

    except Exception as e:

        print(
            "=================================================="
        )
        print(
            "PURCHASE DASHBOARD ERROR"
        )
        print(
            type(e).__name__
        )
        print(
            repr(e)
        )
        print(
            "=================================================="
        )

        import traceback
        traceback.print_exc()

        flash(
            "Unable to load the Purchase Dashboard.",
            "danger"
        )

        return redirect(
            url_for("admin_login")
        )

    finally:

        if cursor:
            try:
                cursor.close()
            except Exception:
                pass

        if conn:
            try:
                conn.close()
            except Exception:
                pass











# ==========================================================
# PURCHASE OFFICER - CREATE / RESUBMIT MATERIAL REQUEST
# ==========================================================

@app.route(
    "/construction/purchase/material-request",
    methods=["GET", "POST"]
)
def construction_purchase_material_request():

    # ======================================================
    # LOGIN PROTECTION
    # ======================================================

    if "construction_admin_id" not in session:

        flash(
            "Please sign in to access the Purchase Department.",
            "warning"
        )

        return redirect(
            url_for("admin_login")
        )

    # ======================================================
    # ROLE PROTECTION
    # ======================================================

    role = (
        session.get("construction_admin_role") or ""
    ).strip().lower()

    if role != "purchase":

        flash(
            "You are not authorized to access the Purchase Department.",
            "danger"
        )

        return redirect(
            construction_role_dashboard()
        )

    # ======================================================
    # GET REQUEST
    # ======================================================

    if request.method == "GET":

        purchase_user = {
            "fullname": session.get(
                "construction_admin_name",
                "Purchase Officer"
            ),
            "username": session.get(
                "construction_admin_username",
                ""
            ),
            "role": session.get(
                "construction_admin_role",
                "Purchase"
            )
        }

        return render_template(
            "construction_admin/"
            "construction_purchase_material_request.html",
            purchase_user=purchase_user
        )

    # ======================================================
    # POST REQUEST
    # ======================================================

    request_number = (
        request.form.get("request_number") or ""
    ).strip()

    project_name = (
        request.form.get("project_name") or ""
    ).strip()

    requested_by_name = (
        request.form.get("requested_by") or ""
    ).strip()

    request_date = (
        request.form.get("request_date") or ""
    ).strip()

    description = (
        request.form.get("description") or ""
    ).strip()

    signature_data = (
        request.form.get("signature_data") or ""
    ).strip()

    material_file = request.files.get(
        "material_file"
    )

    purchase_admin_id = session.get(
        "construction_admin_id"
    )

    purchase_admin_name = session.get(
        "construction_admin_name",
        "Purchase Officer"
    )

    # ======================================================
    # BASIC VALIDATION
    # ======================================================

    if not request_number:

        flash(
            "Material Request Number is required.",
            "danger"
        )

        return redirect(
            url_for(
                "construction_purchase_material_request"
            )
        )

    if not project_name:

        flash(
            "Project name is required.",
            "danger"
        )

        return redirect(
            url_for(
                "construction_purchase_material_request"
            )
        )

    if not requested_by_name:

        flash(
            "Please enter the actual requester's full name.",
            "danger"
        )

        return redirect(
            url_for(
                "construction_purchase_material_request"
            )
        )

    if not request_date:

        flash(
            "Request date is required.",
            "danger"
        )

        return redirect(
            url_for(
                "construction_purchase_material_request"
            )
        )

    # ======================================================
    # REQUEST DATE VALIDATION
    # ======================================================

    try:

        parsed_request_date = datetime.strptime(
            request_date,
            "%Y-%m-%d"
        ).date()

    except ValueError:

        flash(
            "Invalid request date.",
            "danger"
        )

        return redirect(
            url_for(
                "construction_purchase_material_request"
            )
        )

    # ======================================================
    # SIGNATURE VALIDATION
    # ======================================================

    if not signature_data:

        flash(
            "Please provide your digital signature.",
            "danger"
        )

        return redirect(
            url_for(
                "construction_purchase_material_request"
            )
        )

    if not signature_data.startswith(
        "data:image/"
    ):

        flash(
            "Invalid digital signature format.",
            "danger"
        )

        return redirect(
            url_for(
                "construction_purchase_material_request"
            )
        )

    # ======================================================
    # FILE VALIDATION
    #
    # MATERIAL REQUEST MUST BE PDF
    # ======================================================

    if (
        not material_file
        or not material_file.filename
    ):

        flash(
            "Please select the Material Request PDF.",
            "danger"
        )

        return redirect(
            url_for(
                "construction_purchase_material_request"
            )
        )

    original_filename = secure_filename(
        material_file.filename
    )

    if not original_filename:

        flash(
            "Invalid file name.",
            "danger"
        )

        return redirect(
            url_for(
                "construction_purchase_material_request"
            )
        )

    extension = (
        original_filename.rsplit(".", 1)[-1].lower()
        if "." in original_filename
        else ""
    )

    if extension != "pdf":

        flash(
            "Only PDF files are accepted for Material Requests.",
            "danger"
        )

        return redirect(
            url_for(
                "construction_purchase_material_request"
            )
        )

    # ======================================================
    # READ PDF
    # ======================================================

    try:

        material_file.seek(0)

        submitted_pdf_bytes = material_file.read()

        if not submitted_pdf_bytes:

            raise ValueError(
                "The uploaded PDF is empty."
            )

    except Exception as e:

        print(
            "MATERIAL REQUEST FILE READ ERROR:",
            repr(e)
        )

        flash(
            "Unable to read the uploaded PDF.",
            "danger"
        )

        return redirect(
            url_for(
                "construction_purchase_material_request"
            )
        )

    # ======================================================
    # DATABASE VARIABLES
    # ======================================================

    conn = None
    cursor = None

    material_request_id = None

    existing_request = None

    is_correction_resubmission = False

    # ======================================================
    # CLOUDINARY VARIABLES
    # ======================================================

    original_file_url = None
    original_public_id = None

    signed_file_url = None
    signed_public_id = None

    signed_filename = (
        f"{request_number}_signed.pdf"
    )

    # ======================================================
    # DATABASE PROCESS
    # ======================================================

    try:

        # ==================================================
        # DATABASE CONNECTION
        # ==================================================

        conn = get_db_connection()

        cursor = conn.cursor(
            dictionary=True
        )

        # ==================================================
        # CHECK WHETHER REQUEST NUMBER ALREADY EXISTS
        #
        # IMPORTANT:
        #
        # Existing request numbers are ONLY allowed when
        # the current status is:
        #
        # Engineer Correction Required
        #
        # In that situation we reuse the SAME request ID
        # and SAME request number.
        # ==================================================

        cursor.execute(
            """
            SELECT
                id,
                request_number,
                project_name,
                requested_by,
                requested_by_name,
                request_date,
                description,

                original_file_name,
                original_file_url,
                original_public_id,

                signed_file_name,
                signed_file_url,
                signed_public_id,

                purchase_signature_data,
                purchase_signed_at,

                status,

                engineer_id,
                engineer_name,
                engineer_reviewed_at,
                engineer_comment,

                manager_id,
                manager_name,
                manager_approved_at,
                manager_comment,

                procurement_method,

                created_at,
                updated_at

            FROM
                construction_purchase_material_requests

            WHERE
                request_number = %s

            LIMIT 1
            """,
            (
                request_number,
            )
        )

        existing_request = cursor.fetchone()

        # ==================================================
        # DETERMINE NEW REQUEST VS CORRECTION RESUBMISSION
        # ==================================================

        if existing_request:

            existing_status = (
                existing_request.get("status") or ""
            ).strip()

            # ------------------------------------------------
            # ONLY CORRECTION REQUESTS CAN REUSE THE NUMBER
            # ------------------------------------------------

            if existing_status == "Engineer Correction Required":

                is_correction_resubmission = True

                material_request_id = (
                    existing_request["id"]
                )

                print(
                    "=================================================="
                )

                print(
                    "CORRECTION RESUBMISSION DETECTED"
                )

                print(
                    "Existing Material Request ID:",
                    material_request_id
                )

                print(
                    "Request Number:",
                    request_number
                )

                print(
                    "Previous Status:",
                    existing_status
                )

                print(
                    "=================================================="
                )

            else:

                raise ValueError(
                    f"Material Request Number "
                    f"{request_number} already exists "
                    f"and cannot be reused because its current "
                    f"status is '{existing_status}'."
                )

        # ==================================================
        # QATAR LOCAL TIME
        # ==================================================

        signed_at = (
            get_qatar_now()
            .replace(tzinfo=None)
        )

        # ==================================================
        # CLOUDINARY FOLDER
        # ==================================================

        cloudinary_folder = (
            "prestigious_construction/"
            "purchase/material_requests"
        )

        # ==================================================
        # ORIGINAL PDF
        #
        # NEW REQUEST:
        #     Upload the original PDF.
        #
        # CORRECTION:
        #     DO NOT replace the original PDF.
        #
        # The original document must remain untouched
        # for audit purposes.
        # ==================================================

        if not is_correction_resubmission:

            try:

                original_file = BytesIO(
                    submitted_pdf_bytes
                )

                original_file.name = original_filename

                original_upload_result = (
                    cloudinary.uploader.upload(
                        original_file,
                        resource_type="raw",
                        folder=cloudinary_folder,
                        public_id=(
                            f"{request_number}_original"
                        ),
                        overwrite=True
                    )
                )

                original_file_url = (
                    original_upload_result.get(
                        "secure_url"
                    )
                )

                original_public_id = (
                    original_upload_result.get(
                        "public_id"
                    )
                )

                if not original_file_url:

                    raise RuntimeError(
                        "Cloudinary did not return "
                        "a secure URL for the original PDF."
                    )

            except Exception as e:

                print(
                    "MATERIAL REQUEST ORIGINAL PDF "
                    "CLOUDINARY ERROR:",
                    repr(e)
                )

                flash(
                    "Unable to upload the original "
                    "Material Request PDF.",
                    "danger"
                )

                return redirect(
                    url_for(
                        "construction_purchase_material_request"
                    )
                )

        else:

            # ==================================================
            # CORRECTION:
            #
            # Preserve the ORIGINAL document already stored.
            # ==================================================

            original_file_url = (
                existing_request.get(
                    "original_file_url"
                )
            )

            original_public_id = (
                existing_request.get(
                    "original_public_id"
                )
            )

        # ==================================================
        # CREATE NEW PURCHASE-SIGNED PDF
        #
        # For a correction, this creates a NEW VERSION of
        # the signed document using the corrected PDF.
        # ==================================================

        try:

            signed_pdf_bytes = create_signed_pdf(
                submitted_pdf_bytes,
                signature_data,
                signer_name=purchase_admin_name
            )

            if not signed_pdf_bytes:

                raise RuntimeError(
                    "Signed PDF generation returned no data."
                )

        except Exception as e:

            print(
                "MATERIAL REQUEST SIGNED PDF "
                "CREATION ERROR:",
                repr(e)
            )

            flash(
                "Unable to create the digitally "
                "signed Material Request PDF.",
                "danger"
            )

            return redirect(
                url_for(
                    "construction_purchase_material_request"
                )
            )

        # ==================================================
        # UPLOAD SIGNED PDF
        #
        # SAME CLOUDINARY PUBLIC ID
        #
        # This means the current signed document is replaced
        # by the corrected signed version.
        #
        # The ORIGINAL PDF remains untouched.
        # ==================================================

        try:

            signed_file = BytesIO(
                signed_pdf_bytes
            )

            signed_file.name = signed_filename

            signed_upload_result = (
                cloudinary.uploader.upload(
                    signed_file,
                    resource_type="raw",
                    folder=cloudinary_folder,
                    public_id=(
                        f"{request_number}_signed"
                    ),
                    overwrite=True
                )
            )

            signed_file_url = (
                signed_upload_result.get(
                    "secure_url"
                )
            )

            signed_public_id = (
                signed_upload_result.get(
                    "public_id"
                )
            )

            if not signed_file_url:

                raise RuntimeError(
                    "Cloudinary did not return "
                    "a secure URL for the signed PDF."
                )

        except Exception as e:

            print(
                "MATERIAL REQUEST SIGNED PDF "
                "CLOUDINARY ERROR:",
                repr(e)
            )

            flash(
                "Unable to upload the digitally "
                "signed Material Request PDF.",
                "danger"
            )

            return redirect(
                url_for(
                    "construction_purchase_material_request"
                )
            )

        # ==================================================
        # NEW REQUEST
        # ==================================================

        if not is_correction_resubmission:

            # ==================================================
            # INSERT MATERIAL REQUEST
            # ==================================================

            cursor.execute(
                """
                INSERT INTO
                construction_purchase_material_requests
                (
                    request_number,
                    project_name,
                    requested_by,
                    requested_by_name,
                    request_date,
                    description,

                    original_file_name,
                    original_file_url,
                    original_public_id,

                    signed_file_name,
                    signed_file_url,
                    signed_public_id,

                    purchase_signature_data,
                    purchase_signed_at,

                    status,

                    created_at,
                    updated_at
                )
                VALUES
                (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,

                    %s,
                    %s,
                    %s,

                    %s,
                    %s,
                    %s,

                    %s,
                    %s,

                    %s,

                    %s,
                    %s
                )
                """,
                (
                    request_number,
                    project_name,
                    purchase_admin_id,
                    requested_by_name,
                    parsed_request_date,
                    description or None,

                    # ORIGINAL PDF
                    original_filename,
                    original_file_url,
                    original_public_id,

                    # SIGNED PDF
                    signed_filename,
                    signed_file_url,
                    signed_public_id,

                    # PURCHASE SIGNATURE
                    signature_data,
                    signed_at,

                    # STATUS
                    "Pending Engineer Review",

                    # TIMESTAMPS
                    signed_at,
                    signed_at
                )
            )

            material_request_id = (
                cursor.lastrowid
            )

            # ==================================================
            # NEW REQUEST AUDIT LOG
            # ==================================================

            cursor.execute(
                """
                INSERT INTO
                construction_purchase_audit_logs
                (
                    material_request_id,
                    procurement_id,
                    user_id,
                    user_name,
                    user_role,
                    action,
                    description,
                    old_status,
                    new_status,
                    ip_address,
                    created_at
                )
                VALUES
                (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s
                )
                """,
                (
                    material_request_id,
                    None,
                    purchase_admin_id,
                    purchase_admin_name,
                    "Purchase",
                    "Material Request Submitted",
                    (
                        "Purchase Officer submitted Material Request "
                        f"{request_number}. "
                        f"Actual requester: {requested_by_name}. "
                        "The original uploaded PDF was preserved "
                        "separately from the digitally signed PDF."
                    ),
                    None,
                    "Pending Engineer Review",
                    request.remote_addr,
                    signed_at
                )
            )

        # ======================================================
        # CORRECTION RESUBMISSION
        # ======================================================

        else:

            previous_status = (
                existing_request.get("status")
                or "Engineer Correction Required"
            )

            previous_engineer_comment = (
                existing_request.get(
                    "engineer_comment"
                )
                or ""
            )

            # ==================================================
            # UPDATE SAME MATERIAL REQUEST
            #
            # IMPORTANT:
            #
            # ID DOES NOT CHANGE.
            # REQUEST NUMBER DOES NOT CHANGE.
            # ORIGINAL PDF DOES NOT CHANGE.
            # ==================================================

            cursor.execute(
                """
                UPDATE
                    construction_purchase_material_requests

                SET
                    project_name = %s,
                    requested_by = %s,
                    requested_by_name = %s,
                    request_date = %s,
                    description = %s,

                    signed_file_name = %s,
                    signed_file_url = %s,
                    signed_public_id = %s,

                    purchase_signature_data = %s,
                    purchase_signed_at = %s,

                    status = %s,

                    updated_at = %s

                WHERE
                    id = %s
                """,
                (
                    project_name,
                    purchase_admin_id,
                    requested_by_name,
                    parsed_request_date,
                    description or None,

                    # LATEST SIGNED PDF
                    signed_filename,
                    signed_file_url,
                    signed_public_id,

                    # NEW PURCHASE SIGNATURE
                    signature_data,
                    signed_at,

                    # SEND BACK TO ENGINEER
                    "Pending Engineer Review",

                    # AUDIT TIMESTAMP
                    signed_at,

                    material_request_id
                )
            )

            # ==================================================
            # CORRECTION RESUBMISSION AUDIT LOG
            # ==================================================

            cursor.execute(
                """
                INSERT INTO
                construction_purchase_audit_logs
                (
                    material_request_id,
                    procurement_id,
                    user_id,
                    user_name,
                    user_role,
                    action,
                    description,
                    old_status,
                    new_status,
                    ip_address,
                    created_at
                )
                VALUES
                (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s
                )
                """,
                (
                    material_request_id,
                    None,
                    purchase_admin_id,
                    purchase_admin_name,
                    "Purchase",
                    "Material Request Resubmitted After Correction",
                    (
                        f"Purchase Officer resubmitted Material Request "
                        f"{request_number} after Engineer requested "
                        "correction. The SAME Material Request ID and "
                        "SAME Material Request Number were retained. "
                        "The original PDF was preserved and the latest "
                        "digitally signed PDF was replaced with the "
                        "corrected version. "

                        + (
                            f"Previous Engineer Comment: "
                            f"{previous_engineer_comment}"
                            if previous_engineer_comment
                            else
                            "No previous Engineer comment was recorded."
                        )
                    ),
                    previous_status,
                    "Pending Engineer Review",
                    request.remote_addr,
                    signed_at
                )
            )

        # ======================================================
        # COMMIT DATABASE
        #
        # The request is committed BEFORE sending email.
        # ======================================================

        conn.commit()

        # ======================================================
        # LOG SUCCESS
        # ======================================================

        print(
            "=================================================="
        )

        if is_correction_resubmission:

            print(
                "MATERIAL REQUEST CORRECTION RESUBMITTED"
            )

        else:

            print(
                "MATERIAL REQUEST CREATED SUCCESSFULLY"
            )

        print(
            "Material Request ID:",
            material_request_id
        )

        print(
            "Request Number:",
            request_number
        )

        print(
            "Project:",
            project_name
        )

        print(
            "Actual Requester:",
            requested_by_name
        )

        print(
            "Submitted By:",
            purchase_admin_name
        )

        print(
            "Status:",
            "Pending Engineer Review"
        )

        print(
            "Original PDF:",
            original_file_url
        )

        print(
            "Latest Signed PDF:",
            signed_file_url
        )

        print(
            "Signed At:",
            signed_at
        )

        print(
            "Correction Resubmission:",
            is_correction_resubmission
        )

        print(
            "=================================================="
        )

        # ======================================================
        # EMAIL NOTIFICATIONS
        #
        # SEND TO:
        # Purchase Officer
        # ALL ENGINEERS
        #
        # DO NOT SEND TO MANAGER YET.
        # ======================================================

        try:

            notification_cursor = conn.cursor(
                dictionary=True
            )

            # ==================================================
            # GET PURCHASE OFFICER
            # ==================================================

            notification_cursor.execute(
                """
                SELECT
                    fullname,
                    email
                FROM
                    construction_admins
                WHERE
                    id = %s
                LIMIT 1
                """,
                (
                    purchase_admin_id,
                )
            )

            purchaser = (
                notification_cursor.fetchone()
            )

            # ==================================================
            # GET ALL ENGINEERS
            # ==================================================

            notification_cursor.execute(
                """
                SELECT
                    id,
                    fullname,
                    email
                FROM
                    construction_admins
                WHERE
                    LOWER(role) = 'engineer'
                    AND email IS NOT NULL
                    AND TRIM(email) != ''
                ORDER BY
                    id ASC
                """
            )

            engineers = (
                notification_cursor.fetchall()
            )

            notification_cursor.close()

            # ==================================================
            # EMAIL SUBJECT
            # ==================================================

            if is_correction_resubmission:

                email_subject = (
                    "Corrected Material Request Requires "
                    "Engineer Review - "
                    f"{request_number}"
                )

            else:

                email_subject = (
                    "Material Request Requires Engineer Review - "
                    f"{request_number}"
                )

            # ==================================================
            # EMAIL CONTENT
            # ==================================================

            if is_correction_resubmission:

                email_intro = """
                <p>
                    A Material Request that was previously returned
                    by the Engineer for correction has now been
                    corrected and resubmitted by the Purchase
                    Department.
                </p>

                <p>
                    <b>
                        IMPORTANT:
                    </b>
                    The original Material Request Number has been
                    retained. No new Material Request Number was
                    generated.
                </p>
                """

            else:

                email_intro = """
                <p>
                    A new Material Request has been submitted
                    by the Purchase Department.
                </p>
                """

            email_html = f"""
            <h2>
                Material Request Requires Engineer Review
            </h2>

            {email_intro}

            <hr>

            <h3>Material Request Details</h3>

            <p>
                <b>Request Number:</b>
                {request_number}
            </p>

            <p>
                <b>Project:</b>
                {project_name}
            </p>

            <p>
                <b>Actual Requested By:</b>
                {requested_by_name}
            </p>

            <p>
                <b>Request Date:</b>
                {parsed_request_date.strftime("%Y-%m-%d")}
            </p>

            <p>
                <b>Purchase Submitted Date &amp; Time:</b>
                {signed_at.strftime("%Y-%m-%d %H:%M:%S")}
            </p>

            <p>
                <b>Description:</b>
                {description or "No description provided"}
            </p>

            <p>
                <b>Submitted By:</b>
                {purchase_admin_name}
            </p>

            <p>
                <b>Status:</b>
                Pending Engineer Review
            </p>

            <p>
                <b>Purchase Signature:</b>
                Digitally signed and embedded into
                the latest signed PDF.
            </p>

            <hr>

            <h3>Documents</h3>

            <p>
                <b>Original Material Request:</b>
            </p>

            <p>
                <a
                    href="{original_file_url}"
                    target="_blank"
                >
                    View Original Material Request PDF
                </a>
            </p>

            <p>
                <b>Latest Digitally Signed Material Request:</b>
            </p>

            <p>
                <a
                    href="{signed_file_url}"
                    target="_blank"
                >
                    View Latest Signed Material Request PDF
                </a>
            </p>

            <hr>

            <p>
                Please log into the Construction Management
                System to review this Material Request.
            </p>

            <p>
                Regards,<br>
                <b>
                    Prestigious Trading &amp; Construction W.L.L.
                </b>
            </p>
            """

            # ==================================================
            # SEND EMAIL TO PURCHASE OFFICER
            # ==================================================

            if purchaser:

                purchaser_email = (
                    purchaser.get("email") or ""
                ).strip()

                purchaser_name = (
                    purchaser.get("fullname")
                    or purchase_admin_name
                )

                if purchaser_email:

                    try:

                        send_email(
                            purchaser_email,
                            (
                                (
                                    "Corrected Material Request "
                                    "Resubmitted - "
                                )
                                if is_correction_resubmission
                                else
                                (
                                    "Material Request Submitted - "
                                )
                            )
                            + request_number,
                            f"""
                            <h2>
                                Material Request
                                {
                                    "Resubmitted After Correction"
                                    if is_correction_resubmission
                                    else
                                    "Submitted Successfully"
                                }
                            </h2>

                            <p>
                                Hello
                                <b>{purchaser_name}</b>,
                            </p>

                            <p>
                                Your Material Request
                                <b>{request_number}</b>
                                has been
                                {
                                    "corrected and resubmitted"
                                    if is_correction_resubmission
                                    else
                                    "successfully submitted"
                                }
                                and is now awaiting Engineer Review.
                            </p>

                            <hr>

                            {email_html}
                            """
                        )

                    except Exception as purchaser_email_error:

                        print(
                            "PURCHASE OFFICER EMAIL ERROR:",
                            repr(purchaser_email_error)
                        )

            # ==================================================
            # SEND EMAIL TO ALL ENGINEERS
            # ==================================================

            for engineer in engineers:

                engineer_email = (
                    engineer.get("email") or ""
                ).strip()

                engineer_name = (
                    engineer.get("fullname")
                    or "Engineer"
                )

                if not engineer_email:
                    continue

                if is_correction_resubmission:

                    engineer_heading = (
                        "Corrected Material Request "
                        "Requires Your Review"
                    )

                    engineer_intro = f"""
                    <p>
                        Hello
                        <b>{engineer_name}</b>,
                    </p>

                    <p>
                        Material Request
                        <b>{request_number}</b>
                        was previously returned for correction
                        and has now been corrected and resubmitted
                        by the Purchase Department.
                    </p>

                    <p>
                        <b>
                            The same Material Request Number has
                            been retained for audit continuity.
                        </b>
                    </p>
                    """

                else:

                    engineer_heading = (
                        "New Material Request Requires Your Review"
                    )

                    engineer_intro = f"""
                    <p>
                        Hello
                        <b>{engineer_name}</b>,
                    </p>

                    <p>
                        A new Material Request has been submitted
                        and is waiting for Engineer Review.
                    </p>
                    """

                engineer_message = f"""
                <h2>
                    {engineer_heading}
                </h2>

                {engineer_intro}

                <hr>

                {email_html}

                <p>
                    This notification was generated
                    automatically by the Construction
                    Management System.
                </p>
                """

                try:

                    send_email(
                        engineer_email,
                        email_subject,
                        engineer_message
                    )

                except Exception as engineer_email_error:

                    print(
                        "ENGINEER EMAIL ERROR:",
                        engineer_email,
                        repr(engineer_email_error)
                    )

        except Exception as email_error:

            print(
                "MATERIAL REQUEST EMAIL PROCESS ERROR:",
                repr(email_error)
            )

            # ==================================================
            # IMPORTANT
            #
            # DATABASE WAS ALREADY COMMITTED.
            # EMAIL FAILURE MUST NOT ROLLBACK THE REQUEST.
            # ==================================================

        # ======================================================
        # SUCCESS MESSAGE
        # ======================================================

        if is_correction_resubmission:

            flash(
                f"Material Request {request_number} "
                "was corrected and resubmitted successfully. "
                "The original request number has been retained "
                "and the request is now awaiting Engineer Review.",
                "success"
            )

        else:

            flash(
                f"Material Request {request_number} "
                "was submitted successfully and sent to "
                "Engineer Review.",
                "success"
            )

        return redirect(
            url_for(
                "construction_purchase_dashboard"
            )
        )

    # ======================================================
    # VALIDATION ERROR
    # ======================================================

    except ValueError as e:

        if conn:

            try:
                conn.rollback()
            except Exception:
                pass

        print(
            "MATERIAL REQUEST VALIDATION ERROR:",
            repr(e)
        )

        flash(
            str(e),
            "danger"
        )

        return redirect(
            url_for(
                "construction_purchase_material_request"
            )
        )

    # ======================================================
    # DATABASE / SYSTEM ERROR
    # ======================================================

    except Exception as e:

        if conn:

            try:
                conn.rollback()
            except Exception:
                pass

        print(
            "=================================================="
        )

        print(
            "MATERIAL REQUEST DATABASE ERROR"
        )

        print(
            "ERROR TYPE:",
            type(e).__name__
        )

        print(
            "ERROR:",
            repr(e)
        )

        print(
            "=================================================="
        )

        flash(
            "Unable to save the Material Request. "
            "Please check the application log.",
            "danger"
        )

        return redirect(
            url_for(
                "construction_purchase_material_request"
            )
        )

    finally:

        if cursor:

            try:
                cursor.close()
            except Exception:
                pass

        if conn:

            try:
                conn.close()
            except Exception:
                pass

    # ======================================================
    # SAFETY FALLBACK
    # ======================================================

    return redirect(
        url_for(
            "construction_purchase_material_request"
        )
    )







# ==========================================================
# PURCHASE - CORRECT AND RESUBMIT MATERIAL REQUEST
# ==========================================================

@app.route(
    "/construction/purchase/material-request/<int:request_id>/correction",
    methods=["GET", "POST"]
)
def construction_purchase_material_request_correction(request_id):

    # ======================================================
    # LOGIN PROTECTION
    # ======================================================

    if "construction_admin_id" not in session:

        flash(
            "Please sign in to access Material Request Correction.",
            "warning"
        )

        return redirect(
            url_for("admin_login")
        )

    # ======================================================
    # ROLE PROTECTION
    # ======================================================

    role = (
        session.get("construction_admin_role") or ""
    ).strip().lower()

    if role != "purchase":

        flash(
            "You are not authorized to correct Material Requests.",
            "danger"
        )

        return redirect(
            construction_role_dashboard()
        )

    # ======================================================
    # PURCHASE USER INFORMATION
    # ======================================================

    purchase_id = session.get(
        "construction_admin_id"
    )

    purchase_name = session.get(
        "construction_admin_name",
        "Purchase Officer"
    )

    conn = None
    cursor = None

    try:

        # ==================================================
        # DATABASE CONNECTION
        # ==================================================

        conn = get_db_connection()

        cursor = conn.cursor(
            dictionary=True
        )

        # ==================================================
        # GET MATERIAL REQUEST
        # ==================================================

        cursor.execute(
            """
            SELECT

                id,
                request_number,

                project_name,

                requested_by,
                requested_by_name,

                request_date,

                description,

                original_file_name,
                original_file_url,
                original_public_id,

                signed_file_name,
                signed_file_url,
                signed_public_id,

                purchase_signature_data,
                purchase_signed_at,

                status,

                engineer_id,
                engineer_name,
                engineer_reviewed_at,
                engineer_comment,

                manager_id,
                manager_name,
                manager_approved_at,
                manager_comment,

                procurement_method,

                created_at,
                updated_at

            FROM
                construction_purchase_material_requests

            WHERE
                id = %s

            LIMIT 1
            """,
            (
                request_id,
            )
        )

        material_request = cursor.fetchone()

        # ==================================================
        # REQUEST NOT FOUND
        # ==================================================

        if not material_request:

            flash(
                "Material Request was not found.",
                "danger"
            )

            return redirect(
                url_for(
                    "construction_purchase_dashboard"
                )
            )

        # ==================================================
        # STATUS PROTECTION
        #
        # Purchase can only correct a request that has
        # explicitly been returned by Engineering.
        # ==================================================

        if (
            material_request["status"]
            != "Engineer Correction Required"
        ):

            flash(
                "This Material Request is not currently "
                "awaiting Purchase correction.",
                "warning"
            )

            return redirect(
                url_for(
                    "construction_purchase_dashboard"
                )
            )

        # ==================================================
        # POST REQUEST
        # ==================================================

        if request.method == "POST":

            # ==================================================
            # FORM DATA
            # ==================================================

            project_name = (
                request.form.get("project_name") or ""
            ).strip()

            requested_by = (
                request.form.get("requested_by") or ""
            ).strip()

            request_date = (
                request.form.get("request_date") or ""
            ).strip()

            description = (
                request.form.get("description") or ""
            ).strip()

            signature_data = (
                request.form.get("signature_data") or ""
            ).strip()

            corrected_file = request.files.get(
                "material_file"
            )

            # ==================================================
            # VALIDATION
            # ==================================================

            if not project_name:

                flash(
                    "Please enter the project name.",
                    "danger"
                )

                return redirect(
                    url_for(
                        "construction_purchase_material_request_correction",
                        request_id=request_id
                    )
                )

            if not requested_by:

                flash(
                    "Please select the requester.",
                    "danger"
                )

                return redirect(
                    url_for(
                        "construction_purchase_material_request_correction",
                        request_id=request_id
                    )
                )

            if not request_date:

                flash(
                    "Please select the request date.",
                    "danger"
                )

                return redirect(
                    url_for(
                        "construction_purchase_material_request_correction",
                        request_id=request_id
                    )
                )

            if not signature_data:

                flash(
                    "Please provide your Purchase digital signature.",
                    "danger"
                )

                return redirect(
                    url_for(
                        "construction_purchase_material_request_correction",
                        request_id=request_id
                    )
                )

            if (
                not corrected_file
                or not corrected_file.filename
            ):

                flash(
                    "Please upload the corrected Material Request document.",
                    "danger"
                )

                return redirect(
                    url_for(
                        "construction_purchase_material_request_correction",
                        request_id=request_id
                    )
                )

            # ==================================================
            # READ CORRECTED DOCUMENT
            # ==================================================

            try:

                corrected_pdf_bytes = (
                    corrected_file.read()
                )

                if not corrected_pdf_bytes:

                    raise ValueError(
                        "The uploaded document is empty."
                    )

                # ==============================================
                # THIS WORKFLOW EXPECTS A PDF.
                # ==============================================

                if not corrected_pdf_bytes.startswith(
                    b"%PDF"
                ):

                    raise ValueError(
                        "The corrected Material Request "
                        "must be a valid PDF file."
                    )

            except Exception as file_error:

                flash(
                    f"Unable to read corrected document: "
                    f"{str(file_error)}",
                    "danger"
                )

                return redirect(
                    url_for(
                        "construction_purchase_material_request_correction",
                        request_id=request_id
                    )
                )

            # ==================================================
            # REQUESTER NAME
            #
            # requested_by is expected to be the construction
            # admin ID selected by Purchase.
            # ==================================================

            try:

                requester_id = int(
                    requested_by
                )

            except (TypeError, ValueError):

                flash(
                    "Invalid requester selected.",
                    "danger"
                )

                return redirect(
                    url_for(
                        "construction_purchase_material_request_correction",
                        request_id=request_id
                    )
                )

            # ==================================================
            # LOCK REQUEST
            #
            # Prevent simultaneous correction/resubmission.
            # ==================================================

            cursor.execute(
                """
                SELECT

                    id,
                    request_number,

                    project_name,

                    requested_by,
                    requested_by_name,

                    request_date,

                    description,

                    original_file_name,
                    original_file_url,
                    original_public_id,

                    signed_file_name,
                    signed_file_url,
                    signed_public_id,

                    status,

                    engineer_id,
                    engineer_name,
                    engineer_reviewed_at,
                    engineer_comment

                FROM
                    construction_purchase_material_requests

                WHERE
                    id = %s

                FOR UPDATE
                """,
                (
                    request_id,
                )
            )

            locked_request = cursor.fetchone()

            # ==================================================
            # REQUEST DISAPPEARED
            # ==================================================

            if not locked_request:

                conn.rollback()

                flash(
                    "Material Request no longer exists.",
                    "danger"
                )

                return redirect(
                    url_for(
                        "construction_purchase_dashboard"
                    )
                )

            # ==================================================
            # STATUS CHECK AGAIN
            # ==================================================

            if (
                locked_request["status"]
                != "Engineer Correction Required"
            ):

                conn.rollback()

                flash(
                    "This Material Request has already been "
                    "processed and can no longer be corrected.",
                    "warning"
                )

                return redirect(
                    url_for(
                        "construction_purchase_dashboard"
                    )
                )

            # ==================================================
            # REQUESTER LOOKUP
            # ==================================================

            cursor.execute(
                """
                SELECT
                    id,
                    fullname,
                    email

                FROM
                    construction_admins

                WHERE
                    id = %s

                LIMIT 1
                """,
                (
                    requester_id,
                )
            )

            requester = cursor.fetchone()

            if not requester:

                conn.rollback()

                flash(
                    "The selected requester could not be found.",
                    "danger"
                )

                return redirect(
                    url_for(
                        "construction_purchase_material_request_correction",
                        request_id=request_id
                    )
                )

            requester_name = (
                requester.get("fullname")
                or "Requested User"
            )

            # ==================================================
            # QATAR LOCAL TIME
            # ==================================================

            resubmitted_at = get_qatar_now()

            # ==================================================
            # CREATE NEW PURCHASE STAMP
            #
            # IMPORTANT:
            #
            # We create the corrected Purchase-stamped PDF
            # from the newly uploaded corrected PDF.
            #
            # The previous Engineer-stamped document remains
            # untouched in Cloudinary.
            # ==================================================

            try:

                purchase_signed_pdf = create_signed_pdf(
                    pdf_bytes=corrected_pdf_bytes,

                    signature_data=signature_data,

                    signer_name=purchase_name,

                    department_title="PURCHASE DEPARTMENT",

                    position="left"
                )

                if not purchase_signed_pdf:

                    raise ValueError(
                        "Purchase signed PDF is empty."
                    )

                # ==============================================
                # BASIC PDF VALIDATION
                # ==============================================

                if not purchase_signed_pdf.startswith(
                    b"%PDF"
                ):

                    raise ValueError(
                        "Purchase stamping did not produce "
                        "a valid PDF."
                    )

                print("=" * 80)
                print(
                    "PURCHASE CORRECTION PDF CREATED"
                )

                print(
                    "REQUEST NUMBER:",
                    locked_request["request_number"]
                )

                print(
                    "PDF SIZE:",
                    len(purchase_signed_pdf),
                    "bytes"
                )

                print("=" * 80)

            except Exception as stamp_error:

                conn.rollback()

                import traceback

                print("=" * 80)
                print(
                    "PURCHASE CORRECTION PDF STAMP ERROR"
                )

                print(
                    "ERROR TYPE:",
                    type(stamp_error).__name__
                )

                print(
                    "ERROR:",
                    str(stamp_error)
                )

                traceback.print_exc()

                print("=" * 80)

                flash(
                    "The corrected Material Request could not "
                    "be digitally stamped.",
                    "danger"
                )

                return redirect(
                    url_for(
                        "construction_purchase_material_request_correction",
                        request_id=request_id
                    )
                )

            # ==================================================
            # UPLOAD NEW PURCHASE-STAMPED PDF
            #
            # IMPORTANT:
            #
            # A UNIQUE PUBLIC ID is used.
            #
            # This prevents the previous Engineer document
            # from being overwritten.
            # ==================================================

            try:

                request_number = (
                    locked_request["request_number"]
                )

                new_signed_file_name = (
                    f"{request_number}"
                    f"_purchase_correction_"
                    f"{resubmitted_at.strftime('%Y%m%d%H%M%S')}"
                    f".pdf"
                )

                purchase_signed_file = BytesIO(
                    purchase_signed_pdf
                )

                purchase_signed_file.name = (
                    new_signed_file_name
                )

                cloudinary_folder = (
                    "prestigious_construction/"
                    "purchase/material_requests"
                )

                new_public_id = (
                    f"{request_number}"
                    f"_purchase_correction_"
                    f"{resubmitted_at.strftime('%Y%m%d%H%M%S')}"
                )

                upload_result = (
                    cloudinary.uploader.upload(

                        purchase_signed_file,

                        resource_type="image",

                        format="pdf",

                        folder=cloudinary_folder,

                        public_id=new_public_id,

                        overwrite=False
                    )
                )

                new_signed_file_url = (
                    upload_result.get(
                        "secure_url"
                    )
                )

                new_signed_public_id = (
                    upload_result.get(
                        "public_id"
                    )
                )

                if not new_signed_file_url:

                    raise ValueError(
                        "Cloudinary did not return a secure "
                        "URL for the corrected PDF."
                    )

                if not new_signed_public_id:

                    raise ValueError(
                        "Cloudinary did not return a public "
                        "ID for the corrected PDF."
                    )

                print("=" * 80)
                print(
                    "PURCHASE CORRECTION CLOUDINARY UPLOAD SUCCESS"
                )

                print(
                    "FILE NAME:",
                    new_signed_file_name
                )

                print(
                    "PUBLIC ID:",
                    new_signed_public_id
                )

                print(
                    "SECURE URL:",
                    new_signed_file_url
                )

                print("=" * 80)

            except Exception as upload_error:

                conn.rollback()

                import traceback

                print("=" * 80)
                print(
                    "PURCHASE CORRECTION CLOUDINARY UPLOAD ERROR"
                )

                print(
                    "ERROR TYPE:",
                    type(upload_error).__name__
                )

                print(
                    "ERROR:",
                    str(upload_error)
                )

                traceback.print_exc()

                print("=" * 80)

                flash(
                    "The corrected Material Request PDF could "
                    "not be uploaded.",
                    "danger"
                )

                return redirect(
                    url_for(
                        "construction_purchase_material_request_correction",
                        request_id=request_id
                    )
                )

            # ==================================================
            # OLD DOCUMENT INFORMATION
            #
            # Store this in the audit description so the audit
            # trail records the document transition.
            # ==================================================

            old_document_url = (
                locked_request["signed_file_url"]
                or "Not available"
            )

            old_document_public_id = (
                locked_request["signed_public_id"]
                or "Not available"
            )

            engineer_correction_comment = (
                locked_request["engineer_comment"]
                or "No correction comment recorded."
            )

            audit_description = f"""
Material Request resubmitted by Purchase after Engineer correction.

Engineer Correction Comment:
{engineer_correction_comment}

Previous Stamped Document:
{old_document_url}

Previous Cloudinary Public ID:
{old_document_public_id}

New Purchase-Corrected Stamped Document:
{new_signed_file_url}

New Cloudinary Public ID:
{new_signed_public_id}

Purchase Officer:
{purchase_name}

Resubmission Date & Time:
{resubmitted_at.strftime("%Y-%m-%d %H:%M:%S AST")}
""".strip()

            # ==================================================
            # UPDATE MATERIAL REQUEST
            #
            # IMPORTANT:
            #
            # id                  = SAME
            # request_number      = SAME
            # original_file_url   = UNCHANGED
            #
            # Only the current/latest signed document is changed.
            # ==================================================

            cursor.execute(
                """
                UPDATE
                    construction_purchase_material_requests

                SET

                    project_name = %s,

                    requested_by = %s,

                    requested_by_name = %s,

                    request_date = %s,

                    description = %s,

                    signed_file_name = %s,

                    signed_file_url = %s,

                    signed_public_id = %s,

                    purchase_signature_data = %s,

                    purchase_signed_at = %s,

                    status = 'Pending Engineer Review',

                    updated_at = %s

                WHERE
                    id = %s
                """,
                (
                    project_name,

                    requester_id,

                    requester_name,

                    request_date,

                    description,

                    new_signed_file_name,

                    new_signed_file_url,

                    new_signed_public_id,

                    signature_data,

                    resubmitted_at,

                    resubmitted_at,

                    request_id
                )
            )

            # ==================================================
            # VERIFY UPDATE
            # ==================================================

            if cursor.rowcount != 1:

                conn.rollback()

                flash(
                    "The corrected Material Request could not "
                    "be updated.",
                    "danger"
                )

                return redirect(
                    url_for(
                        "construction_purchase_dashboard"
                    )
                )

            # ==================================================
            # AUDIT LOG
            # ==================================================

            cursor.execute(
                """
                INSERT INTO
                construction_purchase_audit_logs
                (
                    material_request_id,

                    user_id,
                    user_name,
                    user_role,

                    action,
                    description,

                    old_status,
                    new_status,

                    ip_address,

                    created_at
                )

                VALUES
                (
                    %s,

                    %s,
                    %s,
                    %s,

                    %s,
                    %s,

                    %s,
                    %s,

                    %s,

                    %s
                )
                """,
                (
                    request_id,

                    purchase_id,
                    purchase_name,
                    "purchase",

                    "Material Request Resubmitted After Engineer Correction",

                    audit_description,

                    "Engineer Correction Required",

                    "Pending Engineer Review",

                    request.remote_addr,

                    resubmitted_at
                )
            )

            # ==================================================
            # COMMIT DATABASE
            #
            # IMPORTANT:
            #
            # Database commit happens BEFORE email.
            #
            # Email failure must never undo the resubmission.
            # ==================================================

            conn.commit()

            # ==================================================
            # EMAIL NOTIFICATIONS
            # ==================================================

            try:

                notification_cursor = conn.cursor(
                    dictionary=True
                )

                # ==================================================
                # GET ALL ENGINEERS
                # ==================================================

                notification_cursor.execute(
                    """
                    SELECT
                        id,
                        fullname,
                        email

                    FROM
                        construction_admins

                    WHERE
                        LOWER(role) = 'engineer'

                        AND email IS NOT NULL

                        AND TRIM(email) != ''

                    ORDER BY
                        id ASC
                    """
                )

                engineers = (
                    notification_cursor.fetchall()
                    or []
                )

                notification_cursor.close()

                # ==================================================
                # COMMON EMAIL DATA
                # ==================================================

                request_number = (
                    locked_request["request_number"]
                )

                previous_engineer_comment = (
                    locked_request["engineer_comment"]
                    or "No correction comment recorded."
                )

                resubmission_date = (
                    resubmitted_at.strftime(
                        "%Y-%m-%d %H:%M AST"
                    )
                )

                # ==================================================
                # DOCUMENT BUTTON
                # ==================================================

                document_button = f"""
                <p style="margin-top:20px;">
                    <a
                        href="{new_signed_file_url}"
                        target="_blank"
                        rel="noopener noreferrer"
                        style="
                            display:inline-block;
                            padding:11px 20px;
                            background:#006b3c;
                            color:#ffffff;
                            text-decoration:none;
                            border-radius:5px;
                            font-weight:bold;
                        "
                    >
                        Open Corrected Stamped Material Request
                    </a>
                </p>
                """

                # ==================================================
                # ENGINEER EMAIL
                # ==================================================

                for engineer in engineers:

                    engineer_email = (
                        engineer.get("email")
                        or ""
                    ).strip()

                    engineer_recipient_name = (
                        engineer.get("fullname")
                        or "Engineer"
                    )

                    if not engineer_email:
                        continue

                    engineer_email_html = f"""
                    <h2>
                        Material Request Resubmitted for
                        Engineering Review
                    </h2>

                    <p>
                        Hello <b>{engineer_recipient_name}</b>,
                    </p>

                    <p>
                        The Purchase Department has corrected
                        and resubmitted the following Material
                        Request after the Engineer requested
                        corrections.
                    </p>

                    <hr>

                    <h3>
                        Material Request Details
                    </h3>

                    <p>
                        <b>Request Number:</b>
                        {request_number}
                    </p>

                    <p>
                        <b>Project:</b>
                        {project_name}
                    </p>

                    <p>
                        <b>Requested By:</b>
                        {requester_name}
                    </p>

                    <p>
                        <b>Request Date:</b>
                        {request_date}
                    </p>

                    <p>
                        <b>Description:</b>
                        {description or "No description provided"}
                    </p>

                    <p>
                        <b>Purchase Officer:</b>
                        {purchase_name}
                    </p>

                    <p>
                        <b>Resubmission Date & Time:</b>
                        {resubmission_date}
                    </p>

                    <p>
                        <b>New Status:</b>
                        <span
                            style="
                                color:#006b3c;
                                font-weight:bold;
                            "
                        >
                            Pending Engineer Review
                        </span>
                    </p>

                    <hr>

                    <h3>
                        Previous Engineer Correction
                    </h3>

                    <div
                        style="
                            background:#fff7ed;
                            border-left:4px solid #f59e0b;
                            padding:14px 16px;
                            margin:12px 0;
                            line-height:1.6;
                        "
                    >
                        {previous_engineer_comment}
                    </div>

                    <hr>

                    <h3>
                        Corrected Document
                    </h3>

                    <p>
                        A new corrected Material Request PDF
                        has been digitally stamped by the Purchase
                        Department.
                    </p>

                    {document_button}

                    <hr>

                    <p>
                        <b>
                            Important:
                        </b>
                        The original Material Request number
                        remains unchanged:
                    </p>

                    <p
                        style="
                            font-size:18px;
                            font-weight:bold;
                            color:#8A1538;
                        "
                    >
                        {request_number}
                    </p>

                    <p>
                        Please review the corrected Material
                        Request and proceed with Engineering
                        approval or request further correction.
                    </p>

                    <p>
                        Regards,<br>
                        <b>
                            Prestigious Trading & Construction W.L.L.
                        </b>
                    </p>

                    <p>
                        <small>
                            This notification was generated
                            automatically by the Construction
                            Management System.
                        </small>
                    </p>
                    """

                    try:

                        send_email(
                            engineer_email,

                            (
                                "Material Request Resubmitted "
                                "for Engineering Review - "
                                f"{request_number}"
                            ),

                            engineer_email_html
                        )

                    except Exception as engineer_email_error:

                        print(
                            "PURCHASE CORRECTION ENGINEER EMAIL ERROR:",
                            engineer_email,
                            repr(engineer_email_error)
                        )

            except Exception as email_error:

                print(
                    "PURCHASE CORRECTION EMAIL "
                    "NOTIFICATION PROCESS ERROR:",
                    repr(email_error)
                )

            # ==================================================
            # SUCCESS
            # ==================================================

            flash(
                (
                    f"Material Request {request_number} has been "
                    "corrected and resubmitted to Engineering. "
                    "The original MR number has been retained."
                ),
                "success"
            )

            return redirect(
                url_for(
                    "construction_purchase_dashboard"
                )
            )

        # ==================================================
        # GET REQUEST
        # ==================================================

        return render_template(
            "construction_admin/"
            "construction_purchase_material_request_correction.html",

            material_request=material_request,

            purchase_name=purchase_name
        )

    except Exception as e:

        if conn:

            try:
                conn.rollback()
            except Exception:
                pass

        import traceback

        print("=" * 80)
        print(
            "PURCHASE MATERIAL REQUEST CORRECTION ERROR"
        )

        print(
            "ERROR TYPE:",
            type(e).__name__
        )

        print(
            "ERROR MESSAGE:",
            str(e)
        )

        print(
            "FULL TRACEBACK:"
        )

        traceback.print_exc()

        print("=" * 80)

        flash(
            f"Material Request correction error: {str(e)}",
            "danger"
        )

        return redirect(
            url_for(
                "construction_purchase_dashboard"
            )
        )

    finally:

        if cursor:

            try:
                cursor.close()
            except Exception:
                pass

        if conn:

            try:
                conn.close()
            except Exception:
                pass











# =====================================
# PURCHASE AUDIT TRAIL
# =====================================

@app.route("/construction/purchase/audit-trail")
def construction_purchase_audit_trail():

    # ---------------------------------
    # LOGIN PROTECTION
    # ---------------------------------
    if "construction_admin_id" not in session:
        return redirect(url_for("admin_login"))

    # ---------------------------------
    # ROLE PROTECTION
    # ---------------------------------
    role = (
        session.get("construction_admin_role") or ""
    ).strip().lower()

    allowed_roles = [
        "super_admin",
        "purchase",
        "engineer",
        "manager",
        "account"
    ]

    if role not in allowed_roles:

        flash(
            "You are not authorized to access the Purchase Audit Trail.",
            "danger"
        )

        return redirect(
            url_for("admin_login")
        )

    conn = None
    cursor = None

    try:

        # ---------------------------------
        # DATABASE
        # ---------------------------------
        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        # =================================================
        # LOAD COMPLETE PURCHASE AUDIT HISTORY
        #
        # SOURCE 1:
        # construction_purchase_audit_logs
        #
        # SOURCE 2:
        # construction_purchase_procurement_actions
        #
        # The second source contains procurement events such
        # as:
        #
        # DOCUMENT_UPLOADED
        # CARD_PROCUREMENT_COMPLETED
        #
        # utf8mb4 conversion prevents MySQL collation
        # conflicts when combining the two audit sources.
        # =================================================

        cursor.execute("""
            SELECT
                a.id,

                a.material_request_id,

                a.procurement_id,

                a.user_id,

                CONVERT(
                    a.user_name USING utf8mb4
                ) AS user_name,

                CONVERT(
                    a.user_role USING utf8mb4
                ) AS user_role,

                CONVERT(
                    a.action USING utf8mb4
                ) AS action,

                CONVERT(
                    a.description USING utf8mb4
                ) AS description,

                CONVERT(
                    a.old_status USING utf8mb4
                ) AS old_status,

                CONVERT(
                    a.new_status USING utf8mb4
                ) AS new_status,

                CONVERT(
                    a.ip_address USING utf8mb4
                ) AS ip_address,

                a.created_at,

                CONVERT(
                    mr.request_number USING utf8mb4
                ) AS request_number,

                CONVERT(
                    mr.project_name USING utf8mb4
                ) AS project_name

            FROM construction_purchase_audit_logs a

            LEFT JOIN construction_purchase_material_requests mr
                ON mr.id = a.material_request_id


            UNION ALL


            SELECT
                (1000000000 + pa.id) AS id,

                p.material_request_id,

                pa.procurement_id,

                pa.performed_by AS user_id,

                CONVERT(
                    pa.performed_by_name USING utf8mb4
                ) AS user_name,

                CONVERT(
                    'purchase' USING utf8mb4
                ) AS user_role,

                CONVERT(
                    pa.action_type USING utf8mb4
                ) AS action,

                CONVERT(
                    pa.action_description USING utf8mb4
                ) AS description,

                CONVERT(
                    pa.from_status USING utf8mb4
                ) AS old_status,

                CONVERT(
                    pa.to_status USING utf8mb4
                ) AS new_status,

                NULL AS ip_address,

                pa.performed_at AS created_at,

                CONVERT(
                    mr.request_number USING utf8mb4
                ) AS request_number,

                CONVERT(
                    mr.project_name USING utf8mb4
                ) AS project_name

            FROM construction_purchase_procurement_actions pa

            INNER JOIN construction_purchase_procurement p
                ON p.id = pa.procurement_id

            LEFT JOIN construction_purchase_material_requests mr
                ON mr.id = p.material_request_id

            ORDER BY
                created_at DESC,
                id DESC
        """)

        audit_logs = cursor.fetchall() or []

        # =================================================
        # SUMMARY COUNTS
        # =================================================

        total_logs = len(audit_logs)

        approved_count = 0
        declined_count = 0
        correction_count = 0
        resubmitted_count = 0
        pending_count = 0
        completed_count = 0

        # =================================================
        # CALCULATE SUMMARY COUNTS
        # =================================================

        for log in audit_logs:

            action = str(
                log.get("action") or ""
            ).strip().lower()

            old_status = str(
                log.get("old_status") or ""
            ).strip().lower()

            new_status = str(
                log.get("new_status") or ""
            ).strip().lower()

            # ---------------------------------
            # APPROVED
            # ---------------------------------

            if (
                "approved" in action
                or "approved" in new_status
            ):
                approved_count += 1

            # ---------------------------------
            # DECLINED
            # ---------------------------------

            if (
                "declined" in action
                or "declined" in new_status
            ):
                declined_count += 1

            # ---------------------------------
            # CORRECTION
            # ---------------------------------

            if (
                "correction" in action
                or "correction" in old_status
                or "correction" in new_status
            ):
                correction_count += 1

            # ---------------------------------
            # RESUBMITTED
            # ---------------------------------

            if (
                "resubmit" in action
                or "resubmitted" in action
            ):
                resubmitted_count += 1

            # ---------------------------------
            # PENDING
            # ---------------------------------

            if (
                "pending" in action
                or "pending" in new_status
            ):
                pending_count += 1

            # ---------------------------------
            # COMPLETED
            # ---------------------------------

            if (
                "completed" in action
                or "completed" in new_status
            ):
                completed_count += 1

        # =================================================
        # RENDER AUDIT TRAIL
        # =================================================

        return render_template(
            "construction_admin/"
            "construction_purchase_audit_trail.html",

            audit_logs=audit_logs,

            total_logs=total_logs,

            approved_count=approved_count,

            declined_count=declined_count,

            correction_count=correction_count,

            resubmitted_count=resubmitted_count,

            pending_count=pending_count,

            completed_count=completed_count
        )

    except Exception as e:

        # ---------------------------------
        # ERROR LOGGING
        # ---------------------------------

        print("==================================================")
        print("PURCHASE AUDIT TRAIL ERROR")
        print(type(e).__name__)
        print(repr(e))
        print("==================================================")

        if conn:

            try:
                conn.rollback()
            except Exception:
                pass

        # ---------------------------------
        # USER MESSAGE
        # ---------------------------------

        flash(
            "Unable to load the Purchase Audit Trail.",
            "danger"
        )

        return redirect(
            url_for("construction_purchase_dashboard")
        )

    finally:

        # ---------------------------------
        # CLOSE CURSOR
        # ---------------------------------

        if cursor:

            try:
                cursor.close()
            except Exception:
                pass

        # ---------------------------------
        # CLOSE DATABASE
        # ---------------------------------

        if conn:

            try:
                conn.close()
            except Exception:
                pass











# ============================================================
# PURCHASE - PROCUREMENT
#
# Workflow:
#
# Approved Material Request
#        ↓
# Purchase starts Card / Quotation Procurement
#        ↓
# Pending Procurement Documents
#        ↓
# Account Review
#        ↓
# Manager Review
#        ↓
# Pending Final Procurement
#        ↓
# Purchase Dashboard
#        ↓
# Separate Final Procurement Route
# ============================================================

@app.route(
    "/construction/purchase/material-request/<int:request_id>/procurement",
    methods=["GET", "POST"]
)
def construction_purchase_material_request_procurement(request_id):

    # ========================================================
    # LOGIN PROTECTION
    # ========================================================

    if "construction_admin_id" not in session:
        return redirect(url_for("admin_login"))

    # ========================================================
    # CURRENT USER
    # ========================================================

    user_id, user_name, role = (
        _construction_purchase_current_user()
    )

    # ========================================================
    # ROLE PROTECTION
    # ========================================================

    if role != "purchase":

        flash(
            "You are not authorized to access Purchase procurement.",
            "danger"
        )

        return redirect(
            url_for("construction_role_dashboard")
        )

    conn = None
    cursor = None

    try:

        # ====================================================
        # DATABASE
        # ====================================================

        conn = get_db_connection()

        cursor = conn.cursor(
            dictionary=True
        )

        # ====================================================
        # LOAD MATERIAL REQUEST
        # ====================================================

        cursor.execute(
            """
            SELECT
                id,
                request_number,
                project_name,
                requested_by,
                requested_by_name,
                request_date,
                description,

                original_file_name,
                original_file_url,
                original_public_id,

                signed_file_name,
                signed_file_url,
                signed_public_id,

                purchase_signature_data,
                purchase_signed_at,

                status,

                engineer_id,
                engineer_name,
                engineer_reviewed_at,
                engineer_comment,

                manager_id,
                manager_name,
                manager_approved_at,
                manager_comment,

                procurement_method,

                created_at,
                updated_at

            FROM construction_purchase_material_requests

            WHERE id = %s

            LIMIT 1
            """,
            (request_id,)
        )

        material_request = cursor.fetchone()

        # ====================================================
        # REQUEST NOT FOUND
        # ====================================================

        if not material_request:

            flash(
                "Material Request was not found.",
                "danger"
            )

            return redirect(
                url_for(
                    "construction_purchase_dashboard"
                )
            )

        # ====================================================
        # MATERIAL REQUEST MUST BE APPROVED
        #
        # It may also be Completed when viewing the historical
        # procurement record.
        # ====================================================

        if material_request["status"] not in (
            "Approved",
            "Completed"
        ):

            flash(
                "This Material Request is not currently available for procurement.",
                "warning"
            )

            return redirect(
                url_for(
                    "construction_purchase_dashboard"
                )
            )

        # ====================================================
        # LOAD EXISTING PROCUREMENT
        # ====================================================

        cursor.execute(
            """
            SELECT
                *
            FROM construction_purchase_procurement

            WHERE material_request_id = %s

            ORDER BY id DESC

            LIMIT 1
            """,
            (request_id,)
        )

        procurement = cursor.fetchone()

        # ====================================================
        # POST ACTION
        # ====================================================

        if request.method == "POST":

            action = (
                request.form.get("action")
                or request.form.get("stage_action")
                or ""
            ).strip()

            # ==================================================
            # START CARD PROCUREMENT
            # ==================================================

            if action == "start_card_procurement":

                # ----------------------------------------------
                # PREVENT DUPLICATE PROCUREMENT
                # ----------------------------------------------

                if procurement:

                    flash(
                        "Procurement has already been started for this Material Request.",
                        "warning"
                    )

                    return redirect(
                        url_for(
                            "construction_purchase_material_request_procurement",
                            request_id=request_id
                        )
                    )

                # ----------------------------------------------
                # FORM VALUES
                # ----------------------------------------------

                supplier_name = (
                    request.form.get("supplier_name")
                    or ""
                ).strip()

                supplier_contact = (
                    request.form.get("supplier_contact")
                    or ""
                ).strip()

                amount = _construction_parse_amount(
                    request.form.get("procurement_amount")
                )

                currency = (
                    request.form.get("currency")
                    or "QAR"
                ).strip().upper()

                card_paid_by_name = (
                    request.form.get("card_paid_by_name")
                    or ""
                ).strip()

                card_payment_reference = (
                    request.form.get("card_payment_reference")
                    or ""
                ).strip()

                card_paid_at_raw = (
                    request.form.get("card_paid_at")
                    or ""
                ).strip()

                notes = (
                    request.form.get("notes")
                    or ""
                ).strip()

                # ----------------------------------------------
                # VALIDATION
                # ----------------------------------------------

                errors = []

                if not supplier_name:
                    errors.append(
                        "Supplier name is required."
                    )

                if not supplier_contact:
                    errors.append(
                        "Supplier contact is required."
                    )

                if amount is None:
                    errors.append(
                        "A valid procurement amount is required."
                    )

                if not currency:
                    errors.append(
                        "Currency is required."
                    )

                if not card_paid_by_name:
                    errors.append(
                        "Name of the person who paid with the company card is required."
                    )

                if not card_paid_at_raw:
                    errors.append(
                        "Card payment date and time are required."
                    )

                if errors:

                    for error in errors:
                        flash(error, "danger")

                    return redirect(
                        url_for(
                            "construction_purchase_material_request_procurement",
                            request_id=request_id
                        )
                    )

                # ----------------------------------------------
                # CARD PAYMENT DATE
                # ----------------------------------------------

                card_paid_at = None

                try:

                    card_paid_at = datetime.strptime(
                        card_paid_at_raw,
                        "%Y-%m-%dT%H:%M"
                    )

                except ValueError:

                    try:

                        card_paid_at = datetime.strptime(
                            card_paid_at_raw,
                            "%Y-%m-%d %H:%M:%S"
                        )

                    except ValueError:

                        flash(
                            "Invalid card payment date and time.",
                            "danger"
                        )

                        return redirect(
                            url_for(
                                "construction_purchase_material_request_procurement",
                                request_id=request_id
                            )
                        )

                # ----------------------------------------------
                # INSERT PROCUREMENT
                # ----------------------------------------------

                cursor.execute(
                    """
                    INSERT INTO construction_purchase_procurement
                    (
                        material_request_id,

                        procurement_method,
                        workflow_stage,

                        initiated_by,
                        initiated_by_name,
                        initiated_at,

                        card_paid_by,
                        card_paid_by_name,
                        card_paid_at,
                        card_payment_reference,

                        supplier_name,
                        supplier_contact,

                        procurement_amount,
                        currency,

                        payment_date,

                        status,

                        notes,

                        created_at,
                        updated_at
                    )

                    VALUES
                    (
                        %s,

                        'Card',
                        'Procurement',

                        %s,
                        %s,
                        NOW(),

                        %s,
                        %s,
                        %s,
                        %s,

                        %s,
                        %s,

                        %s,
                        %s,

                        %s,

                        'Pending Procurement Documents',

                        %s,

                        NOW(),
                        NOW()
                    )
                    """,
                    (
                        request_id,

                        user_id,
                        user_name,

                        user_id,
                        card_paid_by_name,
                        card_paid_at,
                        card_payment_reference or None,

                        supplier_name,
                        supplier_contact,

                        amount,
                        currency,

                        card_paid_at.date(),

                        notes or None
                    )
                )

                procurement_id = cursor.lastrowid

                # ----------------------------------------------
                # AUDIT
                # ----------------------------------------------

                _construction_log_procurement_action(
                    cursor=cursor,
                    procurement_id=procurement_id,

                    action_type="CARD_PROCUREMENT_STARTED",

                    action_description=(
                        "Purchase started Card Procurement for "
                        f"Material Request "
                        f"{material_request['request_number']}."
                    ),

                    performed_by=user_id,
                    performed_by_name=user_name,

                    from_status=None,

                    to_status="Pending Procurement Documents",

                    notes=notes or None
                )

                # ----------------------------------------------
                # UPDATE MATERIAL REQUEST
                # ----------------------------------------------

                cursor.execute(
                    """
                    UPDATE construction_purchase_material_requests

                    SET
                        procurement_method = 'Card',
                        updated_at = NOW()

                    WHERE id = %s
                    """,
                    (request_id,)
                )

                conn.commit()

                flash(
                    "Card procurement has been started. Upload the required procurement documents before submitting to Account.",
                    "success"
                )

                return redirect(
                    url_for(
                        "construction_purchase_material_request_procurement",
                        request_id=request_id
                    )
                )

            # ==================================================
            # START QUOTATION / LPO PROCUREMENT
            # ==================================================

            elif action == "start_quotation_procurement":

                # ----------------------------------------------
                # PREVENT DUPLICATE PROCUREMENT
                # ----------------------------------------------

                if procurement:

                    flash(
                        "Procurement has already been started for this Material Request.",
                        "warning"
                    )

                    return redirect(
                        url_for(
                            "construction_purchase_material_request_procurement",
                            request_id=request_id
                        )
                    )

                # ----------------------------------------------
                # FORM VALUES
                # ----------------------------------------------

                supplier_name = (
                    request.form.get("supplier_name")
                    or ""
                ).strip()

                supplier_contact = (
                    request.form.get("supplier_contact")
                    or ""
                ).strip()

                amount = _construction_parse_amount(
                    request.form.get("procurement_amount")
                )

                currency = (
                    request.form.get("currency")
                    or "QAR"
                ).strip().upper()

                quotation_number = (
                    request.form.get("quotation_number")
                    or ""
                ).strip()

                quotation_date_raw = (
                    request.form.get("quotation_date")
                    or ""
                ).strip()

                lpo_number = (
                    request.form.get("lpo_number")
                    or ""
                ).strip()

                notes = (
                    request.form.get("notes")
                    or ""
                ).strip()

                # ----------------------------------------------
                # VALIDATION
                # ----------------------------------------------

                errors = []

                if not supplier_name:
                    errors.append(
                        "Supplier name is required."
                    )

                if not supplier_contact:
                    errors.append(
                        "Supplier contact is required."
                    )

                if amount is None:
                    errors.append(
                        "A valid procurement amount is required."
                    )

                if not quotation_number:
                    errors.append(
                        "Quotation number is required."
                    )

                if not quotation_date_raw:
                    errors.append(
                        "Quotation date is required."
                    )

                if not lpo_number:
                    errors.append(
                        "LPO number is required."
                    )

                if errors:

                    for error in errors:
                        flash(error, "danger")

                    return redirect(
                        url_for(
                            "construction_purchase_material_request_procurement",
                            request_id=request_id
                        )
                    )

                # ----------------------------------------------
                # QUOTATION DATE
                # ----------------------------------------------

                try:

                    quotation_date = datetime.strptime(
                        quotation_date_raw,
                        "%Y-%m-%d"
                    ).date()

                except ValueError:

                    flash(
                        "Invalid quotation date.",
                        "danger"
                    )

                    return redirect(
                        url_for(
                            "construction_purchase_material_request_procurement",
                            request_id=request_id
                        )
                    )

                # ----------------------------------------------
                # INSERT PROCUREMENT
                # ----------------------------------------------

                cursor.execute(
                    """
                    INSERT INTO construction_purchase_procurement
                    (
                        material_request_id,

                        procurement_method,
                        workflow_stage,

                        initiated_by,
                        initiated_by_name,
                        initiated_at,

                        supplier_name,
                        supplier_contact,

                        procurement_amount,
                        currency,

                        quotation_number,
                        quotation_date,
                        lpo_number,

                        pre_purchase_submitted_by,
                        pre_purchase_submitted_by_name,
                        pre_purchase_submitted_at,

                        status,

                        notes,

                        created_at,
                        updated_at
                    )

                    VALUES
                    (
                        %s,

                        'Quotation',
                        'Procurement',

                        %s,
                        %s,
                        NOW(),

                        %s,
                        %s,

                        %s,
                        %s,

                        %s,
                        %s,
                        %s,

                        %s,
                        %s,
                        NOW(),

                        'Pending Procurement Documents',

                        %s,

                        NOW(),
                        NOW()
                    )
                    """,
                    (
                        request_id,

                        user_id,
                        user_name,

                        supplier_name,
                        supplier_contact,

                        amount,
                        currency,

                        quotation_number,
                        quotation_date,
                        lpo_number,

                        user_id,
                        user_name,

                        notes or None
                    )
                )

                procurement_id = cursor.lastrowid

                # ----------------------------------------------
                # AUDIT
                # ----------------------------------------------

                _construction_log_procurement_action(
                    cursor=cursor,
                    procurement_id=procurement_id,

                    action_type="QUOTATION_LPO_STARTED",

                    action_description=(
                        "Purchase started the Quotation/LPO "
                        "Procurement process for Material Request "
                        f"{material_request['request_number']}."
                    ),

                    performed_by=user_id,
                    performed_by_name=user_name,

                    from_status=None,

                    to_status="Pending Procurement Documents",

                    notes=notes or None
                )

                # ----------------------------------------------
                # UPDATE MATERIAL REQUEST
                # ----------------------------------------------

                cursor.execute(
                    """
                    UPDATE construction_purchase_material_requests

                    SET
                        procurement_method = 'Quotation',
                        updated_at = NOW()

                    WHERE id = %s
                    """,
                    (request_id,)
                )

                conn.commit()

                flash(
                    "Quotation/LPO procurement has been started. Upload the required procurement documents before submitting to Account.",
                    "success"
                )

                return redirect(
                    url_for(
                        "construction_purchase_material_request_procurement",
                        request_id=request_id
                    )
                )

            # ==================================================
            # SUBMIT PROCUREMENT TO ACCOUNT
            # ==================================================

            elif action == "submit_to_account":

                if not procurement:

                    flash(
                        "Start procurement before submitting it to Account.",
                        "warning"
                    )

                    return redirect(
                        url_for(
                            "construction_purchase_material_request_procurement",
                            request_id=request_id
                        )
                    )

                current_status = (
                    procurement.get("status")
                    or ""
                ).strip()

                if current_status not in (
                    "Pending Procurement Documents",
                    "Procurement Correction Required"
                ):

                    flash(
                        "This procurement is not ready to be submitted to Account.",
                        "warning"
                    )

                    return redirect(
                        url_for(
                            "construction_purchase_material_request_procurement",
                            request_id=request_id
                        )
                    )

                cursor.execute(
                    """
                    UPDATE construction_purchase_procurement

                    SET
                        workflow_stage = 'Account Review',
                        submitted_to_account_at = NOW(),
                        status = 'Pending Account Review',
                        updated_at = NOW()

                    WHERE
                        id = %s

                        AND status IN (
                            'Pending Procurement Documents',
                            'Procurement Correction Required'
                        )
                    """,
                    (
                        procurement["id"],
                    )
                )

                if cursor.rowcount != 1:

                    conn.rollback()

                    flash(
                        "The procurement could not be submitted because its status has changed. Please refresh and try again.",
                        "warning"
                    )

                    return redirect(
                        url_for(
                            "construction_purchase_material_request_procurement",
                            request_id=request_id
                        )
                    )

                _construction_log_procurement_action(
                    cursor=cursor,
                    procurement_id=procurement["id"],

                    action_type="SUBMITTED_TO_ACCOUNT",

                    action_description=(
                        "Purchase submitted procurement for "
                        f"Material Request "
                        f"{material_request['request_number']} "
                        "to Account for review."
                    ),

                    performed_by=user_id,
                    performed_by_name=user_name,

                    from_status=current_status,

                    to_status="Pending Account Review"
                )

                conn.commit()

                flash(
                    "Procurement has been submitted to Account for review.",
                    "success"
                )

                return redirect(
                    url_for(
                        "construction_purchase_material_request_procurement",
                        request_id=request_id
                    )
                )

            # ==================================================
            # NO FINAL PROCUREMENT ACTIONS HERE
            #
            # Final Procurement now has its own route.
            # ==================================================

            elif action in (
                "start_final_procurement",
                "submit_final_documents",
                "continue_final_purchase",
                "start_final_purchase",
                "final_purchase",
                "start_final_payment"
            ):

                flash(
                    "Final Procurement is handled from the Purchase Dashboard.",
                    "info"
                )

                return redirect(
                    url_for(
                        "construction_purchase_dashboard"
                    )
                )

            # ==================================================
            # INVALID ACTION
            # ==================================================

            else:

                flash(
                    "Invalid procurement action.",
                    "danger"
                )

                return redirect(
                    url_for(
                        "construction_purchase_material_request_procurement",
                        request_id=request_id
                    )
                )

        # ====================================================
        # LOAD PROCUREMENT DOCUMENTS
        # ====================================================

        procurement_documents = []

        if procurement:

            cursor.execute(
                """
                SELECT
                    id,
                    procurement_id,
                    document_type,
                    workflow_stage,
                    document_title,
                    original_file_name,
                    file_url,
                    public_id,
                    uploaded_by,
                    uploaded_by_name,
                    uploaded_at,
                    notes

                FROM construction_purchase_procurement_documents

                WHERE procurement_id = %s

                ORDER BY
                    uploaded_at DESC,
                    id DESC
                """,
                (
                    procurement["id"],
                )
            )

            procurement_documents = (
                cursor.fetchall()
                or []
            )

        # ====================================================
        # LOAD PROCUREMENT AUDIT TRAIL
        # ====================================================

        procurement_actions = []

        if procurement:

            cursor.execute(
                """
                SELECT
                    id,
                    procurement_id,
                    action_type,
                    action_description,
                    from_status,
                    to_status,
                    performed_by,
                    performed_by_name,
                    performed_at,
                    notes

                FROM construction_purchase_procurement_actions

                WHERE procurement_id = %s

                ORDER BY
                    performed_at DESC,
                    id DESC
                """,
                (
                    procurement["id"],
                )
            )

            procurement_actions = (
                cursor.fetchall()
                or []
            )

        # ====================================================
        # RENDER
        # ====================================================

        return render_template(
            "construction_admin/construction_purchase_material_request_procurement.html",

            material_request=material_request,

            procurement=procurement,

            procurement_documents=procurement_documents,

            procurement_actions=procurement_actions,

            current_user_id=user_id,

            current_user_name=user_name,

            current_user_role=role
        )

    # ========================================================
    # ERROR HANDLING
    # ========================================================

    except Exception as e:

        if conn:

            try:
                conn.rollback()
            except Exception:
                pass

        print(
            "=================================================="
        )

        print(
            "CONSTRUCTION PROCUREMENT ROUTE ERROR"
        )

        print(
            type(e).__name__
        )

        print(
            repr(e)
        )

        import traceback

        traceback.print_exc()

        print(
            "=================================================="
        )

        flash(
            "Unable to process the procurement request.",
            "danger"
        )

        return redirect(
            url_for(
                "construction_purchase_dashboard"
            )
        )

    # ========================================================
    # CLEANUP
    # ========================================================

    finally:

        if cursor:

            try:
                cursor.close()
            except Exception:
                pass

        if conn:

            try:
                conn.close()
            except Exception:
                pass











# ==========================================================
# PURCHASE OFFICER - UPLOAD PROCUREMENT DOCUMENT
# ==========================================================

@app.route(
    "/construction/purchase/procurement/<int:procurement_id>/documents/upload",
    methods=["POST"]
)
def construction_purchase_procurement_documents_upload(procurement_id):

    # ======================================================
    # LOGIN PROTECTION
    # ======================================================

    if "construction_admin_id" not in session:
        flash(
            "Please sign in to access the Purchase Department.",
            "warning"
        )
        return redirect(url_for("admin_login"))

    # ======================================================
    # ROLE PROTECTION
    # ======================================================

    role = (
        session.get("construction_admin_role") or ""
    ).strip().lower()

    if role != "purchase":
        flash(
            "You are not authorized to upload procurement documents.",
            "danger"
        )
        return redirect(url_for("admin_login"))

    # ======================================================
    # CURRENT PURCHASE USER
    # ======================================================

    purchase_user_id = session.get(
        "construction_admin_id"
    )

    purchase_user_name = (
        session.get("construction_admin_name")
        or session.get("name")
        or session.get("fullname")
        or session.get("username")
        or "Purchase Officer"
    )

    conn = None
    cursor = None
    procurement = None

    # ======================================================
    # FORM DATA
    # ======================================================

    document_type = (
        request.form.get("document_type") or ""
    ).strip()

    document_title = (
        request.form.get("document_title") or ""
    ).strip()

    document_notes = (
        request.form.get("notes") or ""
    ).strip()

    uploaded_file = request.files.get("document")

    if not uploaded_file or not uploaded_file.filename:
        flash(
            "Please select a document to upload.",
            "danger"
        )
        return redirect(
            url_for("construction_purchase_dashboard")
        )

    try:

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        # ==================================================
        # LOAD PROCUREMENT + MATERIAL REQUEST
        # ==================================================

        cursor.execute(
            """
            SELECT
                p.*,

                mr.id AS material_request_id,
                mr.request_number,
                mr.project_name,
                mr.requested_by,
                mr.requested_by_name,
                mr.request_date,
                mr.description AS material_request_description,
                mr.status AS material_request_status

            FROM construction_purchase_procurement p

            INNER JOIN construction_purchase_material_requests mr
                ON mr.id = p.material_request_id

            WHERE p.id = %s

            LIMIT 1
            """,
            (procurement_id,)
        )

        procurement = cursor.fetchone()

        if not procurement:

            flash(
                "Procurement record was not found.",
                "danger"
            )

            return redirect(
                url_for("construction_purchase_dashboard")
            )

        # ==================================================
        # MATERIAL REQUEST MUST BE APPROVED
        # ==================================================

        material_request_status = (
            procurement.get("material_request_status")
            or ""
        ).strip()

        if material_request_status != "Approved":

            flash(
                "Documents can only be uploaded for a "
                "Manager-approved Material Request.",
                "danger"
            )

            return redirect(
                url_for(
                    "construction_purchase_material_request_procurement",
                    request_id=procurement["material_request_id"]
                )
            )

        # ==================================================
        # CURRENT PROCUREMENT STATUS
        # ==================================================

        procurement_status = (
            procurement.get("status") or ""
        ).strip()

        # ==================================================
        # PREVENT UPLOAD AFTER SUBMISSION TO ACCOUNT
        #
        # IMPORTANT:
        # Direct Card is completed immediately after
        # Card Payment Evidence is uploaded.
        #
        # Quotation + LPO continues through its normal
        # Account / Manager / Final Procurement workflow.
        # ==================================================

        locked_statuses = {
            "Pending Account Review",
            "Account Correction Required",
            "Pending Manager Review",
            "Manager Correction Required",
            "Pending Final Documents",
            "Completed"
        }

        if procurement_status in locked_statuses:

            flash(
                "This procurement package is already beyond "
                "the Purchase document-upload stage. "
                "Additional procurement documents cannot "
                "be uploaded here.",
                "warning"
            )

            return redirect(
                url_for(
                    "construction_purchase_material_request_procurement",
                    request_id=procurement["material_request_id"]
                )
            )

        # ==================================================
        # ALLOWED PROCUREMENT METHODS
        # ==================================================

        procurement_method = (
            procurement.get("procurement_method") or ""
        ).strip()

        if procurement_method not in {
            "Card",
            "Quotation"
        }:

            flash(
                "This procurement record does not have a valid "
                "procurement method.",
                "danger"
            )

            return redirect(
                url_for(
                    "construction_purchase_material_request_procurement",
                    request_id=procurement["material_request_id"]
                )
            )

        # ==================================================
        # DETERMINE WORKFLOW STAGE
        # ==================================================

        workflow_stage = (
            procurement.get("workflow_stage") or ""
        ).strip()

        if not workflow_stage:
            workflow_stage = "Procurement"

        # ==================================================
        # ALLOWED DOCUMENT TYPES
        # ==================================================

        if procurement_method == "Card":

            allowed_document_types = {
                "Quotation",
                "Invoice",
                "Receipt",
                "Card Payment Evidence",
                "Delivery Note",
                "Other Supporting Document"
            }

        elif procurement_method == "Quotation":

            allowed_document_types = {
                "Quotation",
                "LPO",
                "Other Supporting Document"
            }

        else:

            allowed_document_types = set()

        # ==================================================
        # VALIDATE DOCUMENT TYPE
        # ==================================================

        if not document_type:

            flash(
                "Please select the document type.",
                "danger"
            )

            return redirect(
                url_for(
                    "construction_purchase_material_request_procurement",
                    request_id=procurement["material_request_id"]
                )
            )

        if document_type not in allowed_document_types:

            flash(
                "The selected document type is not valid "
                "for this procurement method.",
                "danger"
            )

            return redirect(
                url_for(
                    "construction_purchase_material_request_procurement",
                    request_id=procurement["material_request_id"]
                )
            )

        # ==================================================
        # VALIDATE FILE NAME
        # ==================================================

        original_filename = secure_filename(
            uploaded_file.filename
        )

        if not original_filename:

            flash(
                "Invalid file name.",
                "danger"
            )

            return redirect(
                url_for(
                    "construction_purchase_material_request_procurement",
                    request_id=procurement["material_request_id"]
                )
            )

        # ==================================================
        # VALIDATE FILE EXTENSION
        # ==================================================

        extension = (
            original_filename.rsplit(".", 1)[-1].lower()
            if "." in original_filename
            else ""
        )

        allowed_extensions = {
            "pdf",
            "jpg",
            "jpeg",
            "png",
            "webp",
            "gif",
            "bmp",
            "tif",
            "tiff",
            "doc",
            "docx",
            "xls",
            "xlsx",
            "csv"
        }

        if extension not in allowed_extensions:

            flash(
                "Unsupported file type. Please upload PDF, "
                "image, Word, Excel, or CSV documents.",
                "danger"
            )

            return redirect(
                url_for(
                    "construction_purchase_material_request_procurement",
                    request_id=procurement["material_request_id"]
                )
            )

        # ==================================================
        # DEFAULT DOCUMENT TITLE
        # ==================================================

        if not document_title:
            document_title = original_filename

        # ==================================================
        # MATERIAL REQUEST NUMBER
        # ==================================================

        request_number = (
            procurement.get("request_number")
            or f"MR-{procurement['material_request_id']}"
        )

        safe_request_number = "".join(
            char
            if char.isalnum() or char in "-_"
            else "_"
            for char in str(request_number)
        )

        # ==================================================
        # CLOUDINARY FOLDER
        # ==================================================

        cloudinary_folder = (
            "prestigious_construction/"
            "purchase/procurement/"
            f"{safe_request_number}/"
            f"procurement_{procurement_id}"
        )

        # ==================================================
        # UNIQUE DOCUMENT PUBLIC ID
        # ==================================================

        document_uuid = str(uuid.uuid4())

        safe_document_type = "".join(
            char
            if char.isalnum() or char in "-_"
            else "_"
            for char in document_type.lower()
        )

        public_id = (
            f"{safe_document_type}_{document_uuid}"
        )

        # ==================================================
        # CLOUDINARY UPLOAD
        # ==================================================

        uploaded_file.seek(0)

        upload_result = cloudinary.uploader.upload(
            uploaded_file,
            resource_type="auto",
            folder=cloudinary_folder,
            public_id=public_id,
            format=extension,
            overwrite=False
        )

        file_url = upload_result.get("secure_url")

        cloudinary_public_id = upload_result.get(
            "public_id"
        )

        cloudinary_resource_type = upload_result.get(
            "resource_type"
        )

        cloudinary_format = upload_result.get(
            "format"
        )

        if not file_url:
            raise RuntimeError(
                "Cloudinary did not return a secure URL."
            )

        if not cloudinary_public_id:
            raise RuntimeError(
                "Cloudinary did not return a public ID."
            )

        print(
            "=================================================="
        )
        print(
            "CLOUDINARY PROCUREMENT DOCUMENT UPLOAD"
        )
        print(
            "Procurement ID:",
            procurement_id
        )
        print(
            "Procurement Method:",
            procurement_method
        )
        print(
            "Document Type:",
            document_type
        )
        print(
            "Resource Type:",
            cloudinary_resource_type
        )
        print(
            "Format:",
            cloudinary_format
        )
        print(
            "Original Filename:",
            original_filename
        )
        print(
            "Cloudinary URL:",
            file_url
        )
        print(
            "Cloudinary Public ID:",
            cloudinary_public_id
        )
        print(
            "=================================================="
        )

        # ==================================================
        # SAVE DOCUMENT RECORD
        # ==================================================

        cursor.execute(
            """
            INSERT INTO construction_purchase_procurement_documents
            (
                procurement_id,
                document_type,
                workflow_stage,
                document_title,
                original_file_name,
                file_url,
                public_id,
                uploaded_by,
                uploaded_by_name,
                uploaded_at,
                notes
            )
            VALUES
            (
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                NOW(),
                %s
            )
            """,
            (
                procurement_id,
                document_type,
                workflow_stage,
                document_title,
                original_filename,
                file_url,
                cloudinary_public_id,
                purchase_user_id,
                purchase_user_name,
                document_notes or None
            )
        )

        # ==================================================
        # AUDIT DOCUMENT UPLOAD
        # ==================================================

        _construction_log_procurement_action(
            cursor=cursor,
            procurement_id=procurement_id,
            action_type="DOCUMENT_UPLOADED",
            action_description=(
                f"Purchase uploaded procurement document "
                f"'{document_title}' ({document_type}) for "
                f"Material Request {request_number}."
            ),
            performed_by=purchase_user_id,
            performed_by_name=purchase_user_name,
            from_status=procurement_status or None,
            to_status=procurement_status or None,
            notes=document_notes or None
        )

        # ==================================================
        # DIRECT CARD PROCUREMENT COMPLETION
        #
        # THIS IS THE ONLY NEW WORKFLOW BEHAVIOUR.
        #
        # If the procurement method is Card AND the uploaded
        # document is Card Payment Evidence:
        #
        #     Card Payment Evidence uploaded
        #                  ↓
        #              Completed
        #
        # It does NOT go to Account.
        # It does NOT go to Manager.
        # It does NOT go to Final Procurement.
        #
        # QUOTATION + LPO IS NOT TOUCHED.
        # ==================================================

        card_procurement_completed = False

        if (
            procurement_method == "Card"
            and document_type == "Card Payment Evidence"
        ):

            # --------------------------------------------------
            # Mark the procurement itself as COMPLETED
            # --------------------------------------------------

            cursor.execute(
                """
                UPDATE construction_purchase_procurement
                SET
                    status = 'Completed',
                    workflow_stage = 'Completed',
                    completed_at = NOW(),
                    updated_at = NOW()
                WHERE id = %s
                LIMIT 1
                """,
                (procurement_id,)
            )

            # --------------------------------------------------
            # Audit the status transition
            # --------------------------------------------------

            _construction_log_procurement_action(
                cursor=cursor,
                procurement_id=procurement_id,
                action_type="CARD_PROCUREMENT_COMPLETED",
                action_description=(
                    f"Direct Card procurement for Material "
                    f"Request {request_number} was automatically "
                    f"completed after Card Payment Evidence "
                    f"was uploaded."
                ),
                performed_by=purchase_user_id,
                performed_by_name=purchase_user_name,
                from_status=procurement_status or None,
                to_status="Completed",
                notes=(
                    "Direct Card procurement completed "
                    "immediately after Card Payment Evidence "
                    "was successfully uploaded."
                )
            )

            card_procurement_completed = True

        # ==================================================
        # COMMIT
        # ==================================================

        conn.commit()

        # ==================================================
        # SUCCESS MESSAGE
        # ==================================================

        if card_procurement_completed:

            flash(
                "Card Payment Evidence uploaded successfully. "
                "The direct Card procurement has been "
                "automatically marked as Completed.",
                "success"
            )

        else:

            # --------------------------------------------------
            # QUOTATION + LPO BEHAVIOUR REMAINS UNCHANGED
            # --------------------------------------------------

            flash(
                f"{document_type} uploaded successfully. "
                "The package has NOT yet been submitted to Account.",
                "success"
            )

        # ==================================================
        # RETURN TO PROCUREMENT PAGE
        # ==================================================

        return redirect(
            url_for(
                "construction_purchase_material_request_procurement",
                request_id=procurement["material_request_id"]
            )
        )

    except Exception as e:

        if conn:
            try:
                conn.rollback()
            except Exception:
                pass

        print(
            "CONSTRUCTION PROCUREMENT DOCUMENT UPLOAD ERROR:",
            type(e).__name__,
            repr(e)
        )

        import traceback
        traceback.print_exc()

        flash(
            "Unable to upload the procurement document.",
            "danger"
        )

        if procurement:

            return redirect(
                url_for(
                    "construction_purchase_material_request_procurement",
                    request_id=procurement["material_request_id"]
                )
            )

        return redirect(
            url_for("construction_purchase_dashboard")
        )

    finally:

        if cursor:
            try:
                cursor.close()
            except Exception:
                pass

        if conn:
            try:
                conn.close()
            except Exception:
                pass












# ==========================================================
# PURCHASE OFFICER - SUBMIT COMPLETE PROCUREMENT PACKAGE
# TO ACCOUNT
#
# WORKFLOW:
#
# Purchase uploads documents
#          ↓
# Pending Procurement Documents
#          ↓
# Purchase clicks Submit to Account
#          ↓
# Pending Account Review
#
# This is the ONLY action that officially submits the
# procurement package to Account.
# ==========================================================

@app.route(
    "/construction/purchase/procurement/<int:procurement_id>/submit-to-account",
    methods=["POST"]
)
def construction_purchase_procurement_submit_to_account(
    procurement_id
):

    # ======================================================
    # LOGIN PROTECTION
    # ======================================================

    if "construction_admin_id" not in session:

        flash(
            "Please sign in to access the Purchase Department.",
            "warning"
        )

        return redirect(
            url_for("admin_login")
        )

    # ======================================================
    # ROLE PROTECTION
    # ======================================================

    role = (
        session.get("construction_admin_role") or ""
    ).strip().lower()

    if role != "purchase":

        flash(
            "You are not authorized to submit procurement packages.",
            "danger"
        )

        return redirect(
            construction_role_dashboard()
        )

    # ======================================================
    # CURRENT PURCHASE USER
    # ======================================================

    purchase_user_id = session.get(
        "construction_admin_id"
    )

    purchase_user_name = (
        session.get("construction_admin_name")
        or session.get("name")
        or session.get("fullname")
        or session.get("username")
        or "Purchase Officer"
    )

    conn = None
    cursor = None
    procurement = None

    try:

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        # ==================================================
        # LOAD PROCUREMENT + MATERIAL REQUEST
        # ==================================================

        cursor.execute(
            """
            SELECT
                p.*,

                mr.id AS material_request_id,
                mr.request_number,
                mr.project_name,
                mr.requested_by,
                mr.requested_by_name,
                mr.request_date,
                mr.description AS material_request_description,
                mr.status AS material_request_status

            FROM construction_purchase_procurement p

            INNER JOIN construction_purchase_material_requests mr
                ON mr.id = p.material_request_id

            WHERE p.id = %s

            LIMIT 1
            """,
            (procurement_id,)
        )

        procurement = cursor.fetchone()

        if not procurement:

            flash(
                "Procurement record was not found.",
                "danger"
            )

            return redirect(
                url_for("construction_purchase_dashboard")
            )

        # ==================================================
        # MATERIAL REQUEST MUST STILL BE APPROVED
        # ==================================================

        material_request_status = (
            procurement.get("material_request_status")
            or ""
        ).strip()

        if material_request_status != "Approved":

            flash(
                "This Material Request is no longer approved "
                "for procurement.",
                "danger"
            )

            return redirect(
                url_for(
                    "construction_purchase_material_request_procurement",
                    request_id=procurement["material_request_id"]
                )
            )

        # ==================================================
        # PROCUREMENT METHOD
        # ==================================================

        procurement_method = (
            procurement.get("procurement_method")
            or ""
        ).strip()

        if procurement_method not in {
            "Card",
            "Quotation"
        }:

            flash(
                "This procurement record does not have a valid "
                "procurement method.",
                "danger"
            )

            return redirect(
                url_for(
                    "construction_purchase_material_request_procurement",
                    request_id=procurement["material_request_id"]
                )
            )

        # ==================================================
        # CURRENT STATUS
        # ==================================================

        current_status = (
            procurement.get("status")
            or ""
        ).strip()

        # ==================================================
        # ALREADY SUBMITTED / LOCKED
        # ==================================================

        locked_statuses = {
            "Pending Account Review",
            "Account Correction Required",
            "Pending Manager Review",
            "Manager Correction Required",
            "Pending Final Documents",
            "Completed"
        }

        if current_status in locked_statuses:

            flash(
                "This procurement package has already been "
                "submitted or moved beyond the Purchase submission stage.",
                "warning"
            )

            return redirect(
                url_for(
                    "construction_purchase_material_request_procurement",
                    request_id=procurement["material_request_id"]
                )
            )

        # ==================================================
        # EXPECTED PURCHASE STATUS
        #
        # BOTH Card and Quotation now use the same status.
        # ==================================================

        allowed_submission_statuses = {
            "Pending Procurement Documents",
            "Procurement Correction Required"
        }

        if current_status not in allowed_submission_statuses:

            flash(
                "This procurement package is not currently "
                "awaiting Purchase document submission.",
                "warning"
            )

            return redirect(
                url_for(
                    "construction_purchase_material_request_procurement",
                    request_id=procurement["material_request_id"]
                )
            )

        # ==================================================
        # LOAD ALL PURCHASE DOCUMENTS
        # ==================================================

        cursor.execute(
            """
            SELECT
                id,
                procurement_id,
                document_type,
                workflow_stage,
                document_title,
                original_file_name,
                file_url,
                public_id,
                uploaded_by,
                uploaded_by_name,
                uploaded_at,
                notes

            FROM construction_purchase_procurement_documents

            WHERE procurement_id = %s

            ORDER BY
                uploaded_at ASC,
                id ASC
            """,
            (procurement_id,)
        )

        procurement_documents = (
            cursor.fetchall() or []
        )

        # ==================================================
        # VALIDATE REQUIRED DOCUMENTS
        # ==================================================

        uploaded_document_types = {
            (
                document.get("document_type") or ""
            ).strip()
            for document in procurement_documents
        }

        missing_documents = []

        # --------------------------------------------------
        # CARD
        # --------------------------------------------------

        if procurement_method == "Card":

            if "Card Payment Evidence" not in uploaded_document_types:

                missing_documents.append(
                    "Card Payment Evidence"
                )

        # --------------------------------------------------
        # QUOTATION / LPO
        # --------------------------------------------------

        elif procurement_method == "Quotation":

            if "Quotation" not in uploaded_document_types:

                missing_documents.append(
                    "Quotation"
                )

            if "LPO" not in uploaded_document_types:

                missing_documents.append(
                    "LPO"
                )

        # ==================================================
        # MISSING DOCUMENTS
        # ==================================================

        if missing_documents:

            missing_text = ", ".join(
                missing_documents
            )

            flash(
                "The package cannot be submitted yet. "
                f"Missing required document(s): {missing_text}.",
                "danger"
            )

            return redirect(
                url_for(
                    "construction_purchase_material_request_procurement",
                    request_id=procurement["material_request_id"]
                )
            )

        # ==================================================
        # NEW STATUS
        #
        # SAME FOR CARD AND QUOTATION.
        # ==================================================

        new_status = "Pending Account Review"

        # ==================================================
        # ACTION TYPE
        # ==================================================

        if procurement_method == "Card":

            action_type = (
                "CARD_DOCUMENT_PACKAGE_SUBMITTED"
            )

            action_description = (
                "Purchase submitted the complete Card "
                "Procurement document package for "
                f"Material Request "
                f"{procurement['request_number']} "
                "to Account."
            )

        else:

            action_type = (
                "QUOTATION_LPO_PACKAGE_SUBMITTED"
            )

            action_description = (
                "Purchase submitted the complete "
                "Quotation/LPO Procurement document "
                "package for Material Request "
                f"{procurement['request_number']} "
                "to Account."
            )

        # ==================================================
        # UPDATE PROCUREMENT
        # ==================================================

        cursor.execute(
            """
            UPDATE construction_purchase_procurement

            SET
                status = %s,
                workflow_stage = 'Account Review',

                pre_purchase_submitted_by = %s,
                pre_purchase_submitted_by_name = %s,
                pre_purchase_submitted_at = NOW(),

                submitted_to_account_at = NOW(),

                updated_at = NOW()

            WHERE id = %s
            """,
            (
                new_status,
                purchase_user_id,
                purchase_user_name,
                procurement_id
            )
        )

        # ==================================================
        # AUDIT SUBMISSION
        # ==================================================

        _construction_log_procurement_action(
            cursor=cursor,
            procurement_id=procurement_id,
            action_type=action_type,
            action_description=action_description,
            performed_by=purchase_user_id,
            performed_by_name=purchase_user_name,
            from_status=current_status,
            to_status=new_status,
            notes=(
                f"Complete package contained "
                f"{len(procurement_documents)} document(s)."
            )
        )

        # ==================================================
        # COMMIT BEFORE EMAIL
        # ==================================================

        conn.commit()

        # ==================================================
        # LOAD ACCOUNT + PURCHASE RECIPIENTS
        # ==================================================

        cursor.execute(
            """
            SELECT
                role,
                fullname,
                email

            FROM construction_admins

            WHERE LOWER(TRIM(role))
                IN ('account', 'purchase')

              AND email IS NOT NULL
              AND TRIM(email) <> ''

            ORDER BY
                CASE
                    WHEN LOWER(TRIM(role)) = 'account'
                        THEN 1

                    WHEN LOWER(TRIM(role)) = 'purchase'
                        THEN 2

                    ELSE 3
                END,

                id ASC
            """
        )

        recipient_rows = (
            cursor.fetchall() or []
        )

        # ==================================================
        # DEDUPLICATE EMAILS
        # ==================================================

        recipients = []
        seen_emails = set()

        for recipient in recipient_rows:

            email_address = (
                recipient.get("email") or ""
            ).strip()

            if not email_address:
                continue

            email_key = email_address.lower()

            if email_key in seen_emails:
                continue

            seen_emails.add(email_key)

            recipients.append(
                {
                    "role": (
                        recipient.get("role")
                        or ""
                    ).strip(),

                    "fullname": (
                        recipient.get("fullname")
                        or ""
                    ).strip(),

                    "email": email_address
                }
            )

        # ==================================================
        # BUILD EMAIL DOCUMENT TABLE
        # ==================================================

        from html import escape as html_escape

        document_rows_html = ""

        for index, document in enumerate(
            procurement_documents,
            start=1
        ):

            document_type = html_escape(
                str(
                    document.get("document_type")
                    or "Document"
                )
            )

            document_title = html_escape(
                str(
                    document.get("document_title")
                    or document.get("original_file_name")
                    or "Procurement Document"
                )
            )

            original_file_name = html_escape(
                str(
                    document.get("original_file_name")
                    or "—"
                )
            )

            uploaded_by_name = html_escape(
                str(
                    document.get("uploaded_by_name")
                    or "Unknown User"
                )
            )

            notes_value = html_escape(
                str(
                    document.get("notes")
                    or "—"
                )
            )

            uploaded_at = document.get(
                "uploaded_at"
            )

            if uploaded_at:

                uploaded_at_text = (
                    uploaded_at.strftime(
                        "%d %b %Y, %I:%M:%S %p"
                    )
                    + " AST"
                )

            else:

                uploaded_at_text = "—"

            uploaded_at_text = html_escape(
                uploaded_at_text
            )

            file_url = (
                document.get("file_url")
                or ""
            ).strip()

            if file_url:

                safe_file_url = html_escape(
                    file_url,
                    quote=True
                )

                file_link_html = (
                    f'<a href="{safe_file_url}" '
                    f'target="_blank" '
                    f'rel="noopener noreferrer" '
                    f'style="'
                    f'display:inline-block;'
                    f'padding:7px 12px;'
                    f'background:#7b1e2b;'
                    f'color:#ffffff;'
                    f'text-decoration:none;'
                    f'border-radius:5px;'
                    f'font-weight:600;'
                    f'">'
                    f'View Document'
                    f'</a>'
                )

            else:

                file_link_html = "No file link"

            document_rows_html += f"""
                <tr>

                    <td style="
                        padding:10px;
                        border:1px solid #e2e2e2;
                        text-align:center;
                    ">
                        {index}
                    </td>

                    <td style="
                        padding:10px;
                        border:1px solid #e2e2e2;
                    ">
                        <strong>
                            {document_type}
                        </strong>

                        <br>

                        <span style="
                            color:#666666;
                            font-size:13px;
                        ">
                            {document_title}
                        </span>
                    </td>

                    <td style="
                        padding:10px;
                        border:1px solid #e2e2e2;
                    ">
                        {original_file_name}
                    </td>

                    <td style="
                        padding:10px;
                        border:1px solid #e2e2e2;
                    ">
                        {uploaded_by_name}
                    </td>

                    <td style="
                        padding:10px;
                        border:1px solid #e2e2e2;
                        white-space:nowrap;
                    ">
                        {uploaded_at_text}
                    </td>

                    <td style="
                        padding:10px;
                        border:1px solid #e2e2e2;
                    ">
                        {notes_value}
                    </td>

                    <td style="
                        padding:10px;
                        border:1px solid #e2e2e2;
                        text-align:center;
                    ">
                        {file_link_html}
                    </td>

                </tr>
            """

        # ==================================================
        # EMAIL SUBJECT
        # ==================================================

        if procurement_method == "Card":

            email_subject = (
                "Card Procurement Package Submitted - "
                f"{procurement['request_number']}"
            )

        else:

            email_subject = (
                "Quotation & LPO Package Submitted - "
                f"{procurement['request_number']}"
            )

        # ==================================================
        # EMAIL RECIPIENT DISPLAY
        # ==================================================

        recipient_display = ", ".join(
            html_escape(
                recipient["email"]
            )
            for recipient in recipients
        )

        if not recipient_display:

            recipient_display = (
                "No Account/Purchase recipient "
                "email address was found."
            )

        # ==================================================
        # BASIC EMAIL DATA
        # ==================================================

        request_number_html = html_escape(
            str(
                procurement.get("request_number")
                or "Material Request"
            )
        )

        project_name_html = html_escape(
            str(
                procurement.get("project_name")
                or "—"
            )
        )

        requested_by_html = html_escape(
            str(
                procurement.get("requested_by_name")
                or "—"
            )
        )

        supplier_name_html = html_escape(
            str(
                procurement.get("supplier_name")
                or "—"
            )
        )

        supplier_contact_html = html_escape(
            str(
                procurement.get("supplier_contact")
                or "—"
            )
        )

        procurement_method_html = html_escape(
            procurement_method
        )

        currency = (
            procurement.get("currency")
            or "QAR"
        )

        amount = procurement.get(
            "procurement_amount"
        )

        if amount is not None:

            try:

                amount_text = (
                    f"{currency} "
                    f"{float(amount):,.2f}"
                )

            except Exception:

                amount_text = (
                    f"{currency} {amount}"
                )

        else:

            amount_text = "—"

        amount_html = html_escape(
            str(amount_text)
        )

        submitted_by_html = html_escape(
            str(purchase_user_name)
        )

        submitted_at_text = (
            datetime.now().strftime(
                "%d %b %Y, %I:%M:%S %p"
            )
            + " AST"
        )

        submitted_at_html = html_escape(
            submitted_at_text
        )

        # ==================================================
        # EMAIL HTML
        # ==================================================

        email_html = f"""
        <!DOCTYPE html>

        <html>

        <body style="
            margin:0;
            padding:0;
            background:#f4f4f4;
            font-family:Arial,Helvetica,sans-serif;
            color:#333333;
        ">

            <div style="
                max-width:900px;
                margin:30px auto;
                background:#ffffff;
                border:1px solid #dddddd;
                border-radius:8px;
                overflow:hidden;
            ">

                <div style="
                    background:#7b1e2b;
                    color:#ffffff;
                    padding:24px 28px;
                ">

                    <div style="
                        font-size:12px;
                        text-transform:uppercase;
                        letter-spacing:1px;
                        opacity:.85;
                        margin-bottom:7px;
                    ">
                        Prestigious Trading & Construction W.L.L.
                    </div>

                    <div style="
                        font-size:23px;
                        font-weight:700;
                    ">
                        Procurement Package Submitted
                    </div>

                </div>


                <div style="
                    padding:28px;
                ">

                    <p style="
                        margin-top:0;
                        font-size:15px;
                        line-height:1.7;
                    ">
                        Dear Account and Purchase Team,
                    </p>


                    <p style="
                        font-size:15px;
                        line-height:1.7;
                    ">

                        The Purchase Department has officially
                        submitted the complete
                        <strong>
                            {procurement_method_html}
                        </strong>
                        procurement document package to Account.

                    </p>


                    <div style="
                        background:#faf7f7;
                        border:1px solid #eadbdd;
                        border-left:4px solid #7b1e2b;
                        padding:18px;
                        margin:22px 0;
                        border-radius:5px;
                    ">

                        <table style="
                            width:100%;
                            border-collapse:collapse;
                        ">

                            <tr>
                                <td style="
                                    padding:7px 0;
                                    width:190px;
                                    font-weight:700;
                                ">
                                    Material Request
                                </td>

                                <td style="
                                    padding:7px 0;
                                ">
                                    {request_number_html}
                                </td>
                            </tr>


                            <tr>
                                <td style="
                                    padding:7px 0;
                                    font-weight:700;
                                ">
                                    Project
                                </td>

                                <td style="
                                    padding:7px 0;
                                ">
                                    {project_name_html}
                                </td>
                            </tr>


                            <tr>
                                <td style="
                                    padding:7px 0;
                                    font-weight:700;
                                ">
                                    Requested By
                                </td>

                                <td style="
                                    padding:7px 0;
                                ">
                                    {requested_by_html}
                                </td>
                            </tr>


                            <tr>
                                <td style="
                                    padding:7px 0;
                                    font-weight:700;
                                ">
                                    Procurement Method
                                </td>

                                <td style="
                                    padding:7px 0;
                                ">
                                    {procurement_method_html}
                                </td>
                            </tr>


                            <tr>
                                <td style="
                                    padding:7px 0;
                                    font-weight:700;
                                ">
                                    Supplier
                                </td>

                                <td style="
                                    padding:7px 0;
                                ">
                                    {supplier_name_html}
                                </td>
                            </tr>


                            <tr>
                                <td style="
                                    padding:7px 0;
                                    font-weight:700;
                                ">
                                    Supplier Contact
                                </td>

                                <td style="
                                    padding:7px 0;
                                ">
                                    {supplier_contact_html}
                                </td>
                            </tr>


                            <tr>
                                <td style="
                                    padding:7px 0;
                                    font-weight:700;
                                ">
                                    Procurement Amount
                                </td>

                                <td style="
                                    padding:7px 0;
                                ">
                                    {amount_html}
                                </td>
                            </tr>


                            <tr>
                                <td style="
                                    padding:7px 0;
                                    font-weight:700;
                                ">
                                    Package Status
                                </td>

                                <td style="
                                    padding:7px 0;
                                    color:#176b3a;
                                    font-weight:700;
                                ">
                                    Pending Account Review
                                </td>
                            </tr>


                            <tr>
                                <td style="
                                    padding:7px 0;
                                    font-weight:700;
                                ">
                                    Submitted By
                                </td>

                                <td style="
                                    padding:7px 0;
                                ">
                                    {submitted_by_html}
                                </td>
                            </tr>


                            <tr>
                                <td style="
                                    padding:7px 0;
                                    font-weight:700;
                                ">
                                    Submitted At
                                </td>

                                <td style="
                                    padding:7px 0;
                                ">
                                    {submitted_at_html}
                                </td>
                            </tr>

                        </table>

                    </div>


                    <p style="
                        font-size:15px;
                        line-height:1.7;
                    ">

                        The package contains
                        <strong>
                            {len(procurement_documents)}
                        </strong>
                        document(s).

                        All documents uploaded for this procurement
                        at the time of submission are listed below.

                    </p>


                    <h3 style="
                        color:#7b1e2b;
                        margin-top:28px;
                        margin-bottom:12px;
                        font-size:18px;
                    ">
                        Complete Procurement Document Package
                    </h3>


                    <div style="
                        overflow-x:auto;
                    ">

                        <table style="
                            width:100%;
                            min-width:760px;
                            border-collapse:collapse;
                            font-size:13px;
                        ">

                            <thead>

                                <tr style="
                                    background:#7b1e2b;
                                    color:#ffffff;
                                ">

                                    <th style="
                                        padding:10px;
                                        border:1px solid #6a1824;
                                    ">
                                        #
                                    </th>

                                    <th style="
                                        padding:10px;
                                        border:1px solid #6a1824;
                                        text-align:left;
                                    ">
                                        Document
                                    </th>

                                    <th style="
                                        padding:10px;
                                        border:1px solid #6a1824;
                                        text-align:left;
                                    ">
                                        File
                                    </th>

                                    <th style="
                                        padding:10px;
                                        border:1px solid #6a1824;
                                        text-align:left;
                                    ">
                                        Uploaded By
                                    </th>

                                    <th style="
                                        padding:10px;
                                        border:1px solid #6a1824;
                                        text-align:left;
                                    ">
                                        Uploaded At
                                    </th>

                                    <th style="
                                        padding:10px;
                                        border:1px solid #6a1824;
                                        text-align:left;
                                    ">
                                        Notes
                                    </th>

                                    <th style="
                                        padding:10px;
                                        border:1px solid #6a1824;
                                    ">
                                        File
                                    </th>

                                </tr>

                            </thead>

                            <tbody>

                                {document_rows_html}

                            </tbody>

                        </table>

                    </div>


                    <div style="
                        margin-top:25px;
                        padding:16px;
                        background:#f7f7f7;
                        border:1px solid #e1e1e1;
                        border-radius:5px;
                        font-size:13px;
                        line-height:1.6;
                    ">

                        <strong>
                            Workflow:
                        </strong>

                        Purchase has completed the procurement
                        document stage. The package is now awaiting
                        Account review and stamping.

                        <br><br>

                        Document uploads alone do not trigger this
                        notification. This notification is generated
                        only when Purchase submits the complete
                        package to Account.

                    </div>


                    <p style="
                        margin-top:28px;
                        font-size:13px;
                        color:#777777;
                        line-height:1.6;
                    ">

                        This is an automated notification from the
                        Construction Administration System.

                        <br>

                        Recipients:
                        {recipient_display}

                    </p>

                </div>


                <div style="
                    background:#f7f7f7;
                    border-top:1px solid #dddddd;
                    padding:18px 28px;
                    text-align:center;
                    color:#777777;
                    font-size:12px;
                ">

                    Prestigious Trading & Construction W.L.L.
                    &nbsp;•&nbsp;
                    Construction Administration System

                </div>

            </div>

        </body>

        </html>
        """

        # ==================================================
        # SEND EMAIL
        #
        # Database transaction is already committed.
        # Email failure therefore does not undo submission.
        # ==================================================

        if recipients:

            for recipient in recipients:

                recipient_email = recipient["email"]

                try:

                    send_email(
                        recipient_email,
                        email_subject,
                        email_html
                    )

                    print(
                        "PROCUREMENT PACKAGE EMAIL SENT:",
                        recipient_email,
                        procurement_id
                    )

                except Exception as email_error:

                    print(
                        "PROCUREMENT PACKAGE EMAIL ERROR:",
                        recipient_email,
                        type(email_error).__name__,
                        repr(email_error)
                    )

        else:

            print(
                "PROCUREMENT PACKAGE EMAIL WARNING: "
                "No Account/Purchase recipients found."
            )

        # ==================================================
        # FINAL USER MESSAGE
        # ==================================================

        if recipients:

            flash(
                "The complete procurement package has been "
                "submitted to Account successfully. "
                "All uploaded documents were included "
                "in the notification.",
                "success"
            )

        else:

            flash(
                "The complete procurement package was submitted "
                "successfully, but no Account/Purchase notification "
                "recipient was found.",
                "warning"
            )

        return redirect(
            url_for(
                "construction_purchase_material_request_procurement",
                request_id=procurement["material_request_id"]
            )
        )

    except Exception as e:

        if conn:
            try:
                conn.rollback()
            except Exception:
                pass

        print(
            "CONSTRUCTION PROCUREMENT PACKAGE SUBMISSION ERROR:",
            type(e).__name__,
            repr(e)
        )

        import traceback
        traceback.print_exc()

        flash(
            "Unable to submit the complete procurement package.",
            "danger"
        )

        if procurement:

            return redirect(
                url_for(
                    "construction_purchase_material_request_procurement",
                    request_id=procurement["material_request_id"]
                )
            )

        return redirect(
            url_for("construction_purchase_dashboard")
        )

    finally:

        if cursor:
            try:
                cursor.close()
            except Exception:
                pass

        if conn:
            try:
                conn.close()
            except Exception:
                pass












# ==========================================================
# PURCHASE OFFICER - FINAL PROCUREMENT
# ==========================================================

@app.route(
    "/construction/purchase/final-procurement/<int:procurement_id>",
    methods=["GET", "POST"]
)
def construction_purchase_final_procurement(procurement_id):

    # ======================================================
    # LOGIN PROTECTION
    # ======================================================

    if "construction_admin_id" not in session:
        flash(
            "Please sign in to access the Purchase Department.",
            "warning"
        )
        return redirect(url_for("admin_login"))

    # ======================================================
    # ROLE PROTECTION
    # ======================================================

    role = (
        session.get("construction_admin_role") or ""
    ).strip().lower()

    if role != "purchase":
        flash(
            "You are not authorized to access Final Procurement.",
            "danger"
        )
        return redirect(construction_role_dashboard())

    # ======================================================
    # CURRENT PURCHASE USER
    # ======================================================

    purchase_user_id = session.get(
        "construction_admin_id"
    )

    purchase_user_name = (
        session.get("construction_admin_name")
        or session.get("name")
        or session.get("fullname")
        or session.get("username")
        or "Purchase Officer"
    )

    conn = None
    cursor = None
    procurement = None

    try:

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        # ==================================================
        # LOAD PROCUREMENT + MATERIAL REQUEST
        # ==================================================

        cursor.execute(
            """
            SELECT
                p.*,

                mr.id AS material_request_id,
                mr.request_number,
                mr.project_name,
                mr.requested_by,
                mr.requested_by_name,
                mr.request_date,
                mr.description AS material_request_description,
                mr.status AS material_request_status

            FROM construction_purchase_procurement p

            INNER JOIN construction_purchase_material_requests mr
                ON mr.id = p.material_request_id

            WHERE p.id = %s

            LIMIT 1
            """,
            (procurement_id,)
        )

        procurement = cursor.fetchone()

        if not procurement:

            flash(
                "Procurement record was not found.",
                "danger"
            )

            return redirect(
                url_for("construction_purchase_dashboard")
            )

        # ==================================================
        # CURRENT STATUS
        # ==================================================

        procurement_status = (
            procurement.get("status") or ""
        ).strip()

        allowed_final_statuses = {
            "Pending Final Procurement",
            "Final Procurement Correction Required",
            "Completed"
        }

        if procurement_status not in allowed_final_statuses:

            flash(
                "This procurement is not currently at the "
                "Final Procurement stage.",
                "warning"
            )

            return redirect(
                url_for("construction_purchase_dashboard")
            )

        # ==================================================
        # MATERIAL REQUEST STATUS
        # ==================================================

        material_request_status = (
            procurement.get("material_request_status")
            or ""
        ).strip()

        # ==================================================
        # LOAD PROCUREMENT DOCUMENTS
        # ==================================================

        cursor.execute(
            """
            SELECT
                id,
                procurement_id,
                document_type,
                workflow_stage,
                document_title,
                original_file_name,
                file_url,
                public_id,
                uploaded_by,
                uploaded_by_name,
                uploaded_at,
                notes

            FROM construction_purchase_procurement_documents

            WHERE procurement_id = %s

            ORDER BY
                uploaded_at DESC,
                id DESC
            """,
            (procurement_id,)
        )

        procurement_documents = cursor.fetchall() or []

        # ==================================================
        # LOAD PROCUREMENT AUDIT TRAIL
        # ==================================================

        cursor.execute(
            """
            SELECT
                id,
                procurement_id,
                action_type,
                action_description,
                from_status,
                to_status,
                performed_by,
                performed_by_name,
                performed_at,
                notes

            FROM construction_purchase_procurement_actions

            WHERE procurement_id = %s

            ORDER BY
                performed_at DESC,
                id DESC
            """,
            (procurement_id,)
        )

        procurement_actions = cursor.fetchall() or []

        # ==================================================
        # GET LATEST DOCUMENTS BY TYPE
        #
        # This lets the final page display the most recent
        # quotation, LPO, invoice, receipt and payment
        # evidence while keeping the complete history.
        # ==================================================

        latest_documents = {
            "quotation": None,
            "lpo": None,
            "invoice": None,
            "receipt": None,
            "payment_evidence": None
        }

        for document in procurement_documents:

            document_type = (
                document.get("document_type") or ""
            ).strip().lower()

            if document_type == "quotation":

                if latest_documents["quotation"] is None:
                    latest_documents["quotation"] = document

            elif document_type == "lpo":

                if latest_documents["lpo"] is None:
                    latest_documents["lpo"] = document

            elif document_type == "invoice":

                if latest_documents["invoice"] is None:
                    latest_documents["invoice"] = document

            elif document_type == "receipt":

                if latest_documents["receipt"] is None:
                    latest_documents["receipt"] = document

            elif document_type in {
                "payment evidence",
                "final payment evidence",
                "card payment evidence"
            }:

                if latest_documents["payment_evidence"] is None:
                    latest_documents["payment_evidence"] = document

        # ==================================================
        # GET REQUEST NUMBER
        # ==================================================

        request_number = (
            procurement.get("request_number")
            or f"MR-{procurement['material_request_id']}"
        )

        # ==================================================
        # POST ACTION
        # ==================================================

        if request.method == "POST":

            action = (
                request.form.get("action") or ""
            ).strip()

            # ==================================================
            # START FINAL PROCUREMENT
            # ==================================================

            if action == "start_final_procurement":

                if procurement_status not in {
                    "Pending Final Procurement",
                    "Final Procurement Correction Required"
                }:

                    flash(
                        "Final Procurement cannot be started "
                        "from the current status.",
                        "warning"
                    )

                    return redirect(
                        url_for(
                            "construction_purchase_final_procurement",
                            procurement_id=procurement_id
                        )
                    )

                # ----------------------------------------------
                # Do not overwrite original start information
                # ----------------------------------------------

                if not procurement.get(
                    "final_purchase_started_at"
                ):

                    cursor.execute(
                        """
                        UPDATE construction_purchase_procurement

                        SET
                            final_purchase_started_by = %s,
                            final_purchase_started_by_name = %s,
                            final_purchase_started_at = NOW(),
                            workflow_stage = 'Final Procurement',
                            updated_at = NOW()

                        WHERE id = %s
                        """,
                        (
                            purchase_user_id,
                            purchase_user_name,
                            procurement_id
                        )
                    )

                    _construction_log_procurement_action(
                        cursor=cursor,
                        procurement_id=procurement_id,
                        action_type="FINAL_PROCUREMENT_STARTED",
                        action_description=(
                            "Purchase started Final Procurement "
                            f"for Material Request {request_number}."
                        ),
                        performed_by=purchase_user_id,
                        performed_by_name=purchase_user_name,
                        from_status=procurement_status,
                        to_status=procurement_status,
                        notes=None
                    )

                    conn.commit()

                    flash(
                        "Final Procurement started successfully.",
                        "success"
                    )

                else:

                    flash(
                        "Final Procurement has already been started.",
                        "info"
                    )

                return redirect(
                    url_for(
                        "construction_purchase_final_procurement",
                        procurement_id=procurement_id
                    )
                )

            # ==================================================
            # SUBMIT FINAL PROCUREMENT
            # ==================================================

            elif action == "submit_final_procurement":

                # ----------------------------------------------
                # Completed records cannot be submitted again
                # ----------------------------------------------

                if procurement_status == "Completed":

                    flash(
                        "This procurement has already been completed.",
                        "info"
                    )

                    return redirect(
                        url_for(
                            "construction_purchase_final_procurement",
                            procurement_id=procurement_id
                        )
                    )

                # ----------------------------------------------
                # Validate status
                # ----------------------------------------------

                if procurement_status not in {
                    "Pending Final Procurement",
                    "Final Procurement Correction Required"
                }:

                    flash(
                        "This procurement is not available for "
                        "Final Procurement submission.",
                        "danger"
                    )

                    return redirect(
                        url_for("construction_purchase_dashboard")
                    )

                # ----------------------------------------------
                # Final Procurement must have been started
                # ----------------------------------------------

                if not procurement.get(
                    "final_purchase_started_at"
                ):

                    flash(
                        "Please start Final Procurement before "
                        "submitting the completed purchase.",
                        "warning"
                    )

                    return redirect(
                        url_for(
                            "construction_purchase_final_procurement",
                            procurement_id=procurement_id
                        )
                    )

                # ----------------------------------------------
                # PAYMENT METHOD
                # ----------------------------------------------

                final_payment_method = (
                    request.form.get("final_payment_method")
                    or ""
                ).strip()

                if final_payment_method not in {
                    "Card",
                    "Cheque"
                }:

                    flash(
                        "Please select either Card or Cheque "
                        "as the final payment method.",
                        "danger"
                    )

                    return redirect(
                        url_for(
                            "construction_purchase_final_procurement",
                            procurement_id=procurement_id
                        )
                    )

                # ----------------------------------------------
                # PROCUREMENT AMOUNT
                # ----------------------------------------------

                procurement_amount = _construction_parse_amount(
                    request.form.get("procurement_amount")
                )

                if procurement_amount is None:

                    flash(
                        "Please enter a valid final procurement amount.",
                        "danger"
                    )

                    return redirect(
                        url_for(
                            "construction_purchase_final_procurement",
                            procurement_id=procurement_id
                        )
                    )

                # ----------------------------------------------
                # CURRENCY
                # ----------------------------------------------

                currency = (
                    request.form.get("currency")
                    or procurement.get("currency")
                    or "QAR"
                ).strip().upper()

                if currency not in {
                    "QAR",
                    "USD",
                    "EUR",
                    "GBP"
                }:

                    currency = "QAR"

                # ----------------------------------------------
                # PAYMENT DATE
                # ----------------------------------------------

                payment_date = (
                    request.form.get("payment_date")
                    or ""
                ).strip()

                if not payment_date:

                    flash(
                        "Please enter the final payment date.",
                        "danger"
                    )

                    return redirect(
                        url_for(
                            "construction_purchase_final_procurement",
                            procurement_id=procurement_id
                        )
                    )

                # ==================================================
                # CARD PAYMENT
                # ==================================================

                final_card_payment_reference = None
                final_card_paid_at = None
                final_card_paid_by = None
                final_card_paid_by_name = None

                # ==================================================
                # CHEQUE PAYMENT
                # ==================================================

                cheque_required = 0
                cheque_number = None
                cheque_date = None
                cheque_bank = None

                if final_payment_method == "Card":

                    final_card_payment_reference = (
                        request.form.get(
                            "final_card_payment_reference"
                        )
                        or ""
                    ).strip()

                    if not final_card_payment_reference:

                        flash(
                            "Please enter the Card payment reference.",
                            "danger"
                        )

                        return redirect(
                            url_for(
                                "construction_purchase_final_procurement",
                                procurement_id=procurement_id
                            )
                        )

                    final_card_paid_by = purchase_user_id
                    final_card_paid_by_name = purchase_user_name

                    # ------------------------------------------
                    # datetime-local input
                    # ------------------------------------------

                    final_card_paid_at = (
                        request.form.get(
                            "final_card_paid_at"
                        )
                        or ""
                    ).strip()

                    if not final_card_paid_at:
                        final_card_paid_at = payment_date

                # ==================================================
                # CHEQUE PAYMENT
                # ==================================================

                elif final_payment_method == "Cheque":

                    cheque_required = 1

                    cheque_number = (
                        request.form.get("cheque_number")
                        or ""
                    ).strip()

                    cheque_date = (
                        request.form.get("cheque_date")
                        or ""
                    ).strip()

                    cheque_bank = (
                        request.form.get("cheque_bank")
                        or ""
                    ).strip()

                    if not cheque_number:

                        flash(
                            "Please enter the cheque number.",
                            "danger"
                        )

                        return redirect(
                            url_for(
                                "construction_purchase_final_procurement",
                                procurement_id=procurement_id
                            )
                        )

                    if not cheque_date:

                        flash(
                            "Please enter the cheque date.",
                            "danger"
                        )

                        return redirect(
                            url_for(
                                "construction_purchase_final_procurement",
                                procurement_id=procurement_id
                            )
                        )

                    if not cheque_bank:

                        flash(
                            "Please enter the cheque bank.",
                            "danger"
                        )

                        return redirect(
                            url_for(
                                "construction_purchase_final_procurement",
                                procurement_id=procurement_id
                            )
                        )

                # ==================================================
                # FINAL DOCUMENTS
                #
                # All three final documents are required:
                # 1. Final Invoice
                # 2. Final Receipt
                # 3. Payment Evidence
                #
                # On correction, the Purchase Officer can upload
                # corrected versions. Existing history is preserved.
                # ==================================================

                final_invoice_file = request.files.get(
                    "final_invoice_document"
                )

                final_receipt_file = request.files.get(
                    "final_receipt_document"
                )

                final_payment_evidence_file = request.files.get(
                    "final_payment_evidence_document"
                )

                # ----------------------------------------------
                # Determine whether each document already exists
                # ----------------------------------------------

                existing_invoice = latest_documents.get(
                    "invoice"
                )

                existing_receipt = latest_documents.get(
                    "receipt"
                )

                existing_payment_evidence = latest_documents.get(
                    "payment_evidence"
                )

                # ----------------------------------------------
                # Invoice required if no previous invoice exists
                # ----------------------------------------------

                if (
                    (
                        not final_invoice_file
                        or not final_invoice_file.filename
                    )
                    and not existing_invoice
                ):

                    flash(
                        "Please upload the Final Invoice.",
                        "danger"
                    )

                    return redirect(
                        url_for(
                            "construction_purchase_final_procurement",
                            procurement_id=procurement_id
                        )
                    )

                # ----------------------------------------------
                # Receipt required if no previous receipt exists
                # ----------------------------------------------

                if (
                    (
                        not final_receipt_file
                        or not final_receipt_file.filename
                    )
                    and not existing_receipt
                ):

                    flash(
                        "Please upload the Final Receipt.",
                        "danger"
                    )

                    return redirect(
                        url_for(
                            "construction_purchase_final_procurement",
                            procurement_id=procurement_id
                        )
                    )

                # ----------------------------------------------
                # Payment evidence required if no previous one
                # ----------------------------------------------

                if (
                    (
                        not final_payment_evidence_file
                        or not final_payment_evidence_file.filename
                    )
                    and not existing_payment_evidence
                ):

                    flash(
                        "Please upload the Final Payment Evidence.",
                        "danger"
                    )

                    return redirect(
                        url_for(
                            "construction_purchase_final_procurement",
                            procurement_id=procurement_id
                        )
                    )

                # ==================================================
                # DOCUMENT UPLOAD HELPER
                # ==================================================

                def upload_final_document(
                    uploaded_file,
                    document_type,
                    document_title
                ):

                    if (
                        not uploaded_file
                        or not uploaded_file.filename
                    ):
                        return None

                    original_filename = secure_filename(
                        uploaded_file.filename
                    )

                    if not original_filename:
                        raise ValueError(
                            f"Invalid file name for {document_title}."
                        )

                    extension = (
                        original_filename.rsplit(".", 1)[-1].lower()
                        if "." in original_filename
                        else ""
                    )

                    allowed_extensions = {
                        "pdf",
                        "jpg",
                        "jpeg",
                        "png",
                        "webp",
                        "gif",
                        "bmp",
                        "tif",
                        "tiff",
                        "doc",
                        "docx",
                        "xls",
                        "xlsx",
                        "csv"
                    }

                    if extension not in allowed_extensions:

                        raise ValueError(
                            f"Unsupported file type for "
                            f"{document_title}."
                        )

                    # ------------------------------------------
                    # Safe request number
                    # ------------------------------------------

                    safe_request_number = "".join(
                        char
                        if char.isalnum() or char in "-_"
                        else "_"
                        for char in str(request_number)
                    )

                    # ------------------------------------------
                    # Same Cloudinary folder structure
                    # ------------------------------------------

                    cloudinary_folder = (
                        "prestigious_construction/"
                        "purchase/procurement/"
                        f"{safe_request_number}/"
                        f"procurement_{procurement_id}"
                    )

                    # ------------------------------------------
                    # Unique public ID
                    # ------------------------------------------

                    document_uuid = str(uuid.uuid4())

                    safe_document_type = "".join(
                        char
                        if char.isalnum() or char in "-_"
                        else "_"
                        for char in document_type.lower()
                    )

                    public_id = (
                        f"{safe_document_type}_{document_uuid}"
                    )

                    # ------------------------------------------
                    # Cloudinary upload
                    # ------------------------------------------

                    uploaded_file.seek(0)

                    upload_result = cloudinary.uploader.upload(
                        uploaded_file,
                        resource_type="auto",
                        folder=cloudinary_folder,
                        public_id=public_id,
                        format=extension,
                        overwrite=False
                    )

                    file_url = upload_result.get(
                        "secure_url"
                    )

                    cloudinary_public_id = upload_result.get(
                        "public_id"
                    )

                    if not file_url:
                        raise RuntimeError(
                            f"Cloudinary did not return a secure "
                            f"URL for {document_title}."
                        )

                    if not cloudinary_public_id:
                        raise RuntimeError(
                            f"Cloudinary did not return a public ID "
                            f"for {document_title}."
                        )

                    # ------------------------------------------
                    # Save document record
                    # ------------------------------------------

                    cursor.execute(
                        """
                        INSERT INTO
                            construction_purchase_procurement_documents
                        (
                            procurement_id,
                            document_type,
                            workflow_stage,
                            document_title,
                            original_file_name,
                            file_url,
                            public_id,
                            uploaded_by,
                            uploaded_by_name,
                            uploaded_at,
                            notes
                        )
                        VALUES
                        (
                            %s,
                            %s,
                            %s,
                            %s,
                            %s,
                            %s,
                            %s,
                            %s,
                            %s,
                            NOW(),
                            %s
                        )
                        """,
                        (
                            procurement_id,
                            document_type,
                            "Final Procurement",
                            document_title,
                            original_filename,
                            file_url,
                            cloudinary_public_id,
                            purchase_user_id,
                            purchase_user_name,
                            None
                        )
                    )

                    # ------------------------------------------
                    # Audit document upload
                    # ------------------------------------------

                    _construction_log_procurement_action(
                        cursor=cursor,
                        procurement_id=procurement_id,
                        action_type="FINAL_DOCUMENT_UPLOADED",
                        action_description=(
                            f"Purchase uploaded "
                            f"{document_title} for "
                            f"Material Request {request_number}."
                        ),
                        performed_by=purchase_user_id,
                        performed_by_name=purchase_user_name,
                        from_status=procurement_status,
                        to_status=procurement_status,
                        notes=None
                    )

                    return {
                        "file_url": file_url,
                        "public_id": cloudinary_public_id,
                        "original_file_name": original_filename
                    }

                # ==================================================
                # UPLOAD NEW FINAL DOCUMENTS
                # ==================================================

                if (
                    final_invoice_file
                    and final_invoice_file.filename
                ):

                    upload_final_document(
                        final_invoice_file,
                        "Invoice",
                        "Final Invoice"
                    )

                if (
                    final_receipt_file
                    and final_receipt_file.filename
                ):

                    upload_final_document(
                        final_receipt_file,
                        "Receipt",
                        "Final Receipt"
                    )

                if (
                    final_payment_evidence_file
                    and final_payment_evidence_file.filename
                ):

                    upload_final_document(
                        final_payment_evidence_file,
                        "Payment Evidence",
                        "Final Payment Evidence"
                    )

                # ==================================================
                # FINAL NOTES
                # ==================================================

                final_notes = (
                    request.form.get("notes")
                    or ""
                ).strip()

                # ==================================================
                # UPDATE PROCUREMENT
                #
                # IMPORTANT:
                # Account cheque issuer information is NOT touched.
                # ==================================================

                cursor.execute(
                    """
                    UPDATE construction_purchase_procurement

                    SET
                        procurement_amount = %s,
                        currency = %s,
                        payment_date = %s,

                        final_payment_method = %s,

                        final_card_paid_by = %s,
                        final_card_paid_by_name = %s,
                        final_card_paid_at = %s,
                        final_card_payment_reference = %s,

                        cheque_required = %s,
                        cheque_number = %s,
                        cheque_date = %s,
                        cheque_bank = %s,

                        final_documents_submitted_by = %s,
                        final_documents_submitted_by_name = %s,
                        final_documents_submitted_at = NOW(),

                        completed_by = %s,
                        completed_by_name = %s,
                        completed_at = NOW(),

                        workflow_stage = 'Completion',
                        status = 'Completed',

                        notes = %s,
                        updated_at = NOW()

                    WHERE id = %s
                    """,
                    (
                        procurement_amount,
                        currency,
                        payment_date,

                        final_payment_method,

                        final_card_paid_by,
                        final_card_paid_by_name,
                        final_card_paid_at,
                        final_card_payment_reference,

                        cheque_required,
                        cheque_number,
                        cheque_date,
                        cheque_bank,

                        purchase_user_id,
                        purchase_user_name,

                        purchase_user_id,
                        purchase_user_name,

                        final_notes or procurement.get("notes"),

                        procurement_id
                    )
                )

                # ==================================================
                # COMPLETE MATERIAL REQUEST
                # ==================================================

                cursor.execute(
                    """
                    UPDATE construction_purchase_material_requests

                    SET
                        status = 'Completed'

                    WHERE id = %s
                    """,
                    (
                        procurement["material_request_id"],
                    )
                )

                # ==================================================
                # AUDIT - PROCUREMENT COMPLETED
                # ==================================================

                _construction_log_procurement_action(
                    cursor=cursor,
                    procurement_id=procurement_id,
                    action_type="PROCUREMENT_COMPLETED",
                    action_description=(
                        "Purchase completed Final Procurement "
                        f"for Material Request {request_number}. "
                        f"Payment method: {final_payment_method}. "
                        f"Amount: {procurement_amount} {currency}."
                    ),
                    performed_by=purchase_user_id,
                    performed_by_name=purchase_user_name,
                    from_status=procurement_status,
                    to_status="Completed",
                    notes=final_notes or None
                )

                # ==================================================
                # COMMIT EVERYTHING
                # ==================================================

                conn.commit()

                # ==================================================
                # SUCCESS
                # ==================================================

                flash(
                    "Final Procurement completed successfully. "
                    "The Material Request has been marked Completed.",
                    "success"
                )

                return redirect(
                    url_for(
                        "construction_purchase_dashboard"
                    )
                )

            # ==================================================
            # UNKNOWN ACTION
            # ==================================================

            else:

                flash(
                    "Invalid Final Procurement action.",
                    "danger"
                )

                return redirect(
                    url_for(
                        "construction_purchase_final_procurement",
                        procurement_id=procurement_id
                    )
                )

        # ======================================================
        # GET / RENDER PAGE
        # ======================================================

        return render_template(
            "construction_admin/construction_purchase_final_procurement.html",
            procurement=procurement,
            procurement_documents=procurement_documents,
            procurement_actions=procurement_actions,
            latest_documents=latest_documents,
            current_user_name=purchase_user_name,
            current_user_id=purchase_user_id,
            material_request_status=material_request_status
        )

    # ==========================================================
    # ERROR HANDLING
    # ==========================================================

    except Exception as e:

        if conn:
            try:
                conn.rollback()
            except Exception:
                pass

        print(
            "CONSTRUCTION FINAL PROCUREMENT ERROR:",
            type(e).__name__,
            repr(e)
        )

        import traceback
        traceback.print_exc()

        flash(
            "Unable to process Final Procurement.",
            "danger"
        )

        return redirect(
            url_for(
                "construction_purchase_dashboard"
            )
        )

    # ==========================================================
    # CLOSE DATABASE
    # ==========================================================

    finally:

        if cursor:
            try:
                cursor.close()
            except Exception:
                pass

        if conn:
            try:
                conn.close()
            except Exception:
                pass




















# =========================================================
# CONSTRUCTION ENGINEER DASHBOARD
# =========================================================
@app.route("/construction/engineer/dashboard")
def construction_engineer_dashboard():

    # -----------------------------------------------------
    # LOGIN PROTECTION
    # -----------------------------------------------------

    if "construction_admin_id" not in session:
        return redirect(url_for("admin_login"))

    # -----------------------------------------------------
    # ROLE PROTECTION
    # -----------------------------------------------------

    role = (
        session.get("construction_admin_role") or ""
    ).strip().lower()

    if role != "engineer":

        flash(
            "You are not authorized to access the Engineer dashboard.",
            "danger"
        )

        return redirect(construction_role_dashboard())

    conn = None
    cursor = None

    material_requests = []

    # -----------------------------------------------------
    # DASHBOARD COUNTS
    # -----------------------------------------------------

    total_requests = 0
    pending_engineer_review = 0
    correction_required = 0
    pending_manager_approval = 0
    manager_approved = 0
    declined_requests = 0
    completed_requests = 0

    try:

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        # =================================================
        # SUMMARY COUNTS
        # =================================================

        cursor.execute("""
            SELECT

                COUNT(*) AS total_requests,

                SUM(
                    CASE
                        WHEN status = 'Pending Engineer Review'
                        THEN 1
                        ELSE 0
                    END
                ) AS pending_engineer_review,

                SUM(
                    CASE
                        WHEN status = 'Engineer Correction Required'
                        THEN 1
                        ELSE 0
                    END
                ) AS correction_required,

                SUM(
                    CASE
                        WHEN status = 'Pending Manager Approval'
                        THEN 1
                        ELSE 0
                    END
                ) AS pending_manager_approval,

                SUM(
                    CASE
                        WHEN status = 'Approved'
                        THEN 1
                        ELSE 0
                    END
                ) AS manager_approved,

                SUM(
                    CASE
                        WHEN status = 'Declined'
                        THEN 1
                        ELSE 0
                    END
                ) AS declined_requests,

                SUM(
                    CASE
                        WHEN status = 'Completed'
                        THEN 1
                        ELSE 0
                    END
                ) AS completed_requests

            FROM construction_purchase_material_requests
        """)

        counts = cursor.fetchone() or {}

        total_requests = (
            counts.get("total_requests") or 0
        )

        pending_engineer_review = (
            counts.get("pending_engineer_review") or 0
        )

        correction_required = (
            counts.get("correction_required") or 0
        )

        pending_manager_approval = (
            counts.get("pending_manager_approval") or 0
        )

        manager_approved = (
            counts.get("manager_approved") or 0
        )

        declined_requests = (
            counts.get("declined_requests") or 0
        )

        completed_requests = (
            counts.get("completed_requests") or 0
        )

        # =================================================
        # ALL MATERIAL REQUESTS
        #
        # IMPORTANT:
        # ALWAYS USE THE LATEST SIGNED DOCUMENT.
        #
        # NEVER USE original_file_url HERE.
        # =================================================

        cursor.execute("""
            SELECT

                id,
                request_number,

                project_name,

                requested_by,
                requested_by_name,

                request_date,

                description,

                original_file_name,

                signed_file_name,
                signed_file_url,
                signed_public_id,

                status,

                engineer_id,
                engineer_name,
                engineer_reviewed_at,
                engineer_comment,

                manager_id,
                manager_name,
                manager_approved_at,
                manager_comment,

                procurement_method,

                created_at,
                updated_at

            FROM construction_purchase_material_requests

            ORDER BY

                CASE

                    WHEN status = 'Pending Engineer Review'
                    THEN 1

                    WHEN status = 'Engineer Correction Required'
                    THEN 2

                    WHEN status = 'Pending Manager Approval'
                    THEN 3

                    WHEN status = 'Approved'
                    THEN 4

                    WHEN status = 'Declined'
                    THEN 5

                    WHEN status = 'Completed'
                    THEN 6

                    ELSE 7

                END,

                created_at DESC,
                id DESC
        """)

        material_requests = cursor.fetchall() or []

    except Exception as e:

        print(
            "================================================="
        )

        print(
            "ENGINEER DASHBOARD ERROR"
        )

        print(
            type(e).__name__
        )

        print(
            repr(e)
        )

        print(
            "================================================="
        )

        flash(
            "Unable to load the Engineer dashboard.",
            "danger"
        )

    finally:

        if cursor:

            try:
                cursor.close()
            except Exception:
                pass

        if conn:

            try:
                conn.close()
            except Exception:
                pass

    # =====================================================
    # RENDER ENGINEER DASHBOARD
    # =====================================================

    return render_template(
        "construction_admin/"
        "construction_engineer_dashboard.html",

        material_requests=material_requests,

        total_requests=total_requests,

        pending_engineer_review=(
            pending_engineer_review
        ),

        correction_required=(
            correction_required
        ),

        pending_manager_approval=(
            pending_manager_approval
        ),

        manager_approved=(
            manager_approved
        ),

        declined_requests=(
            declined_requests
        ),

        completed_requests=(
            completed_requests
        )
    )







# ==========================================================
# ENGINEER - REVIEW MATERIAL REQUEST
# ==========================================================

@app.route(
    "/construction/engineer/material-request/<int:request_id>/review",
    methods=["GET", "POST"]
)
def construction_engineer_material_request_review(request_id):

    # ======================================================
    # LOGIN PROTECTION
    # ======================================================

    if "construction_admin_id" not in session:

        flash(
            "Please sign in to access Engineer Review.",
            "warning"
        )

        return redirect(
            url_for("admin_login")
        )

    # ======================================================
    # ROLE PROTECTION
    # ======================================================

    role = (
        session.get("construction_admin_role") or ""
    ).strip().lower()

    if role != "engineer":

        flash(
            "You are not authorized to access Engineer Review.",
            "danger"
        )

        return redirect(
            construction_role_dashboard()
        )

    # ======================================================
    # ENGINEER INFORMATION
    # ======================================================

    engineer_id = session.get(
        "construction_admin_id"
    )

    engineer_name = session.get(
        "construction_admin_name",
        "Engineer"
    )

    conn = None
    cursor = None

    try:

        # ==================================================
        # DATABASE CONNECTION
        # ==================================================

        conn = get_db_connection()

        cursor = conn.cursor(
            dictionary=True
        )

        # ==================================================
        # GET MATERIAL REQUEST
        # ==================================================

        cursor.execute(
            """
            SELECT
                id,
                request_number,
                project_name,
                requested_by,
                requested_by_name,
                request_date,
                description,

                original_file_name,
                original_file_url,
                original_public_id,

                signed_file_name,
                signed_file_url,
                signed_public_id,

                purchase_signature_data,
                purchase_signed_at,

                status,

                engineer_id,
                engineer_name,
                engineer_reviewed_at,
                engineer_comment,

                manager_id,
                manager_name,
                manager_approved_at,
                manager_comment,

                procurement_method,

                created_at,
                updated_at

            FROM
                construction_purchase_material_requests

            WHERE
                id = %s

            LIMIT 1
            """,
            (
                request_id,
            )
        )

        material_request = cursor.fetchone()

        # ==================================================
        # REQUEST NOT FOUND
        # ==================================================

        if not material_request:

            flash(
                "Material Request was not found.",
                "danger"
            )

            return redirect(
                url_for(
                    "construction_engineer_dashboard"
                )
            )

        # ==================================================
        # ALLOWED STATUSES
        # ==================================================

        allowed_statuses = [
            "Pending Engineer Review",
            "Engineer Correction Required"
        ]

        if material_request["status"] not in allowed_statuses:

            flash(
                "This Material Request has already been "
                "processed by the Engineer.",
                "warning"
            )

            return redirect(
                url_for(
                    "construction_engineer_dashboard"
                )
            )

        # ==================================================
        # POST REQUEST
        # ==================================================

        if request.method == "POST":

            # ==================================================
            # FORM DATA
            # ==================================================

            action = (
                request.form.get("action") or ""
            ).strip()

            comment = (
                request.form.get("comment") or ""
            ).strip()

            signature_data = (
                request.form.get("signature_data") or ""
            ).strip()

            # ==================================================
            # DEBUG
            # ==================================================

            print("=" * 80)
            print("ENGINEER REVIEW POST DEBUG")
            print("REQUEST ID:", request_id)

            print(
                "FORM DATA:",
                {
                    "action": action,
                    "comment": comment,
                    "signature_data":
                        "[PRESENT]"
                        if signature_data
                        else "[EMPTY]"
                }
            )

            print(
                "ACTION:",
                repr(action)
            )

            print(
                "COMMENT:",
                repr(comment)
            )

            print(
                "SIGNATURE LENGTH:",
                len(signature_data)
            )

            print("=" * 80)

            # ==================================================
            # ACTION VALIDATION
            # ==================================================

            if action not in [
                "Approved",
                "Correction Required"
            ]:

                flash(
                    "Invalid Engineer action.",
                    "danger"
                )

                return redirect(
                    url_for(
                        "construction_engineer_material_request_review",
                        request_id=request_id
                    )
                )

            # ==================================================
            # SIGNATURE VALIDATION
            # ==================================================

            if not signature_data:

                flash(
                    "Please provide your signature before "
                    "submitting the review.",
                    "danger"
                )

                return redirect(
                    url_for(
                        "construction_engineer_material_request_review",
                        request_id=request_id
                    )
                )

            # ==================================================
            # CORRECTION COMMENT VALIDATION
            # ==================================================

            if (
                action == "Correction Required"
                and not comment
            ):

                flash(
                    "Please enter a correction comment.",
                    "danger"
                )

                return redirect(
                    url_for(
                        "construction_engineer_material_request_review",
                        request_id=request_id
                    )
                )

            # ==================================================
            # NEW STATUS
            # ==================================================

            if action == "Approved":

                new_status = (
                    "Pending Manager Approval"
                )

            else:

                new_status = (
                    "Engineer Correction Required"
                )

            # ==================================================
            # QATAR LOCAL TIME
            # ==================================================

            reviewed_at = get_qatar_now()

            # ==================================================
            # LOCK MATERIAL REQUEST
            #
            # This prevents two Engineer submissions from
            # processing the same request simultaneously.
            # ==================================================

            cursor.execute(
                """
                SELECT
                    id,
                    request_number,
                    project_name,
                    requested_by,
                    requested_by_name,
                    request_date,
                    description,
                    status,

                    signed_file_url,
                    signed_file_name,
                    signed_public_id

                FROM
                    construction_purchase_material_requests

                WHERE
                    id = %s

                FOR UPDATE
                """,
                (
                    request_id,
                )
            )

            locked_request = cursor.fetchone()

            # ==================================================
            # REQUEST DISAPPEARED
            # ==================================================

            if not locked_request:

                conn.rollback()

                flash(
                    "Material Request no longer exists.",
                    "danger"
                )

                return redirect(
                    url_for(
                        "construction_engineer_dashboard"
                    )
                )

            # ==================================================
            # STATUS CHECK AGAIN
            # ==================================================

            if locked_request["status"] not in allowed_statuses:

                conn.rollback()

                flash(
                    "This Material Request has already been processed.",
                    "warning"
                )

                return redirect(
                    url_for(
                        "construction_engineer_dashboard"
                    )
                )

            # ==================================================
            # DEFAULT SIGNED FILE VALUES
            #
            # Correction Required does NOT create another PDF.
            #
            # Therefore the current signed document remains
            # unchanged.
            # ==================================================

            new_signed_file_url = (
                locked_request["signed_file_url"]
            )

            new_signed_public_id = (
                locked_request["signed_public_id"]
            )

            new_signed_file_name = (
                locked_request["signed_file_name"]
            )

            # ==================================================
            # ENGINEER APPROVAL
            # ==================================================

            if action == "Approved":

                # ==================================================
                # IMPORTANT DOCUMENT CHAIN
                #
                # NEVER use original_file_url here.
                #
                # Engineer MUST continue from the latest
                # signed PDF.
                #
                # Purchase-stamped PDF
                #          ↓
                # Engineering stamp
                #          ↓
                # Manager stamp later
                # ==================================================

                existing_pdf_url = (
                    locked_request["signed_file_url"]
                )

                if not existing_pdf_url:

                    conn.rollback()

                    flash(
                        "The previously signed Material Request "
                        "PDF could not be found.",
                        "danger"
                    )

                    return redirect(
                        url_for(
                            "construction_engineer_material_request_review",
                            request_id=request_id
                        )
                    )

                print("=" * 80)
                print("ENGINEER DOCUMENT CHAIN")
                print(
                    "SOURCE SIGNED URL:",
                    existing_pdf_url
                )
                print("=" * 80)

                # ==================================================
                # DOWNLOAD CURRENT SIGNED PDF
                # ==================================================

                try:

                    pdf_response = requests.get(
                        existing_pdf_url,
                        timeout=60
                    )

                    pdf_response.raise_for_status()

                    existing_pdf_bytes = (
                        pdf_response.content
                    )

                    if not existing_pdf_bytes:

                        raise ValueError(
                            "Downloaded PDF is empty."
                        )

                    # ==============================================
                    # BASIC PDF VALIDATION
                    # ==============================================

                    if not existing_pdf_bytes.startswith(
                        b"%PDF"
                    ):

                        raise ValueError(
                            "The downloaded signed document "
                            "is not a valid PDF."
                        )

                    print(
                        "CURRENT SIGNED PDF SIZE:",
                        len(existing_pdf_bytes),
                        "bytes"
                    )

                except Exception as pdf_download_error:

                    conn.rollback()

                    print("=" * 80)
                    print(
                        "ENGINEER PDF DOWNLOAD ERROR"
                    )

                    print(
                        "ERROR TYPE:",
                        type(
                            pdf_download_error
                        ).__name__
                    )

                    print(
                        "ERROR:",
                        str(
                            pdf_download_error
                        )
                    )

                    print("=" * 80)

                    flash(
                        "Unable to load the existing signed "
                        "PDF for Engineer approval.",
                        "danger"
                    )

                    return redirect(
                        url_for(
                            "construction_engineer_material_request_review",
                            request_id=request_id
                        )
                    )

                # ==================================================
                # CREATE ENGINEERING STAMP
                #
                # PURCHASE  = LEFT
                # ENGINEER  = CENTER
                # MANAGER   = RIGHT
                # ==================================================

                try:

                    engineer_signed_pdf = create_signed_pdf(
                        pdf_bytes=existing_pdf_bytes,
                        signature_data=signature_data,
                        signer_name=engineer_name,
                        department_title="ENGINEERING DEPARTMENT",
                        position="center"
                    )

                    if not engineer_signed_pdf:

                        raise ValueError(
                            "Engineer signed PDF is empty."
                        )

                    # ==============================================
                    # VALIDATE GENERATED PDF
                    # ==============================================

                    if not engineer_signed_pdf.startswith(
                        b"%PDF"
                    ):

                        raise ValueError(
                            "Engineering stamp process did not "
                            "produce a valid PDF."
                        )

                    print("=" * 80)
                    print(
                        "ENGINEERING PDF STAMP CREATED"
                    )

                    print(
                        "GENERATED PDF SIZE:",
                        len(engineer_signed_pdf),
                        "bytes"
                    )

                    print("=" * 80)

                except Exception as stamp_error:

                    conn.rollback()

                    print("=" * 80)
                    print(
                        "ENGINEER PDF STAMP ERROR"
                    )

                    print(
                        "ERROR TYPE:",
                        type(
                            stamp_error
                        ).__name__
                    )

                    print(
                        "ERROR:",
                        str(
                            stamp_error
                        )
                    )

                    import traceback
                    traceback.print_exc()

                    print("=" * 80)

                    flash(
                        "The Engineering approval stamp "
                        "could not be added to the PDF.",
                        "danger"
                    )

                    return redirect(
                        url_for(
                            "construction_engineer_material_request_review",
                            request_id=request_id
                        )
                    )

                # ==================================================
                # UPLOAD ENGINEER-STAMPED PDF
                #
                # IMPORTANT:
                #
                # Use Cloudinary IMAGE/PDF delivery rather than
                # RAW delivery.
                #
                # This keeps the PDF accessible directly in the
                # browser and allows the Manager stage to retrieve
                # the same stamped PDF reliably.
                #
                # A NEW PUBLIC ID is created for this stage.
                # ==================================================

                try:

                    new_signed_file_name = (
                        f"{locked_request['request_number']}"
                        f"_engineer_approved.pdf"
                    )

                    engineer_signed_file = BytesIO(
                        engineer_signed_pdf
                    )

                    engineer_signed_file.name = (
                        new_signed_file_name
                    )

                    cloudinary_folder = (
                        "prestigious_construction/"
                        "purchase/material_requests"
                    )

                    # ==============================================
                    # UNIQUE ENGINEERING DOCUMENT ID
                    #
                    # The timestamp prevents the Engineer stage from
                    # accidentally replacing an older version.
                    # ==============================================

                    engineer_public_id = (
                        f"{locked_request['request_number']}"
                        f"_engineer_approved_"
                        f"{reviewed_at.strftime('%Y%m%d%H%M%S')}"
                    )

                    upload_result = (
                        cloudinary.uploader.upload(
                            engineer_signed_file,

                            resource_type="image",

                            format="pdf",

                            folder=cloudinary_folder,

                            public_id=engineer_public_id,

                            overwrite=False
                        )
                    )

                    new_signed_file_url = (
                        upload_result.get(
                            "secure_url"
                        )
                    )

                    new_signed_public_id = (
                        upload_result.get(
                            "public_id"
                        )
                    )

                    # ==================================================
                    # VERIFY CLOUDINARY RESPONSE
                    # ==================================================

                    if not new_signed_file_url:

                        raise ValueError(
                            "Cloudinary did not return "
                            "a secure URL for the "
                            "Engineering-stamped PDF."
                        )

                    if not new_signed_public_id:

                        raise ValueError(
                            "Cloudinary did not return "
                            "a public ID for the "
                            "Engineering-stamped PDF."
                        )

                    print("=" * 80)
                    print(
                        "ENGINEER CLOUDINARY UPLOAD SUCCESS"
                    )

                    print(
                        "FILE NAME:",
                        new_signed_file_name
                    )

                    print(
                        "PUBLIC ID:",
                        new_signed_public_id
                    )

                    print(
                        "SECURE URL:",
                        new_signed_file_url
                    )

                    print(
                        "RESOURCE TYPE:",
                        upload_result.get(
                            "resource_type"
                        )
                    )

                    print(
                        "FORMAT:",
                        upload_result.get(
                            "format"
                        )
                    )

                    print("=" * 80)

                except Exception as upload_error:

                    conn.rollback()

                    print("=" * 80)
                    print(
                        "ENGINEER CLOUDINARY UPLOAD ERROR"
                    )

                    print(
                        "ERROR TYPE:",
                        type(
                            upload_error
                        ).__name__
                    )

                    print(
                        "ERROR:",
                        str(
                            upload_error
                        )
                    )

                    import traceback
                    traceback.print_exc()

                    print("=" * 80)

                    flash(
                        "The Engineering-stamped PDF "
                        "could not be uploaded.",
                        "danger"
                    )

                    return redirect(
                        url_for(
                            "construction_engineer_material_request_review",
                            request_id=request_id
                        )
                    )

            # ==================================================
            # 1. ENGINEER REVIEW HISTORY
            # ==================================================

            cursor.execute(
                """
                INSERT INTO
                construction_purchase_engineer_reviews
                (
                    material_request_id,
                    engineer_id,
                    engineer_name,
                    action,
                    comment,
                    signature_data,
                    signed_at,
                    created_at
                )
                VALUES
                (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s
                )
                """,
                (
                    request_id,
                    engineer_id,
                    engineer_name,
                    action,
                    comment if comment else None,
                    signature_data,
                    reviewed_at,
                    reviewed_at
                )
            )

            # ==================================================
            # 2. UPDATE MATERIAL REQUEST
            # ==================================================

            cursor.execute(
                """
                UPDATE
                    construction_purchase_material_requests

                SET
                    status = %s,

                    engineer_id = %s,
                    engineer_name = %s,
                    engineer_reviewed_at = %s,
                    engineer_comment = %s,

                    signed_file_name = %s,
                    signed_file_url = %s,
                    signed_public_id = %s,

                    updated_at = CURRENT_TIMESTAMP

                WHERE
                    id = %s
                """,
                (
                    new_status,

                    engineer_id,
                    engineer_name,
                    reviewed_at,
                    comment if comment else None,

                    new_signed_file_name,
                    new_signed_file_url,
                    new_signed_public_id,

                    request_id
                )
            )

            # ==================================================
            # VERIFY UPDATE
            # ==================================================

            if cursor.rowcount != 1:

                conn.rollback()

                flash(
                    "The Material Request could not be updated.",
                    "danger"
                )

                return redirect(
                    url_for(
                        "construction_engineer_dashboard"
                    )
                )

            # ==================================================
            # 3. AUDIT LOG
            # ==================================================

            cursor.execute(
                """
                INSERT INTO
                construction_purchase_audit_logs
                (
                    material_request_id,
                    user_id,
                    user_name,
                    user_role,
                    action,
                    description,
                    old_status,
                    new_status,
                    ip_address,
                    created_at
                )
                VALUES
                (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s
                )
                """,
                (
                    request_id,

                    engineer_id,
                    engineer_name,

                    "engineer",

                    "Engineer Review - " + action,

                    comment if comment else None,

                    locked_request["status"],
                    new_status,

                    request.remote_addr,

                    reviewed_at
                )
            )

            # ==================================================
            # 4. COMMIT DATABASE
            #
            # Commit BEFORE emails.
            # Email failure must never undo the Engineer decision.
            # ==================================================

            conn.commit()

            # ==================================================
            # 5. EMAIL NOTIFICATIONS
            # ==================================================

            try:

                notification_cursor = conn.cursor(
                    dictionary=True
                )

                # ==================================================
                # GET PURCHASE OFFICER
                # ==================================================

                notification_cursor.execute(
                    """
                    SELECT
                        fullname,
                        email

                    FROM
                        construction_admins

                    WHERE
                        id = %s

                    LIMIT 1
                    """,
                    (
                        material_request["requested_by"],
                    )
                )

                purchaser = (
                    notification_cursor.fetchone()
                )

                # ==================================================
                # GET ENGINEER
                # ==================================================

                notification_cursor.execute(
                    """
                    SELECT
                        id,
                        fullname,
                        email

                    FROM
                        construction_admins

                    WHERE
                        id = %s

                    LIMIT 1
                    """,
                    (
                        engineer_id,
                    )
                )

                approving_engineer = (
                    notification_cursor.fetchone()
                )

                # ==================================================
                # APPROVED:
                # GET ALL MANAGERS
                # ==================================================

                managers = []

                if action == "Approved":

                    notification_cursor.execute(
                        """
                        SELECT
                            id,
                            fullname,
                            email

                        FROM
                            construction_admins

                        WHERE
                            LOWER(role) = 'manager'

                            AND email IS NOT NULL

                            AND TRIM(email) != ''

                        ORDER BY
                            id ASC
                        """
                    )

                    managers = (
                        notification_cursor.fetchall()
                    )

                notification_cursor.close()

                # ==================================================
                # COMMON DATA
                # ==================================================

                request_number = (
                    locked_request["request_number"]
                )

                project_name = (
                    locked_request["project_name"]
                )

                requester_name = (
                    locked_request["requested_by_name"]
                )

                request_date = (
                    locked_request["request_date"]
                )

                description = (
                    locked_request["description"]
                    or "No description provided"
                )

                review_date = (
                    reviewed_at.strftime(
                        "%Y-%m-%d %H:%M AST"
                    )
                )

                # ==================================================
                # IMPORTANT:
                #
                # For Engineer approval, this is the NEW
                # Engineering-stamped document.
                #
                # For Correction Required, it remains the
                # existing signed document.
                # ==================================================

                document_url = (
                    new_signed_file_url
                    or locked_request["signed_file_url"]
                )

                # ==================================================
                # DOCUMENT BUTTON
                # ==================================================

                document_button = ""

                if document_url:

                    document_button = f"""
                    <p style="margin-top:20px;">
                        <a
                            href="{document_url}"
                            target="_blank"
                            rel="noopener noreferrer"
                            style="
                                display:inline-block;
                                padding:11px 20px;
                                background:#006b3c;
                                color:#ffffff;
                                text-decoration:none;
                                border-radius:5px;
                                font-weight:bold;
                            "
                        >
                            Open Stamped Material Request
                        </a>
                    </p>
                    """

                # ==================================================
                # APPROVED EMAIL
                # ==================================================

                if action == "Approved":

                    approval_email_html = f"""
                    <h2>
                        Material Request Approved by Engineering
                    </h2>

                    <p>
                        The following Material Request has been
                        reviewed and approved by the Engineering
                        Department.
                    </p>

                    <hr>

                    <h3>
                        Material Request Details
                    </h3>

                    <p>
                        <b>Request Number:</b>
                        {request_number}
                    </p>

                    <p>
                        <b>Project:</b>
                        {project_name}
                    </p>

                    <p>
                        <b>Actual Requested By:</b>
                        {requester_name}
                    </p>

                    <p>
                        <b>Request Date:</b>
                        {request_date}
                    </p>

                    <p>
                        <b>Description:</b>
                        {description}
                    </p>

                    <p>
                        <b>Engineer:</b>
                        {engineer_name}
                    </p>

                    <p>
                        <b>Engineering Approval Date:</b>
                        {review_date}
                    </p>

                    <p>
                        <b>Engineering Comment:</b>
                        {comment or "No comment provided"}
                    </p>

                    <p>
                        <b>New Status:</b>
                        Pending Manager Approval
                    </p>

                    <hr>

                    <h3>
                        Engineering-Stamped Document
                    </h3>

                    <p>
                        The Material Request PDF has been digitally
                        stamped by Engineering and is ready for
                        Manager approval.
                    </p>

                    {document_button}

                    <hr>

                    <p>
                        Please log into the Construction Management
                        System to continue the approval process.
                    </p>

                    <p>
                        Regards,<br>
                        <b>
                            Prestigious Trading & Construction W.L.L.
                        </b>
                    </p>

                    <p>
                        <small>
                            This notification was generated
                            automatically by the Construction
                            Management System.
                        </small>
                    </p>
                    """

                    # ==================================================
                    # SEND TO PURCHASE OFFICER
                    # ==================================================

                    if purchaser:

                        purchaser_email = (
                            purchaser.get("email") or ""
                        ).strip()

                        purchaser_name = (
                            purchaser.get("fullname")
                            or "Purchase Officer"
                        )

                        if purchaser_email:

                            try:

                                send_email(
                                    purchaser_email,

                                    (
                                        "Material Request Approved "
                                        "by Engineering - "
                                        f"{request_number}"
                                    ),

                                    f"""
                                    <h2>
                                        Material Request Approved
                                    </h2>

                                    <p>
                                        Hello
                                        <b>{purchaser_name}</b>,
                                    </p>

                                    <p>
                                        Your Material Request has been
                                        approved by the Engineering
                                        Department and is now awaiting
                                        Manager Approval.
                                    </p>

                                    <hr>

                                    {approval_email_html}
                                    """
                                )

                                print(
                                    "PURCHASE OFFICER APPROVAL EMAIL SENT:",
                                    purchaser_email
                                )

                            except Exception as purchaser_email_error:

                                print(
                                    "PURCHASE OFFICER APPROVAL EMAIL ERROR:",
                                    purchaser_email,
                                    repr(
                                        purchaser_email_error
                                    )
                                )

                    # ==================================================
                    # SEND TO APPROVING ENGINEER
                    # ==================================================

                    if approving_engineer:

                        engineer_email = (
                            approving_engineer.get("email")
                            or ""
                        ).strip()

                        approving_engineer_name = (
                            approving_engineer.get("fullname")
                            or engineer_name
                        )

                        if engineer_email:

                            try:

                                send_email(
                                    engineer_email,

                                    (
                                        "Engineering Approval Recorded - "
                                        f"{request_number}"
                                    ),

                                    f"""
                                    <h2>
                                        Engineering Approval Recorded
                                    </h2>

                                    <p>
                                        Hello
                                        <b>{approving_engineer_name}</b>,
                                    </p>

                                    <p>
                                        Your approval of this Material
                                        Request has been successfully
                                        recorded.
                                    </p>

                                    <hr>

                                    {approval_email_html}
                                    """
                                )

                                print(
                                    "ENGINEER APPROVAL EMAIL SENT:",
                                    engineer_email
                                )

                            except Exception as engineer_email_error:

                                print(
                                    "ENGINEER APPROVAL EMAIL ERROR:",
                                    engineer_email,
                                    repr(
                                        engineer_email_error
                                    )
                                )

                    # ==================================================
                    # SEND TO ALL MANAGERS
                    # ==================================================

                    for manager in managers:

                        manager_email = (
                            manager.get("email") or ""
                        ).strip()

                        manager_name = (
                            manager.get("fullname")
                            or "Manager"
                        )

                        if not manager_email:
                            continue

                        manager_message = f"""
                        <h2>
                            Material Request Requires Manager Approval
                        </h2>

                        <p>
                            Hello
                            <b>{manager_name}</b>,
                        </p>

                        <p>
                            A Material Request has been approved
                            by the Engineering Department and is
                            now waiting for Manager Approval.
                        </p>

                        <hr>

                        {approval_email_html}
                        """

                        try:

                            send_email(
                                manager_email,

                                (
                                    "Material Request Requires "
                                    "Manager Approval - "
                                    f"{request_number}"
                                ),

                                manager_message
                            )

                            print(
                                "MANAGER APPROVAL EMAIL SENT:",
                                manager_email
                            )

                        except Exception as manager_email_error:

                            print(
                                "MANAGER APPROVAL EMAIL ERROR:",
                                manager_email,
                                repr(manager_email_error)
                            )

                # ==================================================
                # CORRECTION REQUIRED EMAIL
                # ==================================================

                elif action == "Correction Required":

                    correction_email_html = f"""
                    <h2>
                        Material Request Requires Correction
                    </h2>

                    <p>
                        The following Material Request has been
                        reviewed by the Engineering Department and
                        requires correction before it can proceed
                        to Manager Approval.
                    </p>

                    <hr>

                    <h3>
                        Material Request Details
                    </h3>

                    <p>
                        <b>Request Number:</b>
                        {request_number}
                    </p>

                    <p>
                        <b>Project:</b>
                        {project_name}
                    </p>

                    <p>
                        <b>Actual Requested By:</b>
                        {requester_name}
                    </p>

                    <p>
                        <b>Request Date:</b>
                        {request_date}
                    </p>

                    <p>
                        <b>Description:</b>
                        {description}
                    </p>

                    <p>
                        <b>Engineer:</b>
                        {engineer_name}
                    </p>

                    <p>
                        <b>Engineering Review Date:</b>
                        {review_date}
                    </p>

                    <p>
                        <b>New Status:</b>
                        <span style="
                            color:#b45309;
                            font-weight:bold;
                        ">
                            Engineer Correction Required
                        </span>
                    </p>

                    <hr>

                    <h3>
                        Engineer's Correction Comment
                    </h3>

                    <div style="
                        background:#fff7ed;
                        border-left:4px solid #f59e0b;
                        padding:14px 16px;
                        margin:12px 0;
                        line-height:1.6;
                    ">
                        {comment}
                    </div>

                    <h3>
                        Stamped Material Request
                    </h3>

                    <p>
                        The existing stamped Material Request
                        remains available for review.
                    </p>

                    {document_button}

                    <hr>

                    <p>
                        Please review the Engineer's correction
                        comment and make the necessary changes
                        before resubmitting the Material Request
                        for Engineering review.
                    </p>

                    <p>
                        Regards,<br>
                        <b>
                            Prestigious Trading & Construction W.L.L.
                        </b>
                    </p>

                    <p>
                        <small>
                            This notification was generated
                            automatically by the Construction
                            Management System.
                        </small>
                    </p>
                    """

                    # ==================================================
                    # SEND CORRECTION EMAIL TO PURCHASE OFFICER
                    # ==================================================

                    if purchaser:

                        purchaser_email = (
                            purchaser.get("email") or ""
                        ).strip()

                        purchaser_name = (
                            purchaser.get("fullname")
                            or "Purchase Officer"
                        )

                        if purchaser_email:

                            try:

                                send_email(
                                    purchaser_email,

                                    (
                                        "Material Request Requires "
                                        "Correction - "
                                        f"{request_number}"
                                    ),

                                    f"""
                                    <h2>
                                        Material Request Requires Correction
                                    </h2>

                                    <p>
                                        Hello
                                        <b>{purchaser_name}</b>,
                                    </p>

                                    <p>
                                        The Engineer has reviewed your
                                        Material Request and has requested
                                        corrections before the request can
                                        proceed to Manager Approval.
                                    </p>

                                    <hr>

                                    {correction_email_html}
                                    """
                                )

                                print(
                                    "PURCHASE OFFICER CORRECTION EMAIL SENT:",
                                    purchaser_email
                                )

                            except Exception as purchaser_correction_error:

                                print(
                                    "PURCHASE OFFICER CORRECTION EMAIL ERROR:",
                                    purchaser_email,
                                    repr(
                                        purchaser_correction_error
                                    )
                                )

                    # ==================================================
                    # SEND CORRECTION EMAIL TO ENGINEER
                    # ==================================================

                    if approving_engineer:

                        engineer_email = (
                            approving_engineer.get("email")
                            or ""
                        ).strip()

                        approving_engineer_name = (
                            approving_engineer.get("fullname")
                            or engineer_name
                        )

                        if engineer_email:

                            try:

                                send_email(
                                    engineer_email,

                                    (
                                        "Correction Request Recorded - "
                                        f"{request_number}"
                                    ),

                                    f"""
                                    <h2>
                                        Correction Request Recorded
                                    </h2>

                                    <p>
                                        Hello
                                        <b>{approving_engineer_name}</b>,
                                    </p>

                                    <p>
                                        Your correction request for the
                                        following Material Request has
                                        been successfully recorded.
                                    </p>

                                    <hr>

                                    {correction_email_html}

                                    <p>
                                        The Purchase Officer has been
                                        notified of the required
                                        correction.
                                    </p>
                                    """
                                )

                                print(
                                    "ENGINEER CORRECTION EMAIL SENT:",
                                    engineer_email
                                )

                            except Exception as engineer_correction_error:

                                print(
                                    "ENGINEER CORRECTION EMAIL ERROR:",
                                    engineer_email,
                                    repr(
                                        engineer_correction_error
                                    )
                                )

            except Exception as email_error:

                print("=" * 80)
                print(
                    "ENGINEER EMAIL NOTIFICATION PROCESS ERROR"
                )

                print(
                    "ERROR TYPE:",
                    type(email_error).__name__
                )

                print(
                    "ERROR:",
                    str(email_error)
                )

                import traceback
                traceback.print_exc()

                print("=" * 80)

            # ==================================================
            # SUCCESS FLASH
            # ==================================================

            if action == "Approved":

                flash(
                    f"Material Request "
                    f"{material_request['request_number']} "
                    "has been approved by Engineering and "
                    "sent to Manager Approval.",
                    "success"
                )

            else:

                flash(
                    f"Material Request "
                    f"{material_request['request_number']} "
                    "has been returned for correction.",
                    "warning"
                )

            return redirect(
                url_for(
                    "construction_engineer_dashboard"
                )
            )

        # ==================================================
        # GET ENGINEER REVIEW HISTORY
        # ==================================================

        cursor.execute(
            """
            SELECT
                id,
                material_request_id,
                engineer_id,
                engineer_name,
                action,
                comment,
                signature_data,
                signed_at,
                created_at

            FROM
                construction_purchase_engineer_reviews

            WHERE
                material_request_id = %s

            ORDER BY
                created_at DESC,
                id DESC
            """,
            (
                request_id,
            )
        )

        engineer_reviews = (
            cursor.fetchall() or []
        )

        # ==================================================
        # RENDER PAGE
        # ==================================================

        return render_template(
            "construction_admin/"
            "construction_engineer_material_request_review.html",

            material_request=material_request,

            engineer_reviews=engineer_reviews,

            engineer_name=engineer_name
        )

    # ======================================================
    # GENERAL ERROR
    # ======================================================

    except Exception as e:

        if conn:

            try:
                conn.rollback()
            except Exception:
                pass

        import traceback

        print("=" * 80)

        print(
            "ENGINEER MATERIAL REQUEST REVIEW ERROR"
        )

        print(
            "ERROR TYPE:",
            type(e).__name__
        )

        print(
            "ERROR MESSAGE:",
            str(e)
        )

        print(
            "FULL TRACEBACK:"
        )

        traceback.print_exc()

        print("=" * 80)

        flash(
            f"Engineer review error: {str(e)}",
            "danger"
        )

        return redirect(
            url_for(
                "construction_engineer_dashboard"
            )
        )

    finally:

        if cursor:

            try:
                cursor.close()
            except Exception:
                pass

        if conn:

            try:
                conn.close()
            except Exception:
                pass






# ============================================================
# MANAGER DASHBOARD
# ============================================================

@app.route("/construction/manager/dashboard")
def construction_manager_dashboard():

    # ============================================================
    # LOGIN PROTECTION
    # ============================================================

    if "construction_admin_id" not in session:
        return redirect(url_for("admin_login"))

    # ============================================================
    # ROLE PROTECTION
    # ============================================================

    role = (
        session.get("construction_admin_role") or ""
    ).strip().lower()

    if role != "manager":
        flash(
            "You are not authorized to access the Manager dashboard.",
            "danger"
        )
        return redirect(construction_role_dashboard())

    conn = None
    cursor = None

    # ============================================================
    # MATERIAL REQUEST DATA
    # ============================================================

    material_requests = []

    total_requests = 0
    pending_manager_approval = 0
    engineer_approved_requests = 0
    manager_approved_requests = 0
    declined_requests = 0
    correction_required = 0
    completed_requests = 0

    # ============================================================
    # PROCUREMENT DATA
    # ============================================================

    manager_procurements = []

    total_procurements = 0
    pending_manager_procurements = 0
    pending_final_procurements = 0
    completed_procurements = 0

    quotation_procurements = 0
    card_procurements = 0

    pending_quotation_procurements = 0
    pending_card_procurements = 0

    final_quotation_procurements = 0
    final_card_procurements = 0

    completed_quotation_procurements = 0
    completed_card_procurements = 0

    try:

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        # ========================================================
        # MATERIAL REQUEST COUNTS
        # ========================================================

        cursor.execute("""
            SELECT
                COUNT(*) AS total_requests,

                SUM(
                    CASE
                        WHEN status = 'Pending Manager Approval'
                        THEN 1
                        ELSE 0
                    END
                ) AS pending_manager_approval,

                SUM(
                    CASE
                        WHEN status = 'Pending Manager Approval'
                        THEN 1
                        ELSE 0
                    END
                ) AS engineer_approved_requests,

                SUM(
                    CASE
                        WHEN status = 'Approved'
                        THEN 1
                        ELSE 0
                    END
                ) AS manager_approved_requests,

                SUM(
                    CASE
                        WHEN status = 'Declined'
                        THEN 1
                        ELSE 0
                    END
                ) AS declined_requests,

                SUM(
                    CASE
                        WHEN status = 'Engineer Correction Required'
                        THEN 1
                        ELSE 0
                    END
                ) AS correction_required,

                SUM(
                    CASE
                        WHEN status = 'Completed'
                        THEN 1
                        ELSE 0
                    END
                ) AS completed_requests

            FROM construction_purchase_material_requests
        """)

        counts = cursor.fetchone() or {}

        total_requests = counts.get("total_requests") or 0

        pending_manager_approval = (
            counts.get("pending_manager_approval") or 0
        )

        engineer_approved_requests = (
            counts.get("engineer_approved_requests") or 0
        )

        manager_approved_requests = (
            counts.get("manager_approved_requests") or 0
        )

        declined_requests = (
            counts.get("declined_requests") or 0
        )

        correction_required = (
            counts.get("correction_required") or 0
        )

        completed_requests = (
            counts.get("completed_requests") or 0
        )

        # ========================================================
        # MATERIAL REQUEST HISTORY
        # ========================================================

        cursor.execute("""
            SELECT
                id,
                request_number,
                project_name,
                requested_by,
                requested_by_name,
                request_date,
                description,

                original_file_name,
                original_file_url,
                original_public_id,

                signed_file_name,
                signed_file_url,
                signed_public_id,

                purchase_signature_data,
                purchase_signed_at,

                status,

                engineer_id,
                engineer_name,
                engineer_reviewed_at,
                engineer_comment,

                manager_id,
                manager_name,
                manager_approved_at,
                manager_comment,

                procurement_method,

                created_at,
                updated_at

            FROM construction_purchase_material_requests

            ORDER BY
                CASE
                    WHEN status = 'Pending Manager Approval'
                        THEN 1

                    WHEN status = 'Engineer Correction Required'
                        THEN 2

                    WHEN status = 'Approved'
                        THEN 3

                    WHEN status = 'Declined'
                        THEN 4

                    WHEN status = 'Completed'
                        THEN 5

                    WHEN status = 'Pending Engineer Review'
                        THEN 6

                    ELSE 7
                END,

                created_at DESC,
                id DESC
        """)

        material_requests = cursor.fetchall() or []

        # ========================================================
        # PROCUREMENT LIST
        # ========================================================

        cursor.execute("""
            SELECT

                p.id,
                p.material_request_id,

                p.procurement_method,
                p.status,
                p.workflow_stage,

                p.initiated_by,
                p.initiated_by_name,
                p.initiated_at,

                p.supplier_name,
                p.supplier_contact,

                p.procurement_amount,
                p.currency,

                p.card_paid_by,
                p.card_paid_by_name,
                p.card_paid_at,
                p.card_payment_reference,

                p.quotation_number,
                p.quotation_date,
                p.lpo_number,

                p.pre_purchase_submitted_by,
                p.pre_purchase_submitted_by_name,
                p.pre_purchase_submitted_at,

                p.account_cheque_issued_by,
                p.account_cheque_issued_by_name,
                p.account_cheque_issued_at,
                p.account_cheque_number,
                p.account_cheque_date,
                p.account_cheque_bank,

                p.submitted_to_account_at,

                p.account_reviewed_at,
                p.account_reviewer_id,
                p.account_reviewer_name,
                p.account_comment,
                p.account_signature_data,
                p.account_signed_at,

                p.manager_reviewed_at,
                p.manager_reviewer_id,
                p.manager_reviewer_name,
                p.manager_comment,
                p.manager_signature_data,
                p.manager_signed_at,

                p.final_purchase_started_by,
                p.final_purchase_started_by_name,
                p.final_purchase_started_at,

                p.final_payment_method,

                p.final_card_paid_by,
                p.final_card_paid_by_name,
                p.final_card_paid_at,
                p.final_card_payment_reference,

                p.cheque_required,
                p.cheque_number,
                p.cheque_date,
                p.cheque_bank,

                p.final_documents_submitted_by,
                p.final_documents_submitted_by_name,
                p.final_documents_submitted_at,

                p.completed_by,
                p.completed_by_name,
                p.completed_at,

                p.created_at,
                p.updated_at,

                mr.request_number,
                mr.project_name,

                mr.requested_by,
                mr.requested_by_name,

                mr.request_date,
                mr.description,

                mr.engineer_id,
                mr.engineer_name

            FROM construction_purchase_procurement p

            INNER JOIN construction_purchase_material_requests mr
                ON mr.id = p.material_request_id

            ORDER BY

                CASE
                    WHEN p.status = 'Pending Manager Review'
                        THEN 1

                    WHEN p.status = 'Manager Correction Required'
                        THEN 2

                    WHEN p.status = 'Pending Final Procurement'
                        THEN 3

                    WHEN p.status = 'Final Procurement Correction Required'
                        THEN 4

                    WHEN p.status = 'Completed'
                        THEN 5

                    ELSE 6
                END,

                p.updated_at DESC,
                p.id DESC
        """)

        manager_procurements = cursor.fetchall() or []

        # ========================================================
        # PROCUREMENT COUNTS
        # ========================================================

        total_procurements = len(manager_procurements)

        pending_manager_procurements = sum(
            1
            for procurement in manager_procurements
            if (
                (procurement.get("status") or "").strip()
                == "Pending Manager Review"
            )
        )

        pending_final_procurements = sum(
            1
            for procurement in manager_procurements
            if (
                (procurement.get("status") or "").strip()
                == "Pending Final Procurement"
            )
        )

        completed_procurements = sum(
            1
            for procurement in manager_procurements
            if (
                (procurement.get("status") or "").strip()
                == "Completed"
            )
        )

        # ========================================================
        # METHOD COUNTS
        # ========================================================

        for procurement in manager_procurements:

            method = (
                procurement.get("procurement_method") or ""
            ).strip().lower()

            status = (
                procurement.get("status") or ""
            ).strip().lower()

            if method == "quotation":

                quotation_procurements += 1

                if status == "pending manager review":
                    pending_quotation_procurements += 1

                elif status == "pending final procurement":
                    final_quotation_procurements += 1

                elif status == "completed":
                    completed_quotation_procurements += 1

            elif method == "card":

                card_procurements += 1

                if status == "pending manager review":
                    pending_card_procurements += 1

                elif status == "pending final procurement":
                    final_card_procurements += 1

                elif status == "completed":
                    completed_card_procurements += 1

        # ========================================================
        # LOAD PROCUREMENT DOCUMENTS
        # ========================================================

        procurement_ids = [
            procurement.get("id")
            for procurement in manager_procurements
            if procurement.get("id") is not None
        ]

        procurement_documents = []

        if procurement_ids:

            placeholders = ",".join(
                ["%s"] * len(procurement_ids)
            )

            cursor.execute(
                f"""
                    SELECT
                        id,
                        procurement_id,
                        document_type,
                        workflow_stage,
                        document_title,
                        original_file_name,
                        file_url,
                        public_id,
                        uploaded_by,
                        uploaded_by_name,
                        uploaded_at,
                        notes

                    FROM construction_purchase_procurement_documents

                    WHERE procurement_id IN ({placeholders})

                    ORDER BY
                        uploaded_at DESC,
                        id DESC
                """,
                tuple(procurement_ids)
            )

            procurement_documents = (
                cursor.fetchall() or []
            )

        # ========================================================
        # BUILD DOCUMENT MAP
        # ========================================================

        document_map = {}

        for document in procurement_documents:

            procurement_id = document.get(
                "procurement_id"
            )

            if procurement_id not in document_map:

                document_map[procurement_id] = {
                    "quotation_document": None,
                    "lpo_document": None,
                    "card_payment_document": None,

                    "manager_quotation_document": None,
                    "manager_lpo_document": None,
                    "manager_card_payment_document": None,

                    "final_payment_document": None
                }

            document_type = (
                document.get("document_type") or ""
            ).strip().lower()

            workflow_stage = (
                document.get("workflow_stage") or ""
            ).strip().lower()

            # ====================================================
            # ACCOUNT-STAMPED DOCUMENTS
            # ====================================================

            if workflow_stage == "account stamp":

                if (
                    document_type == "quotation"
                    and
                    document_map[procurement_id][
                        "quotation_document"
                    ] is None
                ):

                    document_map[procurement_id][
                        "quotation_document"
                    ] = document

                elif (
                    document_type == "lpo"
                    and
                    document_map[procurement_id][
                        "lpo_document"
                    ] is None
                ):

                    document_map[procurement_id][
                        "lpo_document"
                    ] = document

                elif (
                    document_type == "card payment evidence"
                    and
                    document_map[procurement_id][
                        "card_payment_document"
                    ] is None
                ):

                    document_map[procurement_id][
                        "card_payment_document"
                    ] = document

            # ====================================================
            # MANAGER-SIGNED DOCUMENTS
            # ====================================================

            elif workflow_stage == "manager signed":

                if (
                    document_type == "quotation"
                    and
                    document_map[procurement_id][
                        "manager_quotation_document"
                    ] is None
                ):

                    document_map[procurement_id][
                        "manager_quotation_document"
                    ] = document

                elif (
                    document_type == "lpo"
                    and
                    document_map[procurement_id][
                        "manager_lpo_document"
                    ] is None
                ):

                    document_map[procurement_id][
                        "manager_lpo_document"
                    ] = document

                elif (
                    document_type == "card payment evidence"
                    and
                    document_map[procurement_id][
                        "manager_card_payment_document"
                    ] is None
                ):

                    document_map[procurement_id][
                        "manager_card_payment_document"
                    ] = document

            # ====================================================
            # FINAL PROCUREMENT DOCUMENTS
            # ====================================================

            elif workflow_stage in (
                "final procurement",
                "completion"
            ):

                if (
                    document_type in (
                        "receipt",
                        "invoice",
                        "payment evidence",
                        "final payment evidence"
                    )
                    and
                    document_map[procurement_id][
                        "final_payment_document"
                    ] is None
                ):

                    document_map[procurement_id][
                        "final_payment_document"
                    ] = document

        # ========================================================
        # ATTACH DOCUMENTS + NORMALIZED FLAGS
        # ========================================================

        for procurement in manager_procurements:

            procurement_id = procurement.get("id")

            documents = document_map.get(
                procurement_id,
                {
                    "quotation_document": None,
                    "lpo_document": None,
                    "card_payment_document": None,

                    "manager_quotation_document": None,
                    "manager_lpo_document": None,
                    "manager_card_payment_document": None,

                    "final_payment_document": None
                }
            )

            procurement["quotation_document"] = (
                documents["quotation_document"]
            )

            procurement["lpo_document"] = (
                documents["lpo_document"]
            )

            procurement["card_payment_document"] = (
                documents["card_payment_document"]
            )

            procurement["manager_quotation_document"] = (
                documents["manager_quotation_document"]
            )

            procurement["manager_lpo_document"] = (
                documents["manager_lpo_document"]
            )

            procurement["manager_card_payment_document"] = (
                documents["manager_card_payment_document"]
            )

            procurement["final_payment_document"] = (
                documents["final_payment_document"]
            )

            # ====================================================
            # METHOD
            # ====================================================

            method = (
                procurement.get("procurement_method") or ""
            ).strip().lower()

            procurement["is_card_procurement"] = (
                method == "card"
            )

            procurement["is_quotation_procurement"] = (
                method == "quotation"
            )

            # ====================================================
            # STATUS FLAGS
            # ====================================================

            status = (
                procurement.get("status") or ""
            ).strip().lower()

            procurement["is_pending_manager_review"] = (
                status == "pending manager review"
            )

            procurement["is_manager_correction_required"] = (
                status == "manager correction required"
            )

            procurement["is_pending_final_procurement"] = (
                status == "pending final procurement"
            )

            procurement["is_final_procurement_correction_required"] = (
                status == "final procurement correction required"
            )

            procurement["is_completed"] = (
                status == "completed"
            )

            # ====================================================
            # DOCUMENT FLAGS
            # ====================================================

            procurement["has_account_documents"] = any([
                procurement.get("quotation_document"),
                procurement.get("lpo_document"),
                procurement.get("card_payment_document")
            ])

            procurement["has_manager_documents"] = any([
                procurement.get("manager_quotation_document"),
                procurement.get("manager_lpo_document"),
                procurement.get(
                    "manager_card_payment_document"
                )
            ])

            procurement["has_final_payment_document"] = bool(
                procurement.get("final_payment_document")
            )

    except Exception as e:

        print(
            "================================================="
        )
        print(
            "MANAGER DASHBOARD ERROR"
        )
        print(
            type(e).__name__
        )
        print(
            repr(e)
        )
        print(
            "================================================="
        )

        import traceback
        traceback.print_exc()

        flash(
            "Unable to load the Manager dashboard.",
            "danger"
        )

    finally:

        if cursor:
            try:
                cursor.close()
            except Exception:
                pass

        if conn:
            try:
                conn.close()
            except Exception:
                pass

    # ============================================================
    # RENDER DASHBOARD
    # ============================================================

    return render_template(
        "construction_admin/construction_manager_dashboard.html",

        material_requests=material_requests,

        total_requests=total_requests,
        pending_manager_approval=pending_manager_approval,
        engineer_approved_requests=engineer_approved_requests,
        manager_approved_requests=manager_approved_requests,
        declined_requests=declined_requests,
        correction_required=correction_required,
        completed_requests=completed_requests,

        manager_procurements=manager_procurements,

        total_procurements=total_procurements,
        pending_manager_procurements=pending_manager_procurements,
        pending_final_procurements=pending_final_procurements,
        completed_procurements=completed_procurements,

        quotation_procurements=quotation_procurements,
        card_procurements=card_procurements,

        pending_quotation_procurements=(
            pending_quotation_procurements
        ),

        pending_card_procurements=(
            pending_card_procurements
        ),

        final_quotation_procurements=(
            final_quotation_procurements
        ),

        final_card_procurements=(
            final_card_procurements
        ),

        completed_quotation_procurements=(
            completed_quotation_procurements
        ),

        completed_card_procurements=(
            completed_card_procurements
        )
    )







# ==========================================================
# MANAGER - REVIEW MATERIAL REQUEST
# ==========================================================

@app.route(
    "/construction/manager/material-request/<int:request_id>/review",
    methods=["GET", "POST"]
)
def construction_manager_material_request_review(request_id):

    # ======================================================
    # LOGIN PROTECTION
    # ======================================================

    if "construction_admin_id" not in session:

        flash(
            "Please sign in to access Manager Review.",
            "warning"
        )

        return redirect(
            url_for("admin_login")
        )

    # ======================================================
    # ROLE PROTECTION
    # ======================================================

    role = (
        session.get("construction_admin_role") or ""
    ).strip().lower()

    if role != "manager":

        flash(
            "You are not authorized to access Manager Review.",
            "danger"
        )

        return redirect(
            construction_role_dashboard()
        )

    # ======================================================
    # MANAGER INFORMATION
    # ======================================================

    manager_id = session.get(
        "construction_admin_id"
    )

    manager_name = session.get(
        "construction_admin_name",
        "Manager"
    )

    conn = None
    cursor = None

    try:

        # ==================================================
        # DATABASE CONNECTION
        # ==================================================

        conn = get_db_connection()

        cursor = conn.cursor(
            dictionary=True
        )

        # ==================================================
        # GET MATERIAL REQUEST
        # ==================================================

        cursor.execute(
            """
            SELECT
                id,
                request_number,
                project_name,
                requested_by,
                requested_by_name,
                request_date,
                description,

                original_file_name,
                original_file_url,
                original_public_id,

                signed_file_name,
                signed_file_url,
                signed_public_id,

                purchase_signature_data,
                purchase_signed_at,

                status,

                engineer_id,
                engineer_name,
                engineer_reviewed_at,
                engineer_comment,

                manager_id,
                manager_name,
                manager_approved_at,
                manager_comment,

                procurement_method,

                created_at,
                updated_at

            FROM
                construction_purchase_material_requests

            WHERE
                id = %s

            LIMIT 1
            """,
            (
                request_id,
            )
        )

        material_request = cursor.fetchone()

        # ==================================================
        # REQUEST NOT FOUND
        # ==================================================

        if not material_request:

            flash(
                "Material Request was not found.",
                "danger"
            )

            return redirect(
                url_for(
                    "construction_manager_dashboard"
                )
            )

        # ==================================================
        # ONLY PENDING MANAGER APPROVAL CAN BE PROCESSED
        # ==================================================

        if material_request["status"] != (
            "Pending Manager Approval"
        ):

            flash(
                "This Material Request is not currently "
                "awaiting Manager Approval.",
                "warning"
            )

            return redirect(
                url_for(
                    "construction_manager_dashboard"
                )
            )

        # ==================================================
        # POST REQUEST
        # ==================================================

        if request.method == "POST":

            # ==================================================
            # FORM DATA
            # ==================================================

            action = (
                request.form.get("action") or ""
            ).strip()

            comment = (
                request.form.get("comment") or ""
            ).strip()

            signature_data = (
                request.form.get("signature_data") or ""
            ).strip()

            # ==================================================
            # DEBUG
            # ==================================================

            print("=" * 80)
            print("MANAGER REVIEW POST DEBUG")
            print("REQUEST ID:", request_id)

            print(
                "FORM DATA:",
                {
                    "action": action,
                    "comment": comment,
                    "signature_data":
                        "[PRESENT]"
                        if signature_data
                        else "[EMPTY]"
                }
            )

            print(
                "ACTION:",
                repr(action)
            )

            print(
                "COMMENT:",
                repr(comment)
            )

            print(
                "SIGNATURE LENGTH:",
                len(signature_data)
            )

            print("=" * 80)

            # ==================================================
            # ACTION VALIDATION
            # ==================================================

            if action not in [
                "Approved",
                "Declined"
            ]:

                flash(
                    "Invalid Manager action.",
                    "danger"
                )

                return redirect(
                    url_for(
                        "construction_manager_material_request_review",
                        request_id=request_id
                    )
                )

            # ==================================================
            # SIGNATURE VALIDATION
            #
            # Manager must digitally sign both:
            #
            # APPROVED
            # DECLINED
            #
            # Only APPROVED places the Manager signature
            # onto the PDF.
            # ==================================================

            if not signature_data:

                flash(
                    "Please provide your signature before "
                    "submitting the review.",
                    "danger"
                )

                return redirect(
                    url_for(
                        "construction_manager_material_request_review",
                        request_id=request_id
                    )
                )

            # ==================================================
            # NEW STATUS
            #
            # IMPORTANT:
            #
            # Pending Account Cash Release has been completely
            # removed from the Material Request workflow.
            # ==================================================

            new_status = (
                "Approved"
                if action == "Approved"
                else "Declined"
            )

            # ==================================================
            # QATAR LOCAL TIME
            # ==================================================

            reviewed_at = get_qatar_now()

            # ==================================================
            # LOCK MATERIAL REQUEST
            #
            # Prevent two Managers from processing the same
            # Material Request simultaneously.
            # ==================================================

            cursor.execute(
                """
                SELECT
                    id,
                    request_number,
                    project_name,
                    requested_by,
                    requested_by_name,
                    request_date,
                    description,
                    status,

                    signed_file_url,
                    signed_file_name,
                    signed_public_id,

                    engineer_id,
                    engineer_name,
                    engineer_reviewed_at,
                    engineer_comment

                FROM
                    construction_purchase_material_requests

                WHERE
                    id = %s

                FOR UPDATE
                """,
                (
                    request_id,
                )
            )

            locked_request = cursor.fetchone()

            # ==================================================
            # REQUEST DISAPPEARED
            # ==================================================

            if not locked_request:

                conn.rollback()

                flash(
                    "Material Request no longer exists.",
                    "danger"
                )

                return redirect(
                    url_for(
                        "construction_manager_dashboard"
                    )
                )

            # ==================================================
            # STATUS CHECK AGAIN
            # ==================================================

            if locked_request["status"] != (
                "Pending Manager Approval"
            ):

                conn.rollback()

                flash(
                    "This Material Request has already been "
                    "processed by a Manager.",
                    "warning"
                )

                return redirect(
                    url_for(
                        "construction_manager_dashboard"
                    )
                )

            # ==================================================
            # DEFAULT SIGNED FILE VALUES
            #
            # DECLINED:
            #
            # Keep the Engineering-stamped PDF.
            #
            # APPROVED:
            #
            # Replace it with the Manager-stamped PDF.
            # ==================================================

            new_signed_file_url = (
                locked_request["signed_file_url"]
            )

            new_signed_public_id = (
                locked_request["signed_public_id"]
            )

            new_signed_file_name = (
                locked_request["signed_file_name"]
            )

            # ==================================================
            # MANAGER APPROVAL
            # ==================================================

            if action == "Approved":

                # ==================================================
                # GET EXISTING ENGINEERING-STAMPED PDF
                #
                # NEVER start from original_file_url.
                #
                # Existing PDF:
                #
                # PURCHASE   = LEFT
                # ENGINEER   = CENTER
                #
                # Manager adds:
                #
                # MANAGER    = RIGHT
                # ==================================================

                existing_pdf_url = (
                    locked_request["signed_file_url"]
                )

                if not existing_pdf_url:

                    conn.rollback()

                    flash(
                        "The Engineering-stamped Material Request "
                        "PDF could not be found.",
                        "danger"
                    )

                    return redirect(
                        url_for(
                            "construction_manager_material_request_review",
                            request_id=request_id
                        )
                    )

                # ==================================================
                # DOWNLOAD ENGINEERING-STAMPED PDF
                # ==================================================

                try:

                    pdf_response = requests.get(
                        existing_pdf_url,
                        timeout=60
                    )

                    pdf_response.raise_for_status()

                    existing_pdf_bytes = (
                        pdf_response.content
                    )

                    if not existing_pdf_bytes:

                        raise ValueError(
                            "Downloaded PDF is empty."
                        )

                except Exception as pdf_download_error:

                    conn.rollback()

                    print("=" * 80)
                    print(
                        "MANAGER PDF DOWNLOAD ERROR"
                    )

                    print(
                        "ERROR TYPE:",
                        type(
                            pdf_download_error
                        ).__name__
                    )

                    print(
                        "ERROR:",
                        str(
                            pdf_download_error
                        )
                    )

                    import traceback
                    traceback.print_exc()

                    print("=" * 80)

                    flash(
                        "Unable to load the Engineering-stamped "
                        "PDF for Manager approval.",
                        "danger"
                    )

                    return redirect(
                        url_for(
                            "construction_manager_material_request_review",
                            request_id=request_id
                        )
                    )

                # ==================================================
                # CREATE MANAGER STAMP
                #
                # PURCHASE = LEFT
                # ENGINEER = CENTER
                # MANAGER  = RIGHT
                # ==================================================

                try:

                    manager_signed_pdf = create_signed_pdf(
                        pdf_bytes=existing_pdf_bytes,
                        signature_data=signature_data,
                        signer_name=manager_name,
                        department_title="MANAGEMENT DEPARTMENT",
                        position="right"
                    )

                    if not manager_signed_pdf:

                        raise ValueError(
                            "Manager signed PDF is empty."
                        )

                except Exception as stamp_error:

                    conn.rollback()

                    print("=" * 80)
                    print(
                        "MANAGER PDF STAMP ERROR"
                    )

                    print(
                        "ERROR TYPE:",
                        type(
                            stamp_error
                        ).__name__
                    )

                    print(
                        "ERROR:",
                        str(
                            stamp_error
                        )
                    )

                    import traceback
                    traceback.print_exc()

                    print("=" * 80)

                    flash(
                        "The Manager approval stamp "
                        "could not be added to the PDF.",
                        "danger"
                    )

                    return redirect(
                        url_for(
                            "construction_manager_material_request_review",
                            request_id=request_id
                        )
                    )

                # ==================================================
                # UPLOAD MANAGER-STAMPED PDF
                #
                # IMPORTANT:
                #
                # PDF IS UPLOADED AS AN IMAGE RESOURCE.
                #
                # Cloudinary supports PDF delivery through the
                # image resource type. This allows the browser
                # PDF viewer to open the document instead of
                # treating it as a raw downloadable file.
                # ==================================================

                try:

                    new_signed_file_name = (
                        f"{locked_request['request_number']}"
                        f"_manager_approved.pdf"
                    )

                    manager_signed_file = BytesIO(
                        manager_signed_pdf
                    )

                    manager_signed_file.name = (
                        new_signed_file_name
                    )

                    cloudinary_folder = (
                        "prestigious_construction/"
                        "purchase/material_requests"
                    )

                    upload_result = (
                        cloudinary.uploader.upload(
                            manager_signed_file,
                            resource_type="image",
                            folder=cloudinary_folder,
                            public_id=(
                                f"{locked_request['request_number']}"
                                f"_manager_approved"
                            ),
                            format="pdf",
                            overwrite=True
                        )
                    )

                    new_signed_file_url = (
                        upload_result.get(
                            "secure_url"
                        )
                    )

                    new_signed_public_id = (
                        upload_result.get(
                            "public_id"
                        )
                    )

                    if not new_signed_file_url:

                        raise ValueError(
                            "Cloudinary did not return "
                            "a secure URL for the "
                            "Manager-stamped PDF."
                        )

                    print("=" * 80)
                    print(
                        "MANAGER CLOUDINARY UPLOAD SUCCESS"
                    )

                    print(
                        "RESOURCE TYPE: image"
                    )

                    print(
                        "FORMAT: pdf"
                    )

                    print(
                        "FILE NAME:",
                        new_signed_file_name
                    )

                    print(
                        "PUBLIC ID:",
                        new_signed_public_id
                    )

                    print(
                        "SECURE URL:",
                        new_signed_file_url
                    )

                    print("=" * 80)

                except Exception as upload_error:

                    conn.rollback()

                    print("=" * 80)
                    print(
                        "MANAGER CLOUDINARY UPLOAD ERROR"
                    )

                    print(
                        "ERROR TYPE:",
                        type(
                            upload_error
                        ).__name__
                    )

                    print(
                        "ERROR:",
                        str(
                            upload_error
                        )
                    )

                    import traceback
                    traceback.print_exc()

                    print("=" * 80)

                    flash(
                        "The Manager-stamped PDF "
                        "could not be uploaded.",
                        "danger"
                    )

                    return redirect(
                        url_for(
                            "construction_manager_material_request_review",
                            request_id=request_id
                        )
                    )

            # ==================================================
            # 1. MANAGER REVIEW HISTORY
            # ==================================================

            cursor.execute(
                """
                INSERT INTO
                construction_purchase_manager_reviews
                (
                    material_request_id,
                    manager_id,
                    manager_name,
                    action,
                    comment,
                    signature_data,
                    signed_at,
                    created_at
                )
                VALUES
                (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s
                )
                """,
                (
                    request_id,
                    manager_id,
                    manager_name,
                    action,
                    comment if comment else None,
                    signature_data,
                    reviewed_at,
                    reviewed_at
                )
            )

            # ==================================================
            # 2. UPDATE MATERIAL REQUEST
            # ==================================================

            cursor.execute(
                """
                UPDATE
                    construction_purchase_material_requests

                SET
                    status = %s,

                    manager_id = %s,
                    manager_name = %s,
                    manager_approved_at = %s,
                    manager_comment = %s,

                    signed_file_name = %s,
                    signed_file_url = %s,
                    signed_public_id = %s,

                    updated_at = CURRENT_TIMESTAMP

                WHERE
                    id = %s
                """,
                (
                    new_status,

                    manager_id,
                    manager_name,
                    reviewed_at,
                    comment if comment else None,

                    new_signed_file_name,
                    new_signed_file_url,
                    new_signed_public_id,

                    request_id
                )
            )

            # ==================================================
            # VERIFY UPDATE
            # ==================================================

            if cursor.rowcount != 1:

                conn.rollback()

                flash(
                    "The Material Request could not be updated.",
                    "danger"
                )

                return redirect(
                    url_for(
                        "construction_manager_dashboard"
                    )
                )

            # ==================================================
            # 3. AUDIT LOG
            # ==================================================

            cursor.execute(
                """
                INSERT INTO
                construction_purchase_audit_logs
                (
                    material_request_id,
                    user_id,
                    user_name,
                    user_role,
                    action,
                    description,
                    old_status,
                    new_status,
                    ip_address,
                    created_at
                )
                VALUES
                (
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s,
                    %s
                )
                """,
                (
                    request_id,

                    manager_id,
                    manager_name,
                    "manager",

                    "Manager Review - " + action,

                    comment if comment else None,

                    locked_request["status"],
                    new_status,

                    request.remote_addr,

                    reviewed_at
                )
            )

            # ==================================================
            # 4. COMMIT DATABASE
            # ==================================================

            conn.commit()

            # ==================================================
            # 5. EMAIL NOTIFICATIONS
            #
            # RECIPIENTS:
            #
            # PURCHASE OFFICER
            # ENGINEER
            # CURRENT MANAGER
            #
            # BOTH APPROVED AND DECLINED ARE NOTIFIED.
            # ==================================================

            try:

                notification_cursor = conn.cursor(
                    dictionary=True
                )

                # ==================================================
                # GET PURCHASE OFFICER
                # ==================================================

                notification_cursor.execute(
                    """
                    SELECT
                        fullname,
                        email

                    FROM
                        construction_admins

                    WHERE
                        id = %s

                    LIMIT 1
                    """,
                    (
                        locked_request["requested_by"],
                    )
                )

                purchaser = (
                    notification_cursor.fetchone()
                )

                # ==================================================
                # GET ENGINEER
                # ==================================================

                notification_cursor.execute(
                    """
                    SELECT
                        id,
                        fullname,
                        email

                    FROM
                        construction_admins

                    WHERE
                        id = %s

                    LIMIT 1
                    """,
                    (
                        locked_request["engineer_id"],
                    )
                )

                approving_engineer = (
                    notification_cursor.fetchone()
                )

                # ==================================================
                # GET CURRENT MANAGER
                # ==================================================

                notification_cursor.execute(
                    """
                    SELECT
                        id,
                        fullname,
                        email

                    FROM
                        construction_admins

                    WHERE
                        id = %s

                    LIMIT 1
                    """,
                    (
                        manager_id,
                    )
                )

                approving_manager = (
                    notification_cursor.fetchone()
                )

                notification_cursor.close()

                # ==================================================
                # COMMON DATA
                # ==================================================

                request_number = (
                    locked_request["request_number"]
                )

                project_name = (
                    locked_request["project_name"]
                )

                requester_name = (
                    locked_request["requested_by_name"]
                )

                request_date = (
                    locked_request["request_date"]
                )

                description = (
                    locked_request["description"]
                    or "No description provided"
                )

                review_date = (
                    reviewed_at.strftime(
                        "%Y-%m-%d %H:%M AST"
                    )
                )

                document_url = (
                    new_signed_file_url
                    or locked_request["signed_file_url"]
                )

                # ==================================================
                # DOCUMENT BUTTON
                # ==================================================

                document_button = ""

                if document_url:

                    document_button = f"""
                    <p style="margin-top:20px;">
                        <a
                            href="{document_url}"
                            target="_blank"
                            rel="noopener noreferrer"
                            style="
                                display:inline-block;
                                padding:11px 20px;
                                background:#006b3c;
                                color:#ffffff;
                                text-decoration:none;
                                border-radius:5px;
                                font-weight:bold;
                            "
                        >
                            Open Stamped Material Request
                        </a>
                    </p>
                    """

                # ==================================================
                # APPROVED EMAIL
                # ==================================================

                if action == "Approved":

                    approval_email_html = f"""
                    <h2>
                        Material Request Approved by Management
                    </h2>

                    <p>
                        The following Material Request has been
                        reviewed and approved by the Management
                        Department.
                    </p>

                    <hr>

                    <h3>
                        Material Request Details
                    </h3>

                    <p>
                        <b>Request Number:</b>
                        {request_number}
                    </p>

                    <p>
                        <b>Project:</b>
                        {project_name}
                    </p>

                    <p>
                        <b>Actual Requested By:</b>
                        {requester_name}
                    </p>

                    <p>
                        <b>Request Date:</b>
                        {request_date}
                    </p>

                    <p>
                        <b>Description:</b>
                        {description}
                    </p>

                    <p>
                        <b>Engineer:</b>
                        {locked_request["engineer_name"]
                        or "Engineering Department"}
                    </p>

                    <p>
                        <b>Engineering Approval Date:</b>
                        {locked_request["engineer_reviewed_at"]
                        or "N/A"}
                    </p>

                    <p>
                        <b>Manager:</b>
                        {manager_name}
                    </p>

                    <p>
                        <b>Manager Approval Date:</b>
                        {review_date}
                    </p>

                    <p>
                        <b>Manager Comment:</b>
                        {comment or "No comment provided"}
                    </p>

                    <p>
                        <b>New Status:</b>
                        <span style="
                            color:#166534;
                            font-weight:bold;
                        ">
                            Approved
                        </span>
                    </p>

                    <hr>

                    <h3>
                        Manager-Stamped Document
                    </h3>

                    <p>
                        The Material Request PDF has now been
                        digitally stamped by Management.
                    </p>

                    {document_button}

                    <hr>

                    <p>
                        Please log into the Construction Management
                        System to continue the procurement process.
                    </p>

                    <p>
                        Regards,<br>
                        <b>
                            Prestigious Trading & Construction W.L.L.
                        </b>
                    </p>

                    <p>
                        <small>
                            This notification was generated
                            automatically by the Construction
                            Management System.
                        </small>
                    </p>
                    """

                    # ==================================================
                    # SEND TO PURCHASE OFFICER
                    # ==================================================

                    if purchaser:

                        purchaser_email = (
                            purchaser.get("email") or ""
                        ).strip()

                        purchaser_name = (
                            purchaser.get("fullname")
                            or "Purchase Officer"
                        )

                        if purchaser_email:

                            try:

                                send_email(
                                    purchaser_email,

                                    (
                                        "Material Request Approved "
                                        "by Management - "
                                        f"{request_number}"
                                    ),

                                    f"""
                                    <h2>
                                        Material Request Approved
                                    </h2>

                                    <p>
                                        Hello
                                        <b>{purchaser_name}</b>,
                                    </p>

                                    <p>
                                        Your Material Request has been
                                        approved by Management.
                                    </p>

                                    <hr>

                                    {approval_email_html}
                                    """
                                )

                                print(
                                    "PURCHASE OFFICER MANAGER APPROVAL "
                                    "EMAIL SENT:",
                                    purchaser_email
                                )

                            except Exception as purchaser_email_error:

                                print(
                                    "PURCHASE OFFICER MANAGER APPROVAL "
                                    "EMAIL ERROR:",
                                    purchaser_email,
                                    repr(
                                        purchaser_email_error
                                    )
                                )

                    # ==================================================
                    # SEND TO ENGINEER
                    # ==================================================

                    if approving_engineer:

                        engineer_email = (
                            approving_engineer.get("email")
                            or ""
                        ).strip()

                        approving_engineer_name = (
                            approving_engineer.get("fullname")
                            or locked_request["engineer_name"]
                            or "Engineer"
                        )

                        if engineer_email:

                            try:

                                send_email(
                                    engineer_email,

                                    (
                                        "Material Request Approved "
                                        "by Management - "
                                        f"{request_number}"
                                    ),

                                    f"""
                                    <h2>
                                        Management Approval Recorded
                                    </h2>

                                    <p>
                                        Hello
                                        <b>{approving_engineer_name}</b>,
                                    </p>

                                    <p>
                                        The Material Request you approved
                                        has now also been approved by
                                        Management.
                                    </p>

                                    <hr>

                                    {approval_email_html}
                                    """
                                )

                                print(
                                    "ENGINEER MANAGER APPROVAL "
                                    "EMAIL SENT:",
                                    engineer_email
                                )

                            except Exception as engineer_email_error:

                                print(
                                    "ENGINEER MANAGER APPROVAL "
                                    "EMAIL ERROR:",
                                    engineer_email,
                                    repr(
                                        engineer_email_error
                                    )
                                )

                    # ==================================================
                    # SEND TO CURRENT MANAGER
                    # ==================================================

                    if approving_manager:

                        manager_email = (
                            approving_manager.get("email")
                            or ""
                        ).strip()

                        approving_manager_name = (
                            approving_manager.get("fullname")
                            or manager_name
                        )

                        if manager_email:

                            try:

                                send_email(
                                    manager_email,

                                    (
                                        "Management Approval Recorded - "
                                        f"{request_number}"
                                    ),

                                    f"""
                                    <h2>
                                        Management Approval Recorded
                                    </h2>

                                    <p>
                                        Hello
                                        <b>{approving_manager_name}</b>,
                                    </p>

                                    <p>
                                        Your approval of this Material
                                        Request has been successfully
                                        recorded.
                                    </p>

                                    <hr>

                                    {approval_email_html}
                                    """
                                )

                                print(
                                    "MANAGER APPROVAL EMAIL SENT:",
                                    manager_email
                                )

                            except Exception as manager_email_error:

                                print(
                                    "MANAGER APPROVAL EMAIL ERROR:",
                                    manager_email,
                                    repr(
                                        manager_email_error
                                    )
                                )

                # ==================================================
                # DECLINED EMAIL
                # ==================================================

                elif action == "Declined":

                    declined_email_html = f"""
                    <h2>
                        Material Request Declined by Management
                    </h2>

                    <p>
                        The following Material Request has been
                        reviewed and declined by the Management
                        Department.
                    </p>

                    <hr>

                    <h3>
                        Material Request Details
                    </h3>

                    <p>
                        <b>Request Number:</b>
                        {request_number}
                    </p>

                    <p>
                        <b>Project:</b>
                        {project_name}
                    </p>

                    <p>
                        <b>Actual Requested By:</b>
                        {requester_name}
                    </p>

                    <p>
                        <b>Request Date:</b>
                        {request_date}
                    </p>

                    <p>
                        <b>Description:</b>
                        {description}
                    </p>

                    <p>
                        <b>Engineer:</b>
                        {locked_request["engineer_name"]
                        or "Engineering Department"}
                    </p>

                    <p>
                        <b>Manager:</b>
                        {manager_name}
                    </p>

                    <p>
                        <b>Manager Review Date:</b>
                        {review_date}
                    </p>

                    <p>
                        <b>New Status:</b>
                        <span style="
                            color:#b91c1c;
                            font-weight:bold;
                        ">
                            Declined
                        </span>
                    </p>

                    <hr>

                    <h3>
                        Manager's Comment
                    </h3>

                    <div style="
                        background:#fef2f2;
                        border-left:4px solid #dc2626;
                        padding:14px 16px;
                        margin:12px 0;
                        line-height:1.6;
                    ">
                        {comment or "No decline reason provided"}
                    </div>

                    <h3>
                        Existing Stamped Document
                    </h3>

                    <p>
                        The Engineering-stamped Material Request
                        has been preserved. No Manager stamp was
                        added because the request was declined.
                    </p>

                    {document_button}

                    <hr>

                    <p>
                        Please review the Manager's decision and
                        comment in the Construction Management System.
                    </p>

                    <p>
                        Regards,<br>
                        <b>
                            Prestigious Trading & Construction W.L.L.
                        </b>
                    </p>

                    <p>
                        <small>
                            This notification was generated
                            automatically by the Construction
                            Management System.
                        </small>
                    </p>
                    """

                    # ==================================================
                    # SEND TO PURCHASE OFFICER
                    # ==================================================

                    if purchaser:

                        purchaser_email = (
                            purchaser.get("email") or ""
                        ).strip()

                        purchaser_name = (
                            purchaser.get("fullname")
                            or "Purchase Officer"
                        )

                        if purchaser_email:

                            try:

                                send_email(
                                    purchaser_email,

                                    (
                                        "Material Request Declined "
                                        "by Management - "
                                        f"{request_number}"
                                    ),

                                    f"""
                                    <h2>
                                        Material Request Declined
                                    </h2>

                                    <p>
                                        Hello
                                        <b>{purchaser_name}</b>,
                                    </p>

                                    <p>
                                        Your Material Request has been
                                        declined by Management.
                                    </p>

                                    <hr>

                                    {declined_email_html}
                                    """
                                )

                                print(
                                    "PURCHASE OFFICER MANAGER DECLINE "
                                    "EMAIL SENT:",
                                    purchaser_email
                                )

                            except Exception as purchaser_decline_error:

                                print(
                                    "PURCHASE OFFICER MANAGER DECLINE "
                                    "EMAIL ERROR:",
                                    purchaser_email,
                                    repr(
                                        purchaser_decline_error
                                    )
                                )

                    # ==================================================
                    # SEND TO ENGINEER
                    # ==================================================

                    if approving_engineer:

                        engineer_email = (
                            approving_engineer.get("email")
                            or ""
                        ).strip()

                        approving_engineer_name = (
                            approving_engineer.get("fullname")
                            or locked_request["engineer_name"]
                            or "Engineer"
                        )

                        if engineer_email:

                            try:

                                send_email(
                                    engineer_email,

                                    (
                                        "Material Request Declined "
                                        "by Management - "
                                        f"{request_number}"
                                    ),

                                    f"""
                                    <h2>
                                        Material Request Declined
                                    </h2>

                                    <p>
                                        Hello
                                        <b>{approving_engineer_name}</b>,
                                    </p>

                                    <p>
                                        The Material Request you
                                        previously approved has been
                                        declined by Management.
                                    </p>

                                    <hr>

                                    {declined_email_html}
                                    """
                                )

                                print(
                                    "ENGINEER MANAGER DECLINE "
                                    "EMAIL SENT:",
                                    engineer_email
                                )

                            except Exception as engineer_decline_error:

                                print(
                                    "ENGINEER MANAGER DECLINE "
                                    "EMAIL ERROR:",
                                    engineer_email,
                                    repr(
                                        engineer_decline_error
                                    )
                                )

                    # ==================================================
                    # SEND TO CURRENT MANAGER
                    # ==================================================

                    if approving_manager:

                        manager_email = (
                            approving_manager.get("email")
                            or ""
                        ).strip()

                        approving_manager_name = (
                            approving_manager.get("fullname")
                            or manager_name
                        )

                        if manager_email:

                            try:

                                send_email(
                                    manager_email,

                                    (
                                        "Management Decline Recorded - "
                                        f"{request_number}"
                                    ),

                                    f"""
                                    <h2>
                                        Management Decline Recorded
                                    </h2>

                                    <p>
                                        Hello
                                        <b>{approving_manager_name}</b>,
                                    </p>

                                    <p>
                                        Your decision to decline this
                                        Material Request has been
                                        successfully recorded.
                                    </p>

                                    <hr>

                                    {declined_email_html}
                                    """
                                )

                                print(
                                    "MANAGER DECLINE EMAIL SENT:",
                                    manager_email
                                )

                            except Exception as manager_decline_error:

                                print(
                                    "MANAGER DECLINE EMAIL ERROR:",
                                    manager_email,
                                    repr(
                                        manager_decline_error
                                    )
                                )

            except Exception as email_error:

                print("=" * 80)
                print(
                    "MANAGER EMAIL NOTIFICATION PROCESS ERROR"
                )

                print(
                    "ERROR TYPE:",
                    type(email_error).__name__
                )

                print(
                    "ERROR:",
                    str(email_error)
                )

                import traceback
                traceback.print_exc()

                print("=" * 80)

            # ==================================================
            # SUCCESS FLASH
            # ==================================================

            if action == "Approved":

                flash(
                    f"Material Request "
                    f"{material_request['request_number']} "
                    "has been approved by Management.",
                    "success"
                )

            else:

                flash(
                    f"Material Request "
                    f"{material_request['request_number']} "
                    "has been declined by Management.",
                    "warning"
                )

            return redirect(
                url_for(
                    "construction_manager_dashboard"
                )
            )

        # ==================================================
        # GET MANAGER REVIEW HISTORY
        # ==================================================

        cursor.execute(
            """
            SELECT
                id,
                material_request_id,
                manager_id,
                manager_name,
                action,
                comment,
                signature_data,
                signed_at,
                created_at

            FROM
                construction_purchase_manager_reviews

            WHERE
                material_request_id = %s

            ORDER BY
                created_at DESC,
                id DESC
            """,
            (
                request_id,
            )
        )

        manager_reviews = (
            cursor.fetchall() or []
        )

        # ==================================================
        # RENDER PAGE
        # ==================================================

        return render_template(
            "construction_admin/"
            "construction_manager_material_request_review.html",

            material_request=material_request,

            manager_reviews=manager_reviews,

            manager_name=manager_name
        )

    # ======================================================
    # GENERAL ERROR
    # ======================================================

    except Exception as e:

        if conn:

            try:
                conn.rollback()
            except Exception:
                pass

        import traceback

        print("=" * 80)

        print(
            "MANAGER MATERIAL REQUEST REVIEW ERROR"
        )

        print(
            "ERROR TYPE:",
            type(e).__name__
        )

        print(
            "ERROR MESSAGE:",
            str(e)
        )

        print(
            "FULL TRACEBACK:"
        )

        traceback.print_exc()

        print("=" * 80)

        flash(
            f"Manager review error: {str(e)}",
            "danger"
        )

        return redirect(
            url_for(
                "construction_manager_dashboard"
            )
        )

    finally:

        if cursor:

            try:
                cursor.close()
            except Exception:
                pass

        if conn:

            try:
                conn.close()
            except Exception:
                pass










# ==========================================================
# MANAGER - REVIEW / SIGN PROCUREMENT
# ==========================================================

@app.route(
    "/construction/manager/procurement/<int:procurement_id>/review",
    methods=["GET", "POST"]
)
def construction_manager_procurement_review(procurement_id):

    # ======================================================
    # LOGIN PROTECTION
    # ======================================================

    if "construction_admin_id" not in session:

        flash(
            "Please sign in to access Manager Procurement Review.",
            "warning"
        )

        return redirect(
            url_for("admin_login")
        )

    # ======================================================
    # ROLE PROTECTION
    # ======================================================

    role = (
        session.get("construction_admin_role") or ""
    ).strip().lower()

    if role != "manager":

        flash(
            "You are not authorized to access Manager Procurement Review.",
            "danger"
        )

        return redirect(
            construction_role_dashboard()
        )

    # ======================================================
    # MANAGER INFORMATION
    # ======================================================

    manager_id = session.get(
        "construction_admin_id"
    )

    manager_name = (
        session.get("construction_admin_name")
        or session.get("name")
        or session.get("full_name")
        or session.get("username")
        or "Manager"
    )

    conn = None
    cursor = None

    try:

        # ==================================================
        # DATABASE CONNECTION
        # ==================================================

        conn = get_db_connection()

        cursor = conn.cursor(
            dictionary=True
        )

        # ==================================================
        # GET PROCUREMENT
        # ==================================================

        cursor.execute(
            """
            SELECT
                p.*,

                mr.request_number,
                mr.project_name,
                mr.description,
                mr.requested_by,
                mr.requested_by_name,
                mr.request_date,
                mr.engineer_id,
                mr.engineer_name,
                mr.engineer_reviewed_at,
                mr.engineer_comment,
                mr.status AS material_request_status

            FROM
                construction_purchase_procurement p

            INNER JOIN
                construction_purchase_material_requests mr
                ON mr.id = p.material_request_id

            WHERE
                p.id = %s

            LIMIT 1
            """,
            (
                procurement_id,
            )
        )

        procurement = cursor.fetchone()

        # ==================================================
        # PROCUREMENT NOT FOUND
        # ==================================================

        if not procurement:

            flash(
                "Procurement record was not found.",
                "danger"
            )

            return redirect(
                url_for(
                    "construction_manager_dashboard"
                )
            )

        # ==================================================
        # ONLY PENDING MANAGER REVIEW CAN BE PROCESSED
        # ==================================================

        procurement_status = (
            procurement.get("status") or ""
        ).strip()

        procurement_stage = (
            procurement.get("workflow_stage") or ""
        ).strip()

        if not (
            procurement_status == "Pending Manager Review"
            and procurement_stage == "Manager Review"
        ):

            flash(
                "This procurement is not currently awaiting Manager review.",
                "warning"
            )

            return redirect(
                url_for(
                    "construction_manager_dashboard"
                )
            )

        # ==================================================
        # GET PROCUREMENT DOCUMENTS
        # ==================================================

        cursor.execute(
            """
            SELECT
                id,
                procurement_id,
                document_type,
                workflow_stage,
                document_title,
                original_file_name,
                file_url,
                public_id,
                uploaded_by,
                uploaded_by_name,
                uploaded_at,
                notes

            FROM
                construction_purchase_procurement_documents

            WHERE
                procurement_id = %s

            ORDER BY
                uploaded_at ASC,
                id ASC
            """,
            (
                procurement_id,
            )
        )

        documents = cursor.fetchall() or []

        # ==================================================
        # FIND ACCOUNT-STAMPED DOCUMENTS
        # ==================================================

        quotation_document = None
        lpo_document = None
        card_payment_evidence = None

        for document in documents:

            workflow_stage = (
                document.get("workflow_stage") or ""
            ).strip().lower()

            if workflow_stage != "account stamp":
                continue

            document_type = (
                document.get("document_type") or ""
            ).strip().lower()

            if document_type == "quotation":

                if quotation_document is None:
                    quotation_document = document

            elif document_type == "lpo":

                if lpo_document is None:
                    lpo_document = document

            elif document_type == "card payment evidence":

                if card_payment_evidence is None:
                    card_payment_evidence = document

        # ==================================================
        # DETERMINE PROCUREMENT PACKAGE COMPLETENESS
        # ==================================================

        procurement_method = (
            procurement.get("procurement_method") or ""
        ).strip()

        package_complete = True
        missing_documents = []

        if procurement_method == "Quotation":

            if not quotation_document:

                package_complete = False

                missing_documents.append(
                    "Account-stamped Quotation"
                )

            if not lpo_document:

                package_complete = False

                missing_documents.append(
                    "Account-stamped LPO"
                )

        elif procurement_method == "Card":

            if not card_payment_evidence:

                package_complete = False

                missing_documents.append(
                    "Account-stamped Card Payment Evidence"
                )

        else:

            package_complete = False

            missing_documents.append(
                "Valid procurement method"
            )

        # ==================================================
        # POST REQUEST
        # ==================================================

        if request.method == "POST":

            # ==================================================
            # PACKAGE VALIDATION
            # ==================================================

            if not package_complete:

                flash(
                    "This procurement package is incomplete. "
                    "Missing: "
                    + ", ".join(missing_documents),
                    "danger"
                )

                return redirect(
                    url_for(
                        "construction_manager_procurement_review",
                        procurement_id=procurement_id
                    )
                )

            # ==================================================
            # SIGNATURE VALIDATION
            # ==================================================

            signature_data = (
                request.form.get("signature_data") or ""
            ).strip()

            if not signature_data:

                flash(
                    "Manager digital signature is required.",
                    "danger"
                )

                return redirect(
                    url_for(
                        "construction_manager_procurement_review",
                        procurement_id=procurement_id
                    )
                )

            # ==================================================
            # LOCK PROCUREMENT
            # ==================================================

            cursor.execute(
                """
                SELECT
                    *

                FROM
                    construction_purchase_procurement

                WHERE
                    id = %s

                FOR UPDATE
                """,
                (
                    procurement_id,
                )
            )

            locked_procurement = cursor.fetchone()

            if not locked_procurement:

                conn.rollback()

                flash(
                    "Procurement record no longer exists.",
                    "danger"
                )

                return redirect(
                    url_for(
                        "construction_manager_dashboard"
                    )
                )

            # ==================================================
            # CHECK STATUS AGAIN
            # ==================================================

            locked_status = (
                locked_procurement.get("status") or ""
            ).strip()

            locked_stage = (
                locked_procurement.get("workflow_stage") or ""
            ).strip()

            if not (
                locked_status == "Pending Manager Review"
                and locked_stage == "Manager Review"
            ):

                conn.rollback()

                flash(
                    "This procurement has already been processed.",
                    "warning"
                )

                return redirect(
                    url_for(
                        "construction_manager_dashboard"
                    )
                )

            # ==================================================
            # GET LATEST ACCOUNT-STAMPED DOCUMENTS
            # ==================================================

            cursor.execute(
                """
                SELECT
                    id,
                    procurement_id,
                    document_type,
                    workflow_stage,
                    document_title,
                    original_file_name,
                    file_url,
                    public_id,
                    uploaded_by,
                    uploaded_by_name,
                    uploaded_at,
                    notes

                FROM
                    construction_purchase_procurement_documents

                WHERE
                    procurement_id = %s

                ORDER BY
                    uploaded_at ASC,
                    id ASC
                """,
                (
                    procurement_id,
                )
            )

            locked_documents = cursor.fetchall() or []

            documents_to_sign = []

            latest_quotation = None
            latest_lpo = None
            latest_card_evidence = None

            for document in locked_documents:

                workflow_stage = (
                    document.get("workflow_stage") or ""
                ).strip().lower()

                if workflow_stage != "account stamp":
                    continue

                document_type = (
                    document.get("document_type") or ""
                ).strip().lower()

                if document_type == "quotation":

                    latest_quotation = document

                elif document_type == "lpo":

                    latest_lpo = document

                elif document_type == "card payment evidence":

                    latest_card_evidence = document

            # ==================================================
            # QUOTATION PROCUREMENT
            # ==================================================

            if procurement_method == "Quotation":

                if latest_quotation:

                    documents_to_sign.append(
                        latest_quotation
                    )

                if latest_lpo:

                    documents_to_sign.append(
                        latest_lpo
                    )

            # ==================================================
            # CARD PROCUREMENT
            # ==================================================

            elif procurement_method == "Card":

                if latest_card_evidence:

                    documents_to_sign.append(
                        latest_card_evidence
                    )

            # ==================================================
            # FINAL DOCUMENT VALIDATION
            # ==================================================

            if not documents_to_sign:

                conn.rollback()

                flash(
                    "No Account-stamped PDF documents were found "
                    "for Manager signing.",
                    "danger"
                )

                return redirect(
                    url_for(
                        "construction_manager_procurement_review",
                        procurement_id=procurement_id
                    )
                )

            # ==================================================
            # SIGN EACH ACCOUNT-STAMPED PDF
            # ==================================================

            stamped_count = 0

            for document in documents_to_sign:

                file_url = (
                    document.get("file_url") or ""
                ).strip()

                original_file_name = (
                    document.get("original_file_name")
                    or document.get("document_title")
                    or "procurement_document.pdf"
                )

                if not file_url:

                    print(
                        "MANAGER PROCUREMENT SIGNING - "
                        "MISSING FILE URL:",
                        original_file_name
                    )

                    continue

                # ==================================================
                # DOWNLOAD ACCOUNT-STAMPED PDF
                # ==================================================

                try:

                    pdf_response = requests.get(
                        file_url,
                        timeout=60
                    )

                    pdf_response.raise_for_status()

                    account_stamped_pdf_bytes = (
                        pdf_response.content
                    )

                    if not account_stamped_pdf_bytes:

                        raise ValueError(
                            "Downloaded PDF is empty."
                        )

                except Exception as download_error:

                    print("=" * 80)

                    print(
                        "MANAGER PROCUREMENT PDF DOWNLOAD ERROR"
                    )

                    print(
                        "FILE:",
                        original_file_name
                    )

                    print(
                        "ERROR:",
                        repr(download_error)
                    )

                    print("=" * 80)

                    continue

                # ==================================================
                # ADD MANAGER SIGNATURE
                # ==================================================

                try:

                    manager_signed_pdf = create_signed_pdf(
                        pdf_bytes=account_stamped_pdf_bytes,
                        signature_data=signature_data,
                        signer_name=manager_name,
                        department_title="MANAGEMENT DEPARTMENT",
                        position="left"
                    )

                    if not manager_signed_pdf:

                        raise ValueError(
                            "Manager signed PDF is empty."
                        )

                except Exception as stamp_error:

                    print("=" * 80)

                    print(
                        "MANAGER PROCUREMENT PDF STAMP ERROR"
                    )

                    print(
                        "FILE:",
                        original_file_name
                    )

                    print(
                        "ERROR:",
                        repr(stamp_error)
                    )

                    print("=" * 80)

                    continue

                # ==================================================
                # SAFE FILE NAME
                # ==================================================

                base_name = os.path.splitext(
                    original_file_name
                )[0]

                safe_base_name = (
                    base_name
                    .replace(" ", "_")
                    .replace("/", "_")
                    .replace("\\", "_")
                )

                manager_signed_file_name = (
                    f"{safe_base_name}_manager_signed.pdf"
                )

                manager_signed_file = BytesIO(
                    manager_signed_pdf
                )

                manager_signed_file.name = (
                    manager_signed_file_name
                )

                # ==================================================
                # CLOUDINARY
                # ==================================================

                cloudinary_folder = (
                    "prestigious_construction/"
                    "purchase/procurement_stamped/"
                    f"procurement_{procurement_id}"
                )

                try:

                    upload_result = (
                        cloudinary.uploader.upload(
                            manager_signed_file,
                            resource_type="image",
                            folder=cloudinary_folder,
                            public_id=os.path.splitext(
                                manager_signed_file_name
                            )[0],
                            format="pdf",
                            overwrite=True
                        )
                    )

                except Exception as upload_error:

                    print("=" * 80)

                    print(
                        "MANAGER PROCUREMENT CLOUDINARY ERROR"
                    )

                    print(
                        "FILE:",
                        manager_signed_file_name
                    )

                    print(
                        "ERROR:",
                        repr(upload_error)
                    )

                    print("=" * 80)

                    continue

                manager_signed_url = (
                    upload_result.get("secure_url")
                    or upload_result.get("url")
                )

                manager_signed_public_id = (
                    upload_result.get("public_id")
                )

                if not manager_signed_url:

                    print(
                        "MANAGER PROCUREMENT CLOUDINARY "
                        "DID NOT RETURN URL:",
                        manager_signed_file_name
                    )

                    continue

                # ==================================================
                # SAVE MANAGER-SIGNED DOCUMENT
                # ==================================================

                manager_document_title = (
                    document.get("document_title")
                    or original_file_name
                )

                manager_document_title = (
                    f"{manager_document_title}"
                    " - Manager Signed"
                )

                cursor.execute(
                    """
                    INSERT INTO
                    construction_purchase_procurement_documents
                    (
                        procurement_id,
                        document_type,
                        workflow_stage,
                        document_title,
                        original_file_name,
                        file_url,
                        public_id,
                        uploaded_by,
                        uploaded_by_name,
                        uploaded_at,
                        notes
                    )
                    VALUES
                    (
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        NOW(),
                        %s
                    )
                    """,
                    (
                        procurement_id,
                        document.get("document_type"),
                        "Manager Signed",
                        manager_document_title,
                        manager_signed_file_name,
                        manager_signed_url,
                        manager_signed_public_id,
                        manager_id,
                        manager_name,
                        "Digitally signed by Management Department."
                    )
                )

                stamped_count += 1

            # ==================================================
            # VERIFY ALL REQUIRED DOCUMENTS WERE SIGNED
            # ==================================================

            if stamped_count != len(documents_to_sign):

                conn.rollback()

                flash(
                    "The Manager could not digitally sign all required "
                    "procurement documents. No workflow status change "
                    "was made.",
                    "danger"
                )

                return redirect(
                    url_for(
                        "construction_manager_procurement_review",
                        procurement_id=procurement_id
                    )
                )

            # ==================================================
            # IMPORTANT:
            #
            # MANAGER SIGNING DOES NOT COMPLETE PROCUREMENT.
            #
            # NEXT STAGE:
            #
            # Pending Final Procurement
            #
            # Purchase will now:
            #
            # 1. Go to the market
            # 2. Purchase the materials
            # 3. Return to the system
            # 4. Upload receipt/invoice/payment evidence
            # 5. Select Card or Cheque
            # 6. Enter payment details
            # 7. Submit final procurement
            #
            # ONLY THEN -> Completed
            # ==================================================

            cursor.execute(
                """
                UPDATE
                    construction_purchase_procurement

                SET
                    status = 'Pending Final Procurement',
                    workflow_stage = 'Final Procurement',

                    manager_reviewed_at = NOW(),
                    manager_reviewer_id = %s,
                    manager_reviewer_name = %s,
                    manager_signature_data = %s,
                    manager_signed_at = NOW(),

                    updated_at = NOW()

                WHERE
                    id = %s

                    AND status = 'Pending Manager Review'

                    AND workflow_stage = 'Manager Review'
                """,
                (
                    manager_id,
                    manager_name,
                    signature_data,
                    procurement_id
                )
            )

            if cursor.rowcount != 1:

                conn.rollback()

                flash(
                    "The procurement could not be moved to "
                    "Final Procurement.",
                    "danger"
                )

                return redirect(
                    url_for(
                        "construction_manager_dashboard"
                    )
                )

            # ==================================================
            # PROCUREMENT AUDIT LOG
            # ==================================================

            _construction_log_procurement_action(
                cursor=cursor,
                procurement_id=procurement_id,
                action_type="MANAGER_PROCUREMENT_APPROVED",
                action_description=(
                    "Manager reviewed and digitally signed the "
                    "Account-stamped procurement package. "
                    "The procurement is now awaiting final purchase "
                    "and payment evidence from Purchase."
                ),
                from_status=locked_status,
                to_status="Pending Final Procurement",
                performed_by=manager_id,
                performed_by_name=manager_name,
                notes=(
                    f"{stamped_count} procurement document(s) "
                    "digitally signed by Management. "
                    "Purchase must now complete the final procurement."
                )
            )

            # ==================================================
            # COMMIT DATABASE CHANGES
            # ==================================================

            conn.commit()

            # ==================================================
            # EMAIL NOTIFICATION
            #
            # THIS IS NO LONGER A COMPLETION EMAIL.
            #
            # IT TELLS PURCHASE THAT FINAL PROCUREMENT
            # IS READY.
            # ==================================================

            try:

                request_number = (
                    procurement.get("request_number")
                    or f"PROCUREMENT-{procurement_id}"
                )

                project_name = (
                    procurement.get("project_name")
                    or "N/A"
                )

                requester_name = (
                    procurement.get("requested_by_name")
                    or "Purchase Officer"
                )

                request_date = (
                    procurement.get("request_date")
                    or "N/A"
                )

                description = (
                    procurement.get("description")
                    or "No description provided"
                )

                procurement_method = (
                    procurement.get("procurement_method")
                    or "N/A"
                )

                approval_date = get_qatar_now()

                approval_date_text = (
                    approval_date.strftime(
                        "%Y-%m-%d %H:%M AST"
                    )
                )

                # ==================================================
                # GET MANAGER-SIGNED DOCUMENTS
                # ==================================================

                notification_cursor = conn.cursor(
                    dictionary=True
                )

                notification_cursor.execute(
                    """
                    SELECT
                        document_type,
                        document_title,
                        original_file_name,
                        file_url,
                        uploaded_at

                    FROM
                        construction_purchase_procurement_documents

                    WHERE
                        procurement_id = %s

                        AND LOWER(
                            TRIM(workflow_stage)
                        ) = 'manager signed'

                    ORDER BY
                        uploaded_at DESC,
                        id DESC
                    """,
                    (
                        procurement_id,
                    )
                )

                manager_documents = (
                    notification_cursor.fetchall()
                    or []
                )

                notification_cursor.close()

                # ==================================================
                # DOCUMENT BUTTONS
                # ==================================================

                document_buttons = ""

                seen_document_types = set()

                for document in manager_documents:

                    document_type = (
                        document.get("document_type")
                        or ""
                    ).strip().lower()

                    document_url = (
                        document.get("file_url")
                        or ""
                    ).strip()

                    if not document_url:
                        continue

                    if document_type in seen_document_types:
                        continue

                    seen_document_types.add(
                        document_type
                    )

                    if document_type == "quotation":

                        button_title = (
                            "Open Manager-Signed Quotation"
                        )

                    elif document_type == "lpo":

                        button_title = (
                            "Open Manager-Signed LPO"
                        )

                    elif (
                        document_type
                        == "card payment evidence"
                    ):

                        button_title = (
                            "Open Manager-Signed Card Evidence"
                        )

                    else:

                        button_title = (
                            "Open Manager-Signed Document"
                        )

                    document_buttons += f"""
                    <p style="margin-top:12px;">
                        <a
                            href="{document_url}"
                            target="_blank"
                            rel="noopener noreferrer"
                            style="
                                display:inline-block;
                                padding:11px 20px;
                                background:#8A1538;
                                color:#ffffff;
                                text-decoration:none;
                                border-radius:5px;
                                font-weight:bold;
                            "
                        >
                            {button_title}
                        </a>
                    </p>
                    """

                # ==================================================
                # FINAL PROCUREMENT EMAIL
                # ==================================================

                final_procurement_email_html = f"""
                <h2>
                    Final Procurement Required
                </h2>

                <p>
                    The procurement package has been reviewed and
                    digitally signed by Management.
                </p>

                <p>
                    The procurement is now waiting for the
                    <b>Purchase Department</b> to complete the
                    actual purchase and final payment documentation.
                </p>

                <hr>

                <h3>
                    Procurement Details
                </h3>

                <p>
                    <b>Request Number:</b>
                    {request_number}
                </p>

                <p>
                    <b>Project:</b>
                    {project_name}
                </p>

                <p>
                    <b>Requested By:</b>
                    {requester_name}
                </p>

                <p>
                    <b>Request Date:</b>
                    {request_date}
                </p>

                <p>
                    <b>Procurement Method:</b>
                    {procurement_method}
                </p>

                <p>
                    <b>Description:</b>
                    {description}
                </p>

                <p>
                    <b>Account Review:</b>
                    Completed
                </p>

                <p>
                    <b>Management Review:</b>
                    Completed
                </p>

                <p>
                    <b>Manager:</b>
                    {manager_name}
                </p>

                <p>
                    <b>Manager Review Date:</b>
                    {approval_date_text}
                </p>

                <p>
                    <b>Current Status:</b>
                    <span style="
                        color:#8A1538;
                        font-weight:bold;
                    ">
                        Pending Final Procurement
                    </span>
                </p>

                <hr>

                <h3>
                    Next Action Required
                </h3>

                <p>
                    Purchase must now access the Construction
                    Management System and click
                    <b>Final Procurement</b>.
                </p>

                <p>
                    Purchase should then:
                </p>

                <ol>
                    <li>Proceed with the actual market purchase.</li>
                    <li>Return to the system after purchase.</li>
                    <li>Upload the receipt, invoice or payment evidence.</li>
                    <li>Select the final payment method: Card or Cheque.</li>
                    <li>Enter the required payment details.</li>
                    <li>Submit the final procurement.</li>
                </ol>

                <p>
                    The procurement will only become
                    <b>Completed</b> after Purchase submits
                    the final procurement documentation.
                </p>

                <hr>

                <h3>
                    Manager-Signed Procurement Documents
                </h3>

                <p>
                    The digitally signed documents are available
                    below for reference during the final purchase.
                </p>

                {document_buttons}

                <hr>

                <p>
                    Regards,<br>
                    <b>
                        Prestigious Trading & Construction W.L.L.
                    </b>
                </p>

                <p>
                    <small>
                        This notification was generated automatically
                        by the Construction Management System.
                    </small>
                </p>
                """

                # ==================================================
                # GET ALL DEPARTMENT USERS
                # ==================================================

                notification_cursor = conn.cursor(
                    dictionary=True
                )

                notification_cursor.execute(
                    """
                    SELECT
                        id,
                        fullname,
                        email,
                        role

                    FROM
                        construction_admins

                    WHERE
                        LOWER(TRIM(role)) IN
                        (
                            'purchase',
                            'account',
                            'engineer',
                            'manager'
                        )

                        AND email IS NOT NULL

                        AND TRIM(email) <> ''

                    ORDER BY
                        FIELD(
                            LOWER(TRIM(role)),
                            'purchase',
                            'account',
                            'engineer',
                            'manager'
                        ),

                        fullname ASC
                    """
                )

                department_users = (
                    notification_cursor.fetchall()
                    or []
                )

                notification_cursor.close()

                # ==================================================
                # PREVENT DUPLICATE EMAILS
                # ==================================================

                sent_email_addresses = set()

                # ==================================================
                # SEND EMAIL
                # ==================================================

                for department_user in department_users:

                    recipient_email = (
                        department_user.get("email")
                        or ""
                    ).strip()

                    recipient_name = (
                        department_user.get("fullname")
                        or "Department User"
                    )

                    recipient_role = (
                        department_user.get("role")
                        or ""
                    ).strip().lower()

                    if not recipient_email:
                        continue

                    if recipient_email.lower() in sent_email_addresses:
                        continue

                    # ==================================================
                    # DEPARTMENT TITLE
                    # ==================================================

                    if recipient_role == "purchase":

                        department_title = "Purchase Department"

                    elif recipient_role == "account":

                        department_title = "Account Department"

                    elif recipient_role == "engineer":

                        department_title = "Engineering Department"

                    elif recipient_role == "manager":

                        department_title = "Management Department"

                    else:

                        department_title = "Construction Department"

                    # ==================================================
                    # EMAIL SUBJECT
                    # ==================================================

                    email_subject = (
                        "Final Procurement Required - "
                        f"{request_number}"
                    )

                    # ==================================================
                    # PERSONALIZED EMAIL
                    # ==================================================

                    department_email_html = f"""
                    <p>
                        Hello
                        <b>{recipient_name}</b>,
                    </p>

                    {final_procurement_email_html}

                    <p>
                        <b>Notification Department:</b>
                        {department_title}
                    </p>
                    """

                    # ==================================================
                    # SEND THROUGH BREVO
                    # ==================================================

                    try:

                        email_result = send_email(
                            recipient_email,
                            email_subject,
                            department_email_html
                        )

                        if email_result:

                            sent_email_addresses.add(
                                recipient_email.lower()
                            )

                            print(
                                "FINAL PROCUREMENT EMAIL SENT TO:",
                                department_title,
                                recipient_name,
                                recipient_email
                            )

                        else:

                            print(
                                "FINAL PROCUREMENT EMAIL FAILED:",
                                department_title,
                                recipient_name,
                                recipient_email
                            )

                    except Exception as email_error:

                        print("=" * 80)

                        print(
                            "FINAL PROCUREMENT DEPARTMENT EMAIL ERROR"
                        )

                        print(
                            "DEPARTMENT:",
                            department_title
                        )

                        print(
                            "NAME:",
                            recipient_name
                        )

                        print(
                            "EMAIL:",
                            recipient_email
                        )

                        print(
                            "ERROR:",
                            repr(email_error)
                        )

                        print("=" * 80)

                # ==================================================
                # EMAIL SUMMARY
                # ==================================================

                print("=" * 80)

                print(
                    "FINAL PROCUREMENT EMAIL PROCESS FINISHED"
                )

                print(
                    "REQUEST:",
                    request_number
                )

                print(
                    "TOTAL DEPARTMENT USERS FOUND:",
                    len(department_users)
                )

                print(
                    "UNIQUE EMAILS SUCCESSFULLY SENT:",
                    len(sent_email_addresses)
                )

                print("=" * 80)

            except Exception as email_process_error:

                print("=" * 80)

                print(
                    "FINAL PROCUREMENT EMAIL PROCESS ERROR"
                )

                print(
                    "ERROR TYPE:",
                    type(email_process_error).__name__
                )

                print(
                    "ERROR:",
                    repr(email_process_error)
                )

                import traceback

                traceback.print_exc()

                print("=" * 80)

            # ==================================================
            # SUCCESS
            # ==================================================

            flash(
                f"Procurement {request_number} has been digitally "
                "signed by Management and moved to Pending Final "
                "Procurement. Purchase can now proceed with the "
                "actual purchase and submit the final payment "
                "evidence.",
                "success"
            )

            return redirect(
                url_for(
                    "construction_manager_dashboard"
                )
            )

        # ==================================================
        # GET REQUEST
        # ==================================================

        return render_template(
            "construction_admin/"
            "construction_manager_procurement_review.html",

            procurement=procurement,

            quotation_document=quotation_document,

            lpo_document=lpo_document,

            card_payment_evidence=card_payment_evidence,

            documents=documents,

            manager_name=manager_name
        )

    # ======================================================
    # GENERAL ERROR
    # ======================================================

    except Exception as e:

        if conn:

            try:
                conn.rollback()
            except Exception:
                pass

        import traceback

        print("=" * 80)

        print(
            "MANAGER PROCUREMENT REVIEW ERROR"
        )

        print(
            "ERROR TYPE:",
            type(e).__name__
        )

        print(
            "ERROR MESSAGE:",
            str(e)
        )

        traceback.print_exc()

        print("=" * 80)

        flash(
            f"Manager procurement review error: {str(e)}",
            "danger"
        )

        return redirect(
            url_for(
                "construction_manager_dashboard"
            )
        )

    finally:

        if cursor:

            try:
                cursor.close()
            except Exception:
                pass

        if conn:

            try:
                conn.close()
            except Exception:
                pass
















# =========================================================
# CONSTRUCTION ACCOUNT DASHBOARD
# =========================================================
@app.route("/construction/account/dashboard")
def construction_account_dashboard():

    # -----------------------------------------------------
    # LOGIN PROTECTION
    # -----------------------------------------------------
    if "construction_admin_id" not in session:
        return redirect(url_for("admin_login"))

    # -----------------------------------------------------
    # ROLE PROTECTION
    # -----------------------------------------------------
    role = (
        session.get("construction_admin_role") or ""
    ).strip().lower()

    if role != "account":
        flash(
            "You are not authorized to access the Account dashboard.",
            "danger"
        )
        return redirect(url_for("admin_login"))

    conn = None
    cursor = None

    material_requests = []

    # -----------------------------------------------------
    # DIRECT CARD PROCUREMENT
    # -----------------------------------------------------
    card_procurements = []
    card_documents_submitted_count = 0

    # -----------------------------------------------------
    # QUOTATION / LPO PROCUREMENT
    # -----------------------------------------------------
    quotation_procurements = []
    quotation_pending_count = 0

    # -----------------------------------------------------
    # COMPLETED QUOTATION / LPO PROCUREMENT
    # -----------------------------------------------------
    completed_quotation_procurements = []
    quotation_completed_count = 0

    # -----------------------------------------------------
    # FINAL PROCUREMENT
    # -----------------------------------------------------
    final_procurements = []
    final_documents_count = 0

    # -----------------------------------------------------
    # SUMMARY
    # -----------------------------------------------------
    manager_approved_requests = 0

    # -----------------------------------------------------
    # PETTY CASH
    # -----------------------------------------------------
    petty_cash_transactions = []
    petty_cash_count = 0
    petty_cash_total = 0

    try:

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        # =================================================
        # COUNT MANAGER-APPROVED MATERIAL REQUESTS
        # =================================================
        cursor.execute("""
            SELECT
                COUNT(*) AS manager_approved_requests
            FROM construction_purchase_material_requests
            WHERE status = 'Approved'
        """)

        counts = cursor.fetchone() or {}

        manager_approved_requests = (
            counts.get("manager_approved_requests") or 0
        )

        # =================================================
        # LOAD MANAGER-APPROVED MATERIAL REQUESTS
        # =================================================
        cursor.execute("""
            SELECT
                id,
                request_number,
                project_name,
                requested_by,
                requested_by_name,
                request_date,
                description,
                original_file_name,
                original_file_url,
                original_public_id,
                signed_file_name,
                signed_file_url,
                signed_public_id,
                purchase_signature_data,
                purchase_signed_at,
                status,
                engineer_id,
                engineer_name,
                engineer_reviewed_at,
                engineer_comment,
                manager_id,
                manager_name,
                manager_approved_at,
                manager_comment,
                procurement_method,
                created_at,
                updated_at
            FROM construction_purchase_material_requests
            WHERE status = 'Approved'
            ORDER BY
                manager_approved_at DESC,
                created_at DESC,
                id DESC
        """)

        material_requests = cursor.fetchall() or []

        # =================================================
        # PETTY CASH SUMMARY
        # =================================================
        cursor.execute("""
            SELECT
                COUNT(*) AS total_count,
                COALESCE(SUM(amount), 0) AS total_amount
            FROM construction_account_petty_cash_transactions
            WHERE status <> 'Rejected'
        """)

        petty_cash_summary = cursor.fetchone() or {}

        petty_cash_count = (
            petty_cash_summary.get("total_count") or 0
        )

        petty_cash_total = (
            petty_cash_summary.get("total_amount") or 0
        )

        # =================================================
        # LOAD PETTY CASH TRANSACTIONS
        # =================================================
        cursor.execute("""
            SELECT
                id,
                transaction_number,
                transaction_datetime,
                recipient_name,
                project_name,
                amount,
                currency,
                reference_number,
                receipt_original_name,
                receipt_file_url,
                status,
                recorded_by,
                recorded_by_name,
                recorded_at
            FROM construction_account_petty_cash_transactions
            ORDER BY
                transaction_datetime DESC,
                id DESC
            LIMIT 100
        """)

        petty_cash_transactions = cursor.fetchall() or []

        # =================================================
        # DIRECT CARD PROCUREMENT COUNT
        #
        # UNCHANGED
        #
        # Includes:
        # 1. Documents Submitted to Account
        # 2. Automatically completed Direct Card records
        #
        # NO CASH.
        # NO QUOTATION/LPO RECORDS.
        # =================================================
        cursor.execute("""
            SELECT
                COUNT(*) AS total
            FROM construction_purchase_procurement
            WHERE procurement_method = 'Card'
              AND (
                    status = 'Documents Submitted to Account'
                    OR (
                        status = 'Completed'
                        AND workflow_stage = 'Completed'
                    )
                  )
        """)

        card_count = cursor.fetchone() or {}

        card_documents_submitted_count = (
            card_count.get("total") or 0
        )

        # =================================================
        # LOAD DIRECT CARD PROCUREMENTS
        #
        # THIS WORKFLOW IS LEFT SEPARATE.
        # =================================================
        cursor.execute("""
            SELECT
                p.id,
                p.material_request_id,
                p.procurement_method,
                p.workflow_stage,
                p.initiated_by,
                p.initiated_by_name,
                p.initiated_at,
                p.status,
                p.notes,
                p.supplier_name,
                p.supplier_contact,
                p.procurement_amount,
                p.currency,
                p.payment_date,
                p.card_paid_by,
                p.card_paid_by_name,
                p.card_paid_at,
                p.card_payment_reference,
                p.created_at,
                p.updated_at,

                mr.request_number,
                mr.project_name,
                mr.requested_by,
                mr.requested_by_name,
                mr.request_date,
                mr.description

            FROM construction_purchase_procurement p

            INNER JOIN construction_purchase_material_requests mr
                ON mr.id = p.material_request_id

            WHERE p.procurement_method = 'Card'
              AND (
                    p.status = 'Documents Submitted to Account'
                    OR (
                        p.status = 'Completed'
                        AND p.workflow_stage = 'Completed'
                    )
                  )

            ORDER BY
                p.updated_at DESC,
                p.id DESC
        """)

        card_procurements = cursor.fetchall() or []

        # =================================================
        # LOAD DOCUMENTS FOR DIRECT CARD PROCUREMENT
        # =================================================
        if card_procurements:

            procurement_ids = [
                procurement["id"]
                for procurement in card_procurements
                if procurement.get("id") is not None
            ]

            if procurement_ids:

                placeholders = ", ".join(
                    ["%s"] * len(procurement_ids)
                )

                document_query = f"""
                    SELECT
                        d.id,
                        d.procurement_id,
                        d.document_type,
                        d.workflow_stage,
                        d.document_title,
                        d.original_file_name,
                        d.file_url,
                        d.public_id,
                        d.uploaded_by,
                        d.uploaded_by_name,
                        d.uploaded_at,
                        d.notes
                    FROM construction_purchase_procurement_documents d
                    WHERE d.procurement_id IN ({placeholders})
                    ORDER BY
                        d.uploaded_at DESC,
                        d.id DESC
                """

                cursor.execute(
                    document_query,
                    tuple(procurement_ids)
                )

                card_documents = cursor.fetchall() or []

                documents_by_procurement = {}

                for document in card_documents:

                    procurement_id = document.get(
                        "procurement_id"
                    )

                    if procurement_id not in documents_by_procurement:

                        documents_by_procurement[
                            procurement_id
                        ] = []

                    documents_by_procurement[
                        procurement_id
                    ].append(document)

                for procurement in card_procurements:

                    procurement_id = procurement.get("id")

                    documents = (
                        documents_by_procurement.get(
                            procurement_id,
                            []
                        )
                    )

                    procurement["documents"] = documents

                    procurement["document_count"] = len(
                        documents
                    )

                    procurement["latest_document"] = (
                        documents[0]
                        if documents
                        else None
                    )

        # =================================================
        # QUOTATION / LPO COUNT
        # PENDING ACCOUNT REVIEW
        #
        # UNCHANGED
        # =================================================
        cursor.execute("""
            SELECT
                COUNT(*) AS total
            FROM construction_purchase_procurement
            WHERE procurement_method = 'Quotation'
              AND status = 'Pending Account Review'
        """)

        quotation_count = cursor.fetchone() or {}

        quotation_pending_count = (
            quotation_count.get("total") or 0
        )

        # =================================================
        # LOAD QUOTATION / LPO PROCUREMENT
        # PENDING ACCOUNT REVIEW
        #
        # UNCHANGED
        # =================================================
        cursor.execute("""
            SELECT
                p.id,
                p.material_request_id,
                p.procurement_method,
                p.workflow_stage,
                p.initiated_by,
                p.initiated_by_name,
                p.initiated_at,
                p.status,
                p.notes,
                p.supplier_name,
                p.supplier_contact,
                p.procurement_amount,
                p.currency,
                p.quotation_number,
                p.quotation_date,
                p.lpo_number,
                p.pre_purchase_submitted_by,
                p.pre_purchase_submitted_by_name,
                p.pre_purchase_submitted_at,
                p.created_at,
                p.updated_at,

                mr.request_number,
                mr.project_name,
                mr.requested_by,
                mr.requested_by_name,
                mr.request_date,
                mr.description,
                mr.manager_id,
                mr.manager_name,
                mr.manager_approved_at,
                mr.manager_comment

            FROM construction_purchase_procurement p

            INNER JOIN construction_purchase_material_requests mr
                ON mr.id = p.material_request_id

            WHERE p.procurement_method = 'Quotation'
              AND p.status = 'Pending Account Review'

            ORDER BY
                p.updated_at DESC,
                p.id DESC
        """)

        quotation_procurements = cursor.fetchall() or []

        # =================================================
        # LOAD DOCUMENTS FOR PENDING QUOTATION PACKAGES
        # =================================================
        if quotation_procurements:

            quotation_procurement_ids = [
                procurement["id"]
                for procurement in quotation_procurements
                if procurement.get("id") is not None
            ]

            if quotation_procurement_ids:

                placeholders = ", ".join(
                    ["%s"] * len(
                        quotation_procurement_ids
                    )
                )

                quotation_document_query = f"""
                    SELECT
                        d.id,
                        d.procurement_id,
                        d.document_type,
                        d.workflow_stage,
                        d.document_title,
                        d.original_file_name,
                        d.file_url,
                        d.public_id,
                        d.uploaded_by,
                        d.uploaded_by_name,
                        d.uploaded_at,
                        d.notes
                    FROM construction_purchase_procurement_documents d
                    WHERE d.procurement_id IN ({placeholders})
                    ORDER BY
                        d.uploaded_at DESC,
                        d.id DESC
                """

                cursor.execute(
                    quotation_document_query,
                    tuple(quotation_procurement_ids)
                )

                quotation_documents = (
                    cursor.fetchall() or []
                )

                quotation_documents_by_procurement = {}

                for document in quotation_documents:

                    procurement_id = document.get(
                        "procurement_id"
                    )

                    if procurement_id not in (
                        quotation_documents_by_procurement
                    ):

                        quotation_documents_by_procurement[
                            procurement_id
                        ] = []

                    quotation_documents_by_procurement[
                        procurement_id
                    ].append(document)

                for procurement in quotation_procurements:

                    procurement_id = procurement.get("id")

                    documents = (
                        quotation_documents_by_procurement.get(
                            procurement_id,
                            []
                        )
                    )

                    procurement["documents"] = documents

                    procurement["document_count"] = len(
                        documents
                    )

                    quotation_document = None
                    lpo_document = None
                    supporting_documents = []

                    for document in documents:

                        document_type = (
                            document.get("document_type") or ""
                        ).strip().lower()

                        workflow_stage = (
                            document.get("workflow_stage") or ""
                        ).strip().lower()

                        if workflow_stage == "account stamp":

                            if document_type == "quotation":

                                if quotation_document is None:
                                    quotation_document = document

                            elif document_type == "lpo":

                                if lpo_document is None:
                                    lpo_document = document

                            elif (
                                document_type
                                == "other supporting document"
                            ):

                                supporting_documents.append(
                                    document
                                )

                        elif workflow_stage == "":

                            if document_type == "quotation":

                                if quotation_document is None:
                                    quotation_document = document

                            elif document_type == "lpo":

                                if lpo_document is None:
                                    lpo_document = document

                            elif (
                                document_type
                                == "other supporting document"
                            ):

                                supporting_documents.append(
                                    document
                                )

                    if (
                        quotation_document is None
                        or lpo_document is None
                    ):

                        for document in documents:

                            document_type = (
                                document.get(
                                    "document_type"
                                ) or ""
                            ).strip().lower()

                            workflow_stage = (
                                document.get(
                                    "workflow_stage"
                                ) or ""
                            ).strip().lower()

                            if (
                                document_type == "quotation"
                                and workflow_stage
                                != "manager signed"
                            ):

                                if quotation_document is None:
                                    quotation_document = document

                            elif (
                                document_type == "lpo"
                                and workflow_stage
                                != "manager signed"
                            ):

                                if lpo_document is None:
                                    lpo_document = document

                    procurement["quotation_document"] = (
                        quotation_document
                    )

                    procurement["lpo_document"] = (
                        lpo_document
                    )

                    procurement["supporting_documents"] = (
                        supporting_documents
                    )

                    procurement[
                        "quotation_package_complete"
                    ] = bool(
                        quotation_document
                        and lpo_document
                    )

        # =================================================
        # COMPLETED QUOTATION / LPO COUNT
        # =================================================
        cursor.execute("""
            SELECT
                COUNT(*) AS total
            FROM construction_purchase_procurement
            WHERE procurement_method = 'Quotation'
              AND status = 'Completed'
        """)

        completed_quotation_count = (
            cursor.fetchone() or {}
        )

        quotation_completed_count = (
            completed_quotation_count.get("total") or 0
        )

        # =================================================
        # LOAD COMPLETED QUOTATION / LPO PACKAGES
        # =================================================
        cursor.execute("""
            SELECT
                p.id,
                p.material_request_id,
                p.procurement_method,
                p.workflow_stage,
                p.initiated_by,
                p.initiated_by_name,
                p.initiated_at,
                p.status,
                p.notes,
                p.supplier_name,
                p.supplier_contact,
                p.procurement_amount,
                p.currency,
                p.quotation_number,
                p.quotation_date,
                p.lpo_number,

                p.pre_purchase_submitted_by,
                p.pre_purchase_submitted_by_name,
                p.pre_purchase_submitted_at,

                p.submitted_to_account_at,
                p.account_reviewed_at,
                p.account_reviewer_id,
                p.account_reviewer_name,
                p.account_comment,
                p.account_signature_data,
                p.account_signed_at,

                p.manager_reviewed_at,
                p.manager_reviewer_id,
                p.manager_reviewer_name,
                p.manager_comment,
                p.manager_signature_data,
                p.manager_signed_at,

                p.completed_by,
                p.completed_by_name,
                p.completed_at,

                p.created_at,
                p.updated_at,

                mr.request_number,
                mr.project_name,
                mr.requested_by,
                mr.requested_by_name,
                mr.request_date,
                mr.description,
                mr.manager_id,
                mr.manager_name,
                mr.manager_approved_at,
                mr.manager_comment

            FROM construction_purchase_procurement p

            INNER JOIN construction_purchase_material_requests mr
                ON mr.id = p.material_request_id

            WHERE p.procurement_method = 'Quotation'
              AND p.status = 'Completed'

            ORDER BY
                p.completed_at DESC,
                p.updated_at DESC,
                p.id DESC
        """)

        completed_quotation_procurements = (
            cursor.fetchall() or []
        )

        # =================================================
        # LOAD DOCUMENTS FOR COMPLETED QUOTATION PACKAGES
        # =================================================
        if completed_quotation_procurements:

            completed_procurement_ids = [
                procurement["id"]
                for procurement
                in completed_quotation_procurements
                if procurement.get("id") is not None
            ]

            if completed_procurement_ids:

                placeholders = ", ".join(
                    ["%s"] * len(
                        completed_procurement_ids
                    )
                )

                completed_document_query = f"""
                    SELECT
                        d.id,
                        d.procurement_id,
                        d.document_type,
                        d.workflow_stage,
                        d.document_title,
                        d.original_file_name,
                        d.file_url,
                        d.public_id,
                        d.uploaded_by,
                        d.uploaded_by_name,
                        d.uploaded_at,
                        d.notes
                    FROM construction_purchase_procurement_documents d
                    WHERE d.procurement_id IN ({placeholders})
                    ORDER BY
                        d.uploaded_at DESC,
                        d.id DESC
                """

                cursor.execute(
                    completed_document_query,
                    tuple(completed_procurement_ids)
                )

                completed_documents = (
                    cursor.fetchall() or []
                )

                completed_documents_by_procurement = {}

                for document in completed_documents:

                    procurement_id = document.get(
                        "procurement_id"
                    )

                    if procurement_id not in (
                        completed_documents_by_procurement
                    ):

                        completed_documents_by_procurement[
                            procurement_id
                        ] = []

                    completed_documents_by_procurement[
                        procurement_id
                    ].append(document)

                for procurement in (
                    completed_quotation_procurements
                ):

                    procurement_id = procurement.get("id")

                    documents = (
                        completed_documents_by_procurement.get(
                            procurement_id,
                            []
                        )
                    )

                    procurement["documents"] = documents

                    procurement["document_count"] = len(
                        documents
                    )

                    manager_quotation_document = None
                    manager_lpo_document = None
                    account_quotation_document = None
                    account_lpo_document = None

                    for document in documents:

                        document_type = (
                            document.get(
                                "document_type"
                            ) or ""
                        ).strip().lower()

                        workflow_stage = (
                            document.get(
                                "workflow_stage"
                            ) or ""
                        ).strip().lower()

                        if workflow_stage == "account stamp":

                            if (
                                document_type == "quotation"
                                and
                                account_quotation_document
                                is None
                            ):

                                account_quotation_document = (
                                    document
                                )

                            elif (
                                document_type == "lpo"
                                and
                                account_lpo_document
                                is None
                            ):

                                account_lpo_document = (
                                    document
                                )

                        elif workflow_stage == "manager signed":

                            if (
                                document_type == "quotation"
                                and
                                manager_quotation_document
                                is None
                            ):

                                manager_quotation_document = (
                                    document
                                )

                            elif (
                                document_type == "lpo"
                                and
                                manager_lpo_document
                                is None
                            ):

                                manager_lpo_document = (
                                    document
                                )

                    procurement[
                        "manager_quotation_document"
                    ] = manager_quotation_document

                    procurement[
                        "manager_lpo_document"
                    ] = manager_lpo_document

                    procurement[
                        "quotation_document"
                    ] = account_quotation_document

                    procurement[
                        "lpo_document"
                    ] = account_lpo_document

                    procurement[
                        "manager_documents_complete"
                    ] = bool(
                        manager_quotation_document
                        and manager_lpo_document
                    )

                    procurement[
                        "is_manager_signed"
                    ] = bool(
                        manager_quotation_document
                        or manager_lpo_document
                    )

        # =================================================
        # FINAL PROCUREMENT
        #
        # THIS IS THE IMPORTANT NEW SECTION.
        #
        # It is ONLY for Quotation/LPO procurements.
        #
        # Direct Card procurement remains completely
        # separate above.
        # =================================================
        cursor.execute("""
            SELECT
                p.id,
                p.material_request_id,

                p.procurement_method,
                p.status,
                p.workflow_stage,

                p.supplier_name,
                p.supplier_contact,

                p.procurement_amount,
                p.currency,

                # -------------------------------------------------
                # FINAL PAYMENT METHOD
                # -------------------------------------------------
                p.final_payment_method,

                # -------------------------------------------------
                # FINAL CARD PAYMENT
                # -------------------------------------------------
                p.final_card_paid_by,
                p.final_card_paid_by_name,
                p.final_card_paid_at,
                p.final_card_payment_reference,

                # -------------------------------------------------
                # FINAL CHEQUE PAYMENT
                # -------------------------------------------------
                p.cheque_required,
                p.cheque_number,
                p.cheque_date,
                p.cheque_bank,

                # -------------------------------------------------
                # FINAL DOCUMENT SUBMISSION
                # -------------------------------------------------
                p.final_documents_submitted_by,
                p.final_documents_submitted_by_name,
                p.final_documents_submitted_at,

                # -------------------------------------------------
                # COMPLETION
                # -------------------------------------------------
                p.completed_by,
                p.completed_by_name,
                p.completed_at,

                p.created_at,
                p.updated_at,

                # -------------------------------------------------
                # MATERIAL REQUEST
                # -------------------------------------------------
                mr.request_number,
                mr.project_name,
                mr.requested_by,
                mr.requested_by_name,
                mr.request_date,
                mr.description

            FROM construction_purchase_procurement p

            INNER JOIN construction_purchase_material_requests mr
                ON mr.id = p.material_request_id

            WHERE p.procurement_method = 'Quotation'
              AND p.status = 'Completed'

            ORDER BY
                p.completed_at DESC,
                p.updated_at DESC,
                p.id DESC
        """)

        final_procurement_rows = (
            cursor.fetchall() or []
        )

        # =================================================
        # LOAD ALL FINAL PROCUREMENT DOCUMENTS
        #
        # We intentionally recognize both:
        #
        # OLD:
        #   Final Procurement
        #
        # NEW:
        #   Final Purchase - Card
        #   Final Purchase - Cheque
        #
        # This avoids hiding existing documents.
        # =================================================
        if final_procurement_rows:

            final_procurement_ids = [
                procurement["id"]
                for procurement in final_procurement_rows
                if procurement.get("id") is not None
            ]

            if final_procurement_ids:

                placeholders = ", ".join(
                    ["%s"] * len(
                        final_procurement_ids
                    )
                )

                final_document_query = f"""
                    SELECT
                        d.id,
                        d.procurement_id,
                        d.document_type,
                        d.workflow_stage,
                        d.document_title,
                        d.original_file_name,
                        d.file_url,
                        d.public_id,
                        d.uploaded_by,
                        d.uploaded_by_name,
                        d.uploaded_at,
                        d.notes

                    FROM construction_purchase_procurement_documents d

                    WHERE d.procurement_id IN ({placeholders})

                      AND d.file_url IS NOT NULL
                      AND TRIM(d.file_url) <> ''

                    ORDER BY
                        d.uploaded_at DESC,
                        d.id DESC
                """

                cursor.execute(
                    final_document_query,
                    tuple(final_procurement_ids)
                )

                final_document_rows = (
                    cursor.fetchall() or []
                )

            else:
                final_document_rows = []

        else:
            final_document_rows = []

        # =================================================
        # GROUP FINAL DOCUMENTS BY PROCUREMENT
        # =================================================
        final_documents_by_procurement = {}

        for document in final_document_rows:

            procurement_id = document.get(
                "procurement_id"
            )

            if procurement_id is None:
                continue

            if procurement_id not in (
                final_documents_by_procurement
            ):

                final_documents_by_procurement[
                    procurement_id
                ] = []

            final_documents_by_procurement[
                procurement_id
            ].append(document)

        # =================================================
        # BUILD FINAL PROCUREMENT RECORDS
        # =================================================
        final_procurements = []

        for procurement in final_procurement_rows:

            procurement_id = procurement.get("id")

            documents = (
                final_documents_by_procurement.get(
                    procurement_id,
                    []
                )
            )

            # -------------------------------------------------
            # Attach every document
            # -------------------------------------------------
            procurement["documents"] = documents

            procurement["document_count"] = len(
                documents
            )

            # -------------------------------------------------
            # Individual document references
            # -------------------------------------------------
            procurement[
                "final_invoice_document"
            ] = None

            procurement[
                "final_receipt_document"
            ] = None

            procurement[
                "final_payment_evidence_document"
            ] = None

            procurement[
                "final_delivery_note_document"
            ] = None

            procurement[
                "final_other_document"
            ] = None

            # -------------------------------------------------
            # Supporting document groups
            # -------------------------------------------------
            procurement[
                "final_supporting_documents"
            ] = []

            for document in documents:

                document_type = (
                    document.get("document_type") or ""
                ).strip().lower()

                workflow_stage = (
                    document.get("workflow_stage") or ""
                ).strip().lower()

                # -------------------------------------------------
                # ONLY FINAL PURCHASE DOCUMENTS
                #
                # We accept the new workflow stages and the old
                # stage for backward compatibility.
                # -------------------------------------------------
                is_final_document = (
                    workflow_stage
                    in (
                        "final procurement",
                        "final purchase - card",
                        "final purchase - cheque"
                    )
                )

                if not is_final_document:
                    continue

                # -------------------------------------------------
                # INVOICE
                # -------------------------------------------------
                if (
                    document_type == "invoice"
                    and procurement[
                        "final_invoice_document"
                    ] is None
                ):

                    procurement[
                        "final_invoice_document"
                    ] = document

                # -------------------------------------------------
                # RECEIPT
                # -------------------------------------------------
                elif (
                    document_type == "receipt"
                    and procurement[
                        "final_receipt_document"
                    ] is None
                ):

                    procurement[
                        "final_receipt_document"
                    ] = document

                # -------------------------------------------------
                # PAYMENT EVIDENCE
                # -------------------------------------------------
                elif document_type in (
                    "payment evidence",
                    "final payment evidence"
                ):

                    if (
                        procurement[
                            "final_payment_evidence_document"
                        ] is None
                    ):

                        procurement[
                            "final_payment_evidence_document"
                        ] = document

                # -------------------------------------------------
                # DELIVERY NOTE
                # -------------------------------------------------
                elif document_type == "delivery note":

                    if (
                        procurement[
                            "final_delivery_note_document"
                        ] is None
                    ):

                        procurement[
                            "final_delivery_note_document"
                        ] = document

                # -------------------------------------------------
                # OTHER SUPPORTING DOCUMENT
                # -------------------------------------------------
                elif document_type == "other supporting document":

                    procurement[
                        "final_other_document"
                    ] = document

                # -------------------------------------------------
                # ANY FINAL SUPPORTING DOCUMENT
                # -------------------------------------------------
                procurement[
                    "final_supporting_documents"
                ].append(document)

            # -------------------------------------------------
            # Normalize payment method
            # -------------------------------------------------
            payment_method = (
                procurement.get(
                    "final_payment_method"
                ) or ""
            ).strip()

            if payment_method.lower() == "card":
                procurement[
                    "final_payment_method_display"
                ] = "Card"

            elif payment_method.lower() == "cheque":
                procurement[
                    "final_payment_method_display"
                ] = "Cheque"

            else:
                procurement[
                    "final_payment_method_display"
                ] = payment_method or "Not Recorded"

            # -------------------------------------------------
            # Normalize dates / payment information for HTML
            # -------------------------------------------------
            if (
                procurement.get("final_card_paid_at")
                is not None
            ):

                procurement[
                    "final_card_payment_recorded"
                ] = True

            else:

                procurement[
                    "final_card_payment_recorded"
                ] = False

            if (
                procurement.get("cheque_number")
                or procurement.get("cheque_date")
                or procurement.get("cheque_bank")
            ):

                procurement[
                    "final_cheque_payment_recorded"
                ] = True

            else:

                procurement[
                    "final_cheque_payment_recorded"
                ] = False

            # -------------------------------------------------
            # Add final procurement record
            # -------------------------------------------------
            final_procurements.append(
                procurement
            )

        # =================================================
        # FINAL DOCUMENT COUNT
        #
        # Count only documents belonging to the final
        # purchase workflow.
        # =================================================
        final_documents_count = 0

        for procurement in final_procurements:

            final_documents_count += len(
                procurement.get(
                    "final_supporting_documents",
                    []
                )
            )

        # =================================================
        # DEBUG
        # =================================================
        print("=================================================")
        print("ACCOUNT DASHBOARD")
        print(
            "Manager approved requests:",
            manager_approved_requests
        )
        print(
            "Pending quotation packages:",
            quotation_pending_count
        )
        print(
            "Completed quotation packages:",
            quotation_completed_count
        )
        print(
            "Card records loaded:",
            card_documents_submitted_count
        )
        print(
            "Petty cash records:",
            petty_cash_count
        )
        print(
            "Petty cash total:",
            petty_cash_total
        )
        print(
            "Completed quotation records loaded:",
            len(
                completed_quotation_procurements
            )
        )
        print(
            "Final procurement records:",
            len(final_procurements)
        )
        print(
            "Final procurement documents:",
            final_documents_count
        )

        # -------------------------------------------------
        # FINAL PAYMENT DEBUG
        # -------------------------------------------------
        for procurement in final_procurements:

            print(
                "FINAL PROCUREMENT:",
                procurement.get("id"),
                "| MR:",
                procurement.get("request_number"),
                "| METHOD:",
                procurement.get(
                    "final_payment_method_display"
                ),
                "| CARD REF:",
                procurement.get(
                    "final_card_payment_reference"
                ),
                "| CHEQUE:",
                procurement.get(
                    "cheque_number"
                ),
                "| DOCUMENTS:",
                procurement.get(
                    "document_count",
                    0
                )
            )

        print("=================================================")

    except Exception as e:

        print("=================================================")
        print("ACCOUNT DASHBOARD ERROR")
        print(type(e).__name__)
        print(repr(e))
        print("=================================================")

        import traceback
        traceback.print_exc()

        flash(
            "Unable to load the Account dashboard.",
            "danger"
        )

    finally:

        if cursor:

            try:
                cursor.close()
            except Exception:
                pass

        if conn:

            try:
                conn.close()
            except Exception:
                pass

    # =================================================
    # RENDER DASHBOARD
    # =================================================
    return render_template(
        "construction_admin/"
        "construction_account_dashboard.html",

        material_requests=material_requests,

        manager_approved_requests=(
            manager_approved_requests
        ),

        # -------------------------------------------------
        # DIRECT CARD
        # -------------------------------------------------
        card_procurements=(
            card_procurements
        ),

        card_documents_submitted_count=(
            card_documents_submitted_count
        ),

        # -------------------------------------------------
        # QUOTATION / LPO PENDING
        # -------------------------------------------------
        quotation_procurements=(
            quotation_procurements
        ),

        quotation_pending_count=(
            quotation_pending_count
        ),

        # -------------------------------------------------
        # COMPLETED QUOTATION / LPO
        # -------------------------------------------------
        completed_quotation_procurements=(
            completed_quotation_procurements
        ),

        quotation_completed_count=(
            quotation_completed_count
        ),

        # -------------------------------------------------
        # FINAL PROCUREMENT
        # -------------------------------------------------
        final_procurements=(
            final_procurements
        ),

        final_documents_count=(
            final_documents_count
        ),

        # -------------------------------------------------
        # PETTY CASH
        # -------------------------------------------------
        petty_cash_transactions=(
            petty_cash_transactions
        ),

        petty_cash_count=(
            petty_cash_count
        ),

        petty_cash_total=(
            petty_cash_total
        )
    )










# ============================================================
# ACCOUNT PROCUREMENT REVIEW / DIGITAL STAMP
# ============================================================

@app.route(
    "/construction/account/procurement/<int:procurement_id>/review",
    methods=["GET", "POST"]
)
def construction_account_procurement_review(procurement_id):

    # ========================================================
    # AUTHENTICATION
    # ========================================================

    user_id = session.get("construction_admin_id")

    if not user_id:
        return redirect(url_for("admin_login"))

    user_role = (
        session.get("construction_admin_role") or ""
    ).strip().lower()

    if user_role != "account":
        return redirect(construction_role_dashboard())

    user_name = (
        session.get("construction_admin_name")
        or session.get("name")
        or session.get("full_name")
        or session.get("username")
        or "Account Officer"
    )

    conn = None
    cursor = None

    uploaded_cloudinary_public_ids = []

    try:

        # ====================================================
        # DATABASE CONNECTION
        # ====================================================

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        # ====================================================
        # LOAD PROCUREMENT + MATERIAL REQUEST
        # ====================================================

        cursor.execute(
            """
            SELECT
                p.*,

                mr.request_number,
                mr.project_name,
                mr.description,
                mr.requested_by,
                mr.requested_by_name,
                mr.request_date,
                mr.status AS material_request_status

            FROM construction_purchase_procurement p

            INNER JOIN construction_purchase_material_requests mr
                ON mr.id = p.material_request_id

            WHERE p.id = %s

            LIMIT 1
            """,
            (procurement_id,)
        )

        procurement = cursor.fetchone()

        if not procurement:

            flash(
                "Procurement record was not found.",
                "danger"
            )

            return redirect(
                url_for("construction_account_dashboard")
            )

        # ====================================================
        # LOAD ALL PROCUREMENT DOCUMENTS
        # ====================================================

        cursor.execute(
            """
            SELECT
                id,
                procurement_id,
                document_type,
                workflow_stage,
                document_title,
                original_file_name,
                file_url,
                public_id,
                uploaded_by,
                uploaded_by_name,
                uploaded_at,
                notes

            FROM construction_purchase_procurement_documents

            WHERE procurement_id = %s

            ORDER BY
                uploaded_at ASC,
                id ASC
            """,
            (procurement_id,)
        )

        documents = cursor.fetchall()

        # ====================================================
        # IDENTIFY DOCUMENT TYPES
        # ====================================================

        quotation_document = None
        lpo_document = None
        card_payment_evidence = None

        supporting_documents = []

        for document in documents:

            document_type = (
                document.get("document_type") or ""
            ).strip().lower()

            if document_type == "quotation":

                if quotation_document is None:
                    quotation_document = document

            elif document_type == "lpo":

                if lpo_document is None:
                    lpo_document = document

            elif document_type == "card payment evidence":

                if card_payment_evidence is None:
                    card_payment_evidence = document

            else:

                supporting_documents.append(document)

        # ====================================================
        # PROCUREMENT METHOD
        # ====================================================

        procurement_method = (
            procurement.get("procurement_method") or ""
        ).strip()

        # ====================================================
        # CHECK PACKAGE COMPLETENESS
        # ====================================================

        package_complete = True
        missing_documents = []

        if procurement_method == "Quotation":

            if not quotation_document:

                package_complete = False

                missing_documents.append(
                    "Quotation"
                )

            if not lpo_document:

                package_complete = False

                missing_documents.append(
                    "LPO"
                )

        elif procurement_method == "Card":

            if not card_payment_evidence:

                package_complete = False

                missing_documents.append(
                    "Card Payment Evidence"
                )

        else:

            package_complete = False

            missing_documents.append(
                "Valid procurement method"
            )

        # ====================================================
        # POST - ACCOUNT STAMP
        # ====================================================

        if request.method == "POST":

            # ==================================================
            # CONFIRM PACKAGE COMPLETENESS
            # ==================================================

            if not package_complete:

                flash(
                    "This procurement package is incomplete. "
                    "Missing: "
                    + ", ".join(missing_documents),
                    "danger"
                )

                return redirect(
                    url_for(
                        "construction_account_procurement_review",
                        procurement_id=procurement_id
                    )
                )

            # ==================================================
            # CHECK STATUS
            # ==================================================

            allowed_statuses = [
                "Documents Submitted to Account",
                "Pending Account Review"
            ]

            current_status = (
                procurement.get("status") or ""
            ).strip()

            if current_status not in allowed_statuses:

                flash(
                    "This procurement is no longer awaiting "
                    "Account review.",
                    "warning"
                )

                return redirect(
                    url_for(
                        "construction_account_procurement_review",
                        procurement_id=procurement_id
                    )
                )

            # ==================================================
            # SIGNATURE
            # ==================================================

            signature_data = (
                request.form.get("signature_data") or ""
            ).strip()

            if not signature_data:

                flash(
                    "Account digital signature is required.",
                    "danger"
                )

                return redirect(
                    url_for(
                        "construction_account_procurement_review",
                        procurement_id=procurement_id
                    )
                )

            # ==================================================
            # RELOAD PROCUREMENT WITH LOCK
            # ==================================================

            cursor.execute(
                """
                SELECT *
                FROM construction_purchase_procurement

                WHERE id = %s

                FOR UPDATE
                """,
                (procurement_id,)
            )

            locked_procurement = cursor.fetchone()

            if not locked_procurement:

                raise Exception(
                    "Unable to lock procurement record."
                )

            locked_status = (
                locked_procurement.get("status") or ""
            ).strip()

            if locked_status not in allowed_statuses:

                conn.rollback()

                flash(
                    "This procurement has already been processed "
                    "by another user.",
                    "warning"
                )

                return redirect(
                    url_for(
                        "construction_account_procurement_review",
                        procurement_id=procurement_id
                    )
                )

            # ==================================================
            # RELOAD ALL DOCUMENTS
            # ==================================================

            cursor.execute(
                """
                SELECT
                    id,
                    procurement_id,
                    document_type,
                    workflow_stage,
                    document_title,
                    original_file_name,
                    file_url,
                    public_id,
                    uploaded_by,
                    uploaded_by_name,
                    uploaded_at,
                    notes

                FROM construction_purchase_procurement_documents

                WHERE procurement_id = %s

                ORDER BY
                    uploaded_at ASC,
                    id ASC
                """,
                (procurement_id,)
            )

            stamp_documents = cursor.fetchall()

            # ==================================================
            # CREATE ACCOUNT-STAMPED COPIES
            # ==================================================

            stamped_count = 0

            for document in stamp_documents:

                file_url = (
                    document.get("file_url") or ""
                ).strip()

                original_file_name = (
                    document.get("original_file_name")
                    or document.get("document_title")
                    or "document.pdf"
                )

                workflow_stage = (
                    document.get("workflow_stage") or ""
                ).strip().lower()

                # ----------------------------------------------
                # DO NOT RE-STAMP ACCOUNT-STAMPED DOCUMENTS
                # ----------------------------------------------

                if workflow_stage == "account stamp":
                    continue

                # ----------------------------------------------
                # ONLY PROCESS PDF DOCUMENTS
                # ----------------------------------------------

                if not file_url:
                    continue

                if not original_file_name.lower().endswith(".pdf"):

                    if ".pdf" not in file_url.lower():
                        continue

                # ----------------------------------------------
                # DOWNLOAD ORIGINAL PDF FROM CLOUDINARY
                # ----------------------------------------------

                try:

                    response = requests.get(
                        file_url,
                        timeout=60
                    )

                    response.raise_for_status()

                    pdf_bytes = response.content

                except Exception as download_error:

                    print(
                        "ACCOUNT STAMP PDF DOWNLOAD ERROR:",
                        original_file_name,
                        download_error
                    )

                    continue

                # ----------------------------------------------
                # CREATE SIGNED PDF
                # ----------------------------------------------

                try:

                    signed_pdf_bytes = create_signed_pdf(
                        pdf_bytes,
                        signature_data,
                        signer_name=user_name,
                        department_title="ACCOUNT DEPARTMENT",
                        position="right"
                    )

                except Exception as sign_error:

                    print(
                        "ACCOUNT PDF SIGNING ERROR:",
                        original_file_name,
                        sign_error
                    )

                    continue

                # ----------------------------------------------
                # CLOUDINARY FILENAME
                # ----------------------------------------------

                safe_file_name = (
                    os.path.splitext(
                        original_file_name
                    )[0]
                )

                safe_file_name = (
                    safe_file_name
                    .replace(" ", "_")
                    .replace("/", "_")
                    .replace("\\", "_")
                )

                stamped_file_name = (
                    f"{safe_file_name}_account_stamped.pdf"
                )

                cloudinary_folder = (
                    "prestigious_construction/"
                    "purchase/procurement_stamped/"
                    f"procurement_{procurement_id}"
                )

                stamped_public_id = (
                    f"{cloudinary_folder}/"
                    f"{os.path.splitext(stamped_file_name)[0]}"
                )

                # ----------------------------------------------
                # UPLOAD STAMPED PDF TO CLOUDINARY
                #
                # ----------------------------------------------
                # UPLOAD STAMPED PDF TO CLOUDINARY
                # ----------------------------------------------

                try:

                    stamped_file = BytesIO(
                        signed_pdf_bytes
                    )

                    stamped_file.name = stamped_file_name

                    upload_result = cloudinary.uploader.upload(
                        stamped_file,
                        resource_type="raw",
                        folder=cloudinary_folder,
                        public_id=os.path.splitext(
                            stamped_file_name
                        )[0],
                        overwrite=True
                    )

                except Exception as upload_error:

                    print(
                        "ACCOUNT STAMP CLOUDINARY UPLOAD ERROR:",
                        stamped_file_name,
                        upload_error
                    )

                    continue

                # ----------------------------------------------
                # GET CLOUDINARY URL
                # ----------------------------------------------

                stamped_url = (
                    upload_result.get("secure_url")
                    or upload_result.get("url")
                )

                stamped_cloudinary_public_id = (
                    upload_result.get("public_id")
                )

                if not stamped_url:
                    continue

                if stamped_cloudinary_public_id:

                    uploaded_cloudinary_public_ids.append(
                        stamped_cloudinary_public_id
                    )

                # IMPORTANT:
                # DO NOT add:
                #
                # ?fl_attachment=false
                #
                # The URL returned by Cloudinary is already the
                # normal delivery URL for the PDF asset.
                # ----------------------------------------------

                # ----------------------------------------------
                # INSERT STAMPED DOCUMENT RECORD
                # ----------------------------------------------

                stamped_title = (
                    document.get("document_title")
                    or original_file_name
                )

                stamped_title = (
                    f"{stamped_title} - Account Stamped"
                )

                cursor.execute(
                    """
                    INSERT INTO
                    construction_purchase_procurement_documents
                    (
                        procurement_id,
                        document_type,
                        workflow_stage,
                        document_title,
                        original_file_name,
                        file_url,
                        public_id,
                        uploaded_by,
                        uploaded_by_name,
                        uploaded_at,
                        notes
                    )

                    VALUES
                    (
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        NOW(),
                        %s
                    )
                    """,
                    (
                        procurement_id,
                        document.get("document_type"),
                        "Account Stamp",
                        stamped_title,
                        stamped_file_name,
                        stamped_url,
                        stamped_cloudinary_public_id,
                        user_id,
                        user_name,
                        "Digitally stamped by Account Department."
                    )
                )

                stamped_count += 1

            # ==================================================
            # REQUIRE AT LEAST ONE PDF TO BE STAMPED
            # ==================================================

            if stamped_count == 0:

                conn.rollback()

                flash(
                    "No PDF document could be digitally stamped. "
                    "Please make sure the procurement contains "
                    "valid PDF documents.",
                    "danger"
                )

                return redirect(
                    url_for(
                        "construction_account_procurement_review",
                        procurement_id=procurement_id
                    )
                )

            # ==================================================
            # UPDATE PROCUREMENT
            # ==================================================

            cursor.execute(
                """
                UPDATE construction_purchase_procurement

                SET
                    status = 'Pending Manager Review',

                    workflow_stage = 'Manager Review',

                    account_reviewed_at = NOW(),

                    account_reviewer_id = %s,

                    account_reviewer_name = %s,

                    account_signature_data = %s,

                    account_signed_at = NOW(),

                    updated_at = NOW()

                WHERE id = %s
                """,
                (
                    user_id,
                    user_name,
                    signature_data,
                    procurement_id
                )
            )

            # ==================================================
            # AUDIT LOG
            # ==================================================

            _construction_log_procurement_action(
                cursor=cursor,
                procurement_id=procurement_id,
                action_type="ACCOUNT_PACKAGE_STAMPED",
                action_description=(
                    "Account reviewed, digitally stamped and "
                    "forwarded the complete procurement package "
                    "to Management for review."
                ),
                from_status=locked_status,
                to_status="Pending Manager Review",
                performed_by=user_id,
                performed_by_name=user_name,
                notes=(
                    f"{stamped_count} PDF document(s) "
                    "digitally stamped by Account."
                )
            )

            # ==================================================
            # COMMIT FIRST
            # ==================================================

            conn.commit()

            print(
                "================================================"
            )

            print(
                "ACCOUNT PROCUREMENT STAMP COMMITTED"
            )

            print(
                "Procurement ID:",
                procurement_id
            )

            print(
                "Status: Pending Manager Review"
            )

            print(
                "Stamped PDFs:",
                stamped_count
            )

            print(
                "================================================"
            )

            # ==================================================
            # CLOSE ORIGINAL CURSOR BEFORE NOTIFICATIONS
            # ==================================================

            cursor.close()
            cursor = None

            # ==================================================
            # NOTIFICATION DATABASE CONNECTION
            # ==================================================

            notification_conn = None
            notification_cursor = None

            email_failures = []

            try:

                notification_conn = get_db_connection()

                notification_cursor = (
                    notification_conn.cursor(
                        dictionary=True
                    )
                )

                # =================================================
                # DETERMINE PURCHASE OFFICER
                # =================================================

                purchase_user_id = (
                    locked_procurement.get(
                        "pre_purchase_submitted_by"
                    )
                    or locked_procurement.get(
                        "initiated_by"
                    )
                    or locked_procurement.get(
                        "final_purchase_started_by"
                    )
                )

                # =================================================
                # ACCOUNT USER
                # =================================================

                account_recipient = None

                if user_id:

                    notification_cursor.execute(
                        """
                        SELECT
                            id,
                            fullname,
                            email,
                            role

                        FROM construction_admins

                        WHERE id = %s

                        LIMIT 1
                        """,
                        (user_id,)
                    )

                    account_recipient = (
                        notification_cursor.fetchone()
                    )

                # =================================================
                # MANAGER
                # =================================================

                notification_cursor.execute(
                    """
                    SELECT
                        id,
                        fullname,
                        email,
                        role

                    FROM construction_admins

                    WHERE LOWER(TRIM(role)) = 'manager'

                    ORDER BY id ASC
                    """
                )

                manager_recipients = (
                    notification_cursor.fetchall()
                )

                # =================================================
                # PURCHASE OFFICER
                # =================================================

                purchase_recipient = None

                if purchase_user_id:

                    notification_cursor.execute(
                        """
                        SELECT
                            id,
                            fullname,
                            email,
                            role

                        FROM construction_admins

                        WHERE id = %s

                        LIMIT 1
                        """,
                        (purchase_user_id,)
                    )

                    purchase_recipient = (
                        notification_cursor.fetchone()
                    )

                # =================================================
                # FALLBACK PURCHASE OFFICER
                # =================================================

                if not purchase_recipient:

                    notification_cursor.execute(
                        """
                        SELECT
                            id,
                            fullname,
                            email,
                            role
                        

                        FROM construction_admins

                        WHERE LOWER(TRIM(role)) = 'purchase'

                        ORDER BY id ASC

                        LIMIT 1
                        """
                    )

                    purchase_recipient = (
                        notification_cursor.fetchone()
                    )

                # =================================================
                # LOAD ALL DOCUMENTS AFTER ACCOUNT STAMP
                # =================================================

                notification_cursor.execute(
                    """
                    SELECT
                        id,
                        document_type,
                        workflow_stage,
                        document_title,
                        original_file_name,
                        file_url,
                        uploaded_by_name,
                        uploaded_at

                    FROM construction_purchase_procurement_documents

                    WHERE procurement_id = %s

                    ORDER BY
                        uploaded_at ASC,
                        id ASC
                    """,
                    (procurement_id,)
                )

                notification_documents = (
                    notification_cursor.fetchall()
                )

                # =================================================
                # BUILD DOCUMENT LINKS
                # =================================================

                document_links_html = ""

                if notification_documents:

                    document_links_html += """
                    <div style="
                        margin-top:25px;
                        padding:18px;
                        background:#f8f9fa;
                        border:1px solid #e1e5e8;
                        border-radius:8px;
                    ">

                        <h3 style="
                            margin-top:0;
                            color:#7a1f2b;
                        ">
                            Procurement Documents
                        </h3>
                    """

                    for document in notification_documents:

                        document_url = (
                            document.get("file_url")
                            or ""
                        ).strip()

                        document_name = (
                            document.get("document_title")
                            or document.get(
                                "original_file_name"
                            )
                            or "Document"
                        )

                        workflow_stage = (
                            document.get(
                                "workflow_stage"
                            )
                            or ""
                        )

                        if document_url:

                            document_links_html += f"""
                            <p style="
                                margin:8px 0;
                            ">
                                <a
                                    href="{document_url}"
                                    target="_blank"
                                    style="
                                        display:inline-block;
                                        padding:9px 14px;
                                        background:#7a1f2b;
                                        color:#ffffff;
                                        text-decoration:none;
                                        border-radius:5px;
                                    "
                                >
                                    View {document_name}
                                </a>

                                <span style="
                                    margin-left:8px;
                                    color:#666666;
                                    font-size:12px;
                                ">
                                    {workflow_stage}
                                </span>
                            </p>
                            """

                    document_links_html += """
                    </div>
                    """

                # =================================================
                # COMMON PROCUREMENT DETAILS
                # =================================================

                request_number = (
                    locked_procurement.get(
                        "request_number"
                    )
                    or procurement.get(
                        "request_number"
                    )
                    or "N/A"
                )

                project_name = (
                    locked_procurement.get(
                        "project_name"
                    )
                    or procurement.get(
                        "project_name"
                    )
                    or "N/A"
                )

                supplier_name = (
                    locked_procurement.get(
                        "supplier_name"
                    )
                    or "N/A"
                )

                procurement_amount = (
                    locked_procurement.get(
                        "procurement_amount"
                    )
                )

                currency = (
                    locked_procurement.get(
                        "currency"
                    )
                    or "QAR"
                )

                procurement_method = (
                    locked_procurement.get(
                        "procurement_method"
                    )
                    or "N/A"
                )

                formatted_amount = "N/A"

                if procurement_amount is not None:

                    try:

                        formatted_amount = (
                            f"{currency} "
                            f"{float(procurement_amount):,.2f}"
                        )

                    except Exception:

                        formatted_amount = (
                            f"{currency} "
                            f"{procurement_amount}"
                        )

                # =================================================
                # COMMON EMAIL HTML
                # =================================================

                common_email_html = f"""
                <div style="
                    font-family:Arial,Helvetica,sans-serif;
                    color:#333333;
                    line-height:1.6;
                    max-width:750px;
                    margin:0 auto;
                ">

                    <h2 style="
                        color:#7a1f2b;
                        margin-bottom:5px;
                    ">
                        Procurement Package Forwarded
                    </h2>

                    <p>
                        The procurement package has been
                        reviewed, digitally stamped by the
                        Account Department and forwarded to
                        Management for the next stage of review.
                    </p>

                    <hr>

                    <h3 style="color:#7a1f2b;">
                        Procurement Details
                    </h3>

                    <table style="
                        width:100%;
                        border-collapse:collapse;
                    ">

                        <tr>
                            <td style="
                                padding:8px;
                                border:1px solid #dddddd;
                                font-weight:bold;
                            ">
                                Material Request
                            </td>

                            <td style="
                                padding:8px;
                                border:1px solid #dddddd;
                            ">
                                {request_number}
                            </td>
                        </tr>

                        <tr>
                            <td style="
                                padding:8px;
                                border:1px solid #dddddd;
                                font-weight:bold;
                            ">
                                Project
                            </td>

                            <td style="
                                padding:8px;
                                border:1px solid #dddddd;
                            ">
                                {project_name}
                            </td>
                        </tr>

                        <tr>
                            <td style="
                                padding:8px;
                                border:1px solid #dddddd;
                                font-weight:bold;
                            ">
                                Supplier
                            </td>

                            <td style="
                                padding:8px;
                                border:1px solid #dddddd;
                            ">
                                {supplier_name}
                            </td>
                        </tr>

                        <tr>
                            <td style="
                                padding:8px;
                                border:1px solid #dddddd;
                                font-weight:bold;
                            ">
                                Procurement Method
                            </td>

                            <td style="
                                padding:8px;
                                border:1px solid #dddddd;
                            ">
                                {procurement_method}
                            </td>
                        </tr>

                        <tr>
                            <td style="
                                padding:8px;
                                border:1px solid #dddddd;
                                font-weight:bold;
                            ">
                                Amount
                            </td>

                            <td style="
                                padding:8px;
                                border:1px solid #dddddd;
                            ">
                                {formatted_amount}
                            </td>
                        </tr>

                        <tr>
                            <td style="
                                padding:8px;
                                border:1px solid #dddddd;
                                font-weight:bold;
                            ">
                                Account Officer
                            </td>

                            <td style="
                                padding:8px;
                                border:1px solid #dddddd;
                            ">
                                {user_name}
                            </td>
                        </tr>

                        <tr>
                            <td style="
                                padding:8px;
                                border:1px solid #dddddd;
                                font-weight:bold;
                            ">
                                New Status
                            </td>

                            <td style="
                                padding:8px;
                                border:1px solid #dddddd;
                                color:#7a1f2b;
                                font-weight:bold;
                            ">
                                Pending Manager Review
                            </td>
                        </tr>

                    </table>

                    {document_links_html}

                    <hr>

                    <p>
                        Regards,<br>
                        <strong>
                            Prestigious Trading & Construction W.L.L.
                        </strong>
                    </p>

                </div>
                """

                # =================================================
                # TRACK EMAILS
                # =================================================

                sent_count = 0

                # =================================================
                # HELPER TO SEND ONE EMAIL
                # =================================================

                def send_notification(
                    recipient,
                    recipient_label,
                    subject
                ):

                    nonlocal sent_count

                    if not recipient:

                        email_failures.append(
                            f"{recipient_label}: "
                            "recipient record not found"
                        )

                        print(
                            f"{recipient_label} EMAIL: "
                            "recipient record not found."
                        )

                        return

                    recipient_email = (
                        recipient.get("email") or ""
                    ).strip()

                    recipient_name = (
                        recipient.get("fullname")
                        or recipient_label
                    )

                    if not recipient_email:

                        email_failures.append(
                            f"{recipient_label}: "
                            f"{recipient_name} has no email"
                        )

                        print(
                            f"{recipient_label} EMAIL: "
                            f"{recipient_name} has no valid "
                            "email address."
                        )

                        return

                    # ---------------------------------------------
                    # ROLE-SPECIFIC GREETING
                    # ---------------------------------------------

                    email_html = common_email_html.replace(
                        "<p>\n                        The procurement package",
                        f"""
                        <p>
                            Hello <strong>{recipient_name}</strong>,
                        </p>

                        <p>
                        The procurement package"""
                    )

                    # ---------------------------------------------
                    # SEND
                    # ---------------------------------------------

                    try:

                        email_result = send_email(
                            recipient_email,
                            subject,
                            email_html
                        )

                        print(
                            "================================================"
                        )

                        print(
                            f"{recipient_label} EMAIL RESULT"
                        )

                        print(
                            "Recipient:",
                            recipient_email
                        )

                        print(
                            "Result:",
                            email_result
                        )

                        print(
                            "================================================"
                        )

                        if email_result:

                            sent_count += 1

                            print(
                                f"{recipient_label} "
                                "EMAIL SENT SUCCESSFULLY."
                            )

                        else:

                            email_failures.append(
                                f"{recipient_label}: "
                                f"{recipient_email} - "
                                "send_email returned False"
                            )

                            print(
                                f"{recipient_label} "
                                "EMAIL FAILED: send_email "
                                "returned False."
                            )

                    except Exception as email_error:

                        email_failures.append(
                            f"{recipient_label}: "
                            f"{recipient_email} - "
                            f"{email_error}"
                        )

                        print(
                            f"{recipient_label} EMAIL EXCEPTION:",
                            email_error
                        )

                # =================================================
                # SEND TO ACCOUNT
                # =================================================

                send_notification(
                    account_recipient,
                    "ACCOUNT",
                    (
                        "Account Stamp Completed - "
                        f"{request_number}"
                    )
                )

                # =================================================
                # SEND TO MANAGER(S)
                # =================================================

                if manager_recipients:

                    for manager in manager_recipients:

                        send_notification(
                            manager,
                            "MANAGER",
                            (
                                "Procurement Package Requires "
                                "Management Review - "
                                f"{request_number}"
                            )
                        )

                else:

                    email_failures.append(
                        "MANAGER: no Manager account found"
                    )

                    print(
                        "MANAGER EMAIL: "
                        "No Manager account found."
                    )

                # =================================================
                # SEND TO PURCHASE OFFICER
                # =================================================

                send_notification(
                    purchase_recipient,
                    "PURCHASE",
                    (
                        "Procurement Package Forwarded to "
                        "Management - "
                        f"{request_number}"
                    )
                )

            except Exception as notification_error:

                email_failures.append(
                    "Notification system error: "
                    f"{notification_error}"
                )

                print(
                    "================================================"
                )

                print(
                    "PROCUREMENT NOTIFICATION SYSTEM ERROR"
                )

                print(
                    type(notification_error).__name__
                )

                print(
                    notification_error
                )

                print(
                    "================================================"
                )

            finally:

                if notification_cursor:

                    try:
                        notification_cursor.close()
                    except Exception:
                        pass

                if notification_conn:

                    try:
                        notification_conn.close()
                    except Exception:
                        pass

            # ==================================================
            # USER FEEDBACK
            # ==================================================

            if email_failures:

                print(
                    "================================================"
                )

                print(
                    "ACCOUNT STAMP EMAIL FAILURES:"
                )

                for failure in email_failures:
                    print("-", failure)

                print(
                    "================================================"
                )

                flash(
                    "Procurement was successfully stamped and "
                    "forwarded to Management, but one or more "
                    "email notifications failed. Check the Flask "
                    "terminal for the Brevo response.",
                    "warning"
                )

            else:

                flash(
                    "Procurement package successfully stamped "
                    "and notifications sent to Account, Manager "
                    "and Purchase.",
                    "success"
                )

            return redirect(
                url_for(
                    "construction_account_dashboard"
                )
            )

        # ====================================================
        # GET - LOAD AUDIT HISTORY
        # ====================================================

        cursor.execute(
            """
            SELECT
                id,
                action_type,
                action_description,
                from_status,
                to_status,
                performed_by,
                performed_by_name,
                performed_at,
                notes

            FROM construction_purchase_procurement_actions

            WHERE procurement_id = %s

            ORDER BY
                performed_at DESC,
                id DESC
            """,
            (procurement_id,)
        )

        audit_history = cursor.fetchall()

        # ====================================================
        # RENDER REVIEW PAGE
        # ====================================================

        return render_template(
            "construction_admin/"
            "construction_account_procurement_review.html",

            procurement=procurement,

            documents=documents,

            quotation_document=quotation_document,

            lpo_document=lpo_document,

            card_payment_evidence=card_payment_evidence,

            supporting_documents=supporting_documents,

            package_complete=package_complete,

            missing_documents=missing_documents,

            audit_history=audit_history
        )

    # ========================================================
    # ERROR HANDLING
    # ========================================================

    except Exception as e:

        print(
            "================================================"
        )

        print(
            "ACCOUNT PROCUREMENT REVIEW ERROR"
        )

        print(
            type(e).__name__
        )

        print(
            e
        )

        print(
            "================================================"
        )

        if conn:

            try:
                conn.rollback()
            except Exception:
                pass

        # ----------------------------------------------------
        # CLEAN UP CLOUDINARY FILES CREATED DURING FAILED
        # TRANSACTION
        # ----------------------------------------------------

        for public_id in uploaded_cloudinary_public_ids:

            try:

                cloudinary.uploader.destroy(
                    public_id,
                    resource_type="raw"
                )

            except Exception as cleanup_error:

                print(
                    "ACCOUNT STAMP CLOUDINARY CLEANUP ERROR:",
                    public_id,
                    cleanup_error
                )

        flash(
            "An error occurred while processing the Account "
            "digital stamp. The procurement was not completed.",
            "danger"
        )

        return redirect(
            url_for(
                "construction_account_procurement_review",
                procurement_id=procurement_id
            )
        )

    finally:

        if cursor:

            try:
                cursor.close()
            except Exception:
                pass

        if conn:

            try:
                conn.close()
            except Exception:
                pass













# =========================================================
# ACCOUNT - ADD PETTY CASH
# =========================================================
@app.route(
    "/construction/account/petty-cash/add",
    methods=["POST"]
)
def construction_account_add_petty_cash():

    # -----------------------------------------------------
    # LOGIN PROTECTION
    # -----------------------------------------------------

    if "construction_admin_id" not in session:
        return redirect(url_for("admin_login"))

    # -----------------------------------------------------
    # ROLE PROTECTION
    # -----------------------------------------------------

    role = (
        session.get("construction_admin_role") or ""
    ).strip().lower()

    if role != "account":

        flash(
            "You are not authorized to add petty cash.",
            "danger"
        )

        return redirect(
            url_for("construction_role_dashboard")
        )

    # -----------------------------------------------------
    # FORM DATA
    # -----------------------------------------------------

    transaction_datetime_raw = (
        request.form.get("transaction_datetime") or ""
    ).strip()

    recipient_name = (
        request.form.get("recipient_name") or ""
    ).strip()

    reference_number = (
        request.form.get("reference_number") or ""
    ).strip()

    project_name = (
        request.form.get("project_name") or ""
    ).strip()

    amount_raw = (
        request.form.get("amount") or ""
    ).strip()

    receipt = request.files.get("receipt")

    # -----------------------------------------------------
    # VALIDATION
    # -----------------------------------------------------

    if not transaction_datetime_raw:

        flash(
            "Date and time are required.",
            "danger"
        )

        return redirect(
            url_for("construction_account_dashboard")
        )

    if not recipient_name:

        flash(
            "Please enter the name of the person who paid.",
            "danger"
        )

        return redirect(
            url_for("construction_account_dashboard")
        )

    if not reference_number:

        flash(
            "Receipt reference number is required.",
            "danger"
        )

        return redirect(
            url_for("construction_account_dashboard")
        )

    if not project_name:

        flash(
            "Project is required.",
            "danger"
        )

        return redirect(
            url_for("construction_account_dashboard")
        )

    if not amount_raw:

        flash(
            "Amount is required.",
            "danger"
        )

        return redirect(
            url_for("construction_account_dashboard")
        )

    if not receipt or not receipt.filename:

        flash(
            "Receipt upload is required.",
            "danger"
        )

        return redirect(
            url_for("construction_account_dashboard")
        )

    # -----------------------------------------------------
    # DATE / TIME
    # -----------------------------------------------------

    try:

        transaction_datetime = datetime.strptime(
            transaction_datetime_raw,
            "%Y-%m-%dT%H:%M"
        )

    except ValueError:

        flash(
            "Invalid date and time.",
            "danger"
        )

        return redirect(
            url_for("construction_account_dashboard")
        )

    # -----------------------------------------------------
    # AMOUNT
    # -----------------------------------------------------

    try:

        amount = Decimal(amount_raw)

        if amount <= 0:

            raise InvalidOperation

        amount = amount.quantize(
            Decimal("0.01")
        )

    except (InvalidOperation, ValueError):

        flash(
            "Please enter a valid amount greater than zero.",
            "danger"
        )

        return redirect(
            url_for("construction_account_dashboard")
        )

    # -----------------------------------------------------
    # FILE VALIDATION
    # -----------------------------------------------------

    original_filename = secure_filename(
        receipt.filename
    )

    allowed_extensions = {
        "pdf",
        "jpg",
        "jpeg",
        "png",
        "webp"
    }

    extension = ""

    if "." in original_filename:

        extension = (
            original_filename
           .rsplit(".", 1)[1]
            .lower()
        )

    if extension not in allowed_extensions:

        flash(
            "Receipt must be PDF, JPG, JPEG, PNG or WEBP.",
            "danger"
        )

        return redirect(
            url_for("construction_account_dashboard")
        )

    # -----------------------------------------------------
    # CLOUDINARY
    # -----------------------------------------------------

    upload_result = None

    conn = None
    cursor = None

    try:

        upload_result = cloudinary.uploader.upload(

            receipt,

            folder=(
                "prestigious_construction/"
                "account/petty_cash"
            ),

            resource_type="auto"
        )

        receipt_file_url = (
            upload_result.get("secure_url")
            or ""
        )

        receipt_public_id = (
            upload_result.get("public_id")
            or ""
        )

        if not receipt_file_url:

            raise Exception(
                "Cloudinary did not return a receipt URL."
            )

        # -------------------------------------------------
        # DATABASE
        # -------------------------------------------------

        conn = get_db_connection()

        cursor = conn.cursor()

        # -------------------------------------------------
        # TEMPORARY TRANSACTION NUMBER
        # -------------------------------------------------

        temporary_transaction_number = (
            "TEMP-"
            + datetime.now().strftime(
                "%Y%m%d%H%M%S%f"
            )
        )

        recorded_by = session.get(
            "construction_admin_id"
        )

        recorded_by_name = (
            session.get(
                "construction_admin_name"
            )
            or "Account"
        )

        # -------------------------------------------------
        # INSERT
        # -------------------------------------------------

        cursor.execute(
            """
            INSERT INTO
                construction_account_petty_cash_transactions
            (
                transaction_number,
                transaction_datetime,
                recipient_name,
                project_name,
                amount,
                currency,
                reference_number,
                receipt_original_name,
                receipt_file_url,
                receipt_public_id,
                recorded_by,
                recorded_by_name,
                status
            )

            VALUES
            (
                %s,
                %s,
                %s,
                %s,
                %s,
                'QAR',
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                'Recorded'
            )
            """,

            (
                temporary_transaction_number,
                transaction_datetime,
                recipient_name,
                project_name,
                amount,
                reference_number,
                original_filename,
                receipt_file_url,
                receipt_public_id,
                recorded_by,
                recorded_by_name
            )
        )

        petty_cash_id = cursor.lastrowid

        # -------------------------------------------------
        # FINAL TRANSACTION NUMBER
        # -------------------------------------------------

        transaction_number = (
            "PC-"
            + transaction_datetime.strftime("%Y%m%d")
            + "-"
            + str(petty_cash_id).zfill(6)
        )

        cursor.execute(
            """
            UPDATE
                construction_account_petty_cash_transactions

            SET
                transaction_number = %s

            WHERE id = %s
            """,

            (
                transaction_number,
                petty_cash_id
            )
        )

        conn.commit()

        flash(
            "Petty cash transaction "
            + transaction_number
            + " was recorded successfully.",
            "success"
        )

    except Exception as e:

        if conn:

            try:
                conn.rollback()
            except Exception:
                pass

        print(
            "================================================="
        )

        print(
            "PETTY CASH ERROR"
        )

        print(
            type(e).__name__
        )

        print(
            repr(e)
        )

        print(
            "================================================="
        )

        import traceback
        traceback.print_exc()

        flash(
            "Unable to record the petty cash transaction.",
            "danger"
        )

    finally:

        if cursor:

            try:
                cursor.close()
            except Exception:
                pass

        if conn:

            try:
                conn.close()
            except Exception:
                pass

    return redirect(
        url_for("construction_account_dashboard")
    )













# =========================================================
# ACCOUNT FINANCIAL TRANSACTION SYNCHRONIZATION
# =========================================================

from decimal import Decimal, InvalidOperation
from datetime import datetime


def _sync_account_financial_transactions(
    cursor,
    current_user_id,
    current_user_name
):
    """
    Synchronize completed/recorded payment transactions into
    the unified Account financial transaction ledger.

    SOURCES
    ---------------------------------------------------------
    1. Petty Cash
    2. Completed Direct Card Procurement
    3. Completed Cheque Procurement

    IMPORTANT
    ---------------------------------------------------------
    transaction_by / transaction_by_name
        = actual person who carried out the payment.

    payment_recorded_by / payment_recorded_by_name
        = Account user who originally recorded/imported
          the financial transaction.

    Existing recorder information is NOT overwritten when
    a ledger transaction already exists.

    The UNIQUE(source_type, source_id, payment_method)
    constraint prevents duplicate imports.
    """

    # =====================================================
    # CURRENT ACCOUNT USER
    # =====================================================

    current_user_id = (
        current_user_id
        if current_user_id
        else None
    )

    current_user_name = (
        current_user_name
        or "Account Officer"
    )

    # =====================================================
    # 1. PETTY CASH
    # =====================================================

    cursor.execute("""
        SELECT
            id,
            transaction_number,
            transaction_datetime,
            recipient_name,
            project_name,
            amount,
            currency,
            reference_number,
            recorded_by,
            recorded_by_name,
            status
        FROM construction_account_petty_cash_transactions
        WHERE status <> 'Rejected'
    """)

    petty_cash_rows = (
        cursor.fetchall()
        or []
    )

    for row in petty_cash_rows:

        transaction_by_id = (
            row.get("recorded_by")
            or None
        )

        transaction_by_name = (
            row.get("recorded_by_name")
            or "Unknown"
        )

        source_id = row["id"]

        # -------------------------------------------------
        # Preserve the original ledger recorder.
        #
        # We only use the current Account user for a brand
        # new imported transaction.
        # -------------------------------------------------

        cursor.execute("""
            INSERT INTO construction_account_financial_transactions
            (
                transaction_number,
                transaction_date,
                source_type,
                source_id,
                payment_method,
                project_name,
                supplier_or_recipient,
                amount,
                currency,
                payment_reference,
                transaction_by,
                transaction_by_name,
                payment_recorded_by,
                payment_recorded_by_name,
                payment_recorded_at,
                status
            )
            VALUES
            (
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                NOW(),
                %s
            )
            ON DUPLICATE KEY UPDATE

                transaction_date =
                    VALUES(transaction_date),

                project_name =
                    VALUES(project_name),

                supplier_or_recipient =
                    VALUES(supplier_or_recipient),

                amount =
                    VALUES(amount),

                currency =
                    VALUES(currency),

                payment_reference =
                    VALUES(payment_reference),

                transaction_by =
                    VALUES(transaction_by),

                transaction_by_name =
                    VALUES(transaction_by_name),

                status =
                    VALUES(status)
        """, (
            row["transaction_number"],
            row["transaction_datetime"],
            "Petty Cash",
            source_id,
            "Petty Cash",
            row.get("project_name"),
            row.get("recipient_name"),
            row.get("amount") or Decimal("0.00"),
            row.get("currency") or "QAR",
            row.get("reference_number"),
            transaction_by_id,
            transaction_by_name,
            current_user_id,
            current_user_name,
            "Cancelled"
            if row.get("status") == "Rejected"
            else "Recorded"
        ))

    # =====================================================
    # 2. COMPLETED DIRECT CARD PAYMENTS
    # =====================================================

    cursor.execute("""
        SELECT
            p.id,
            p.procurement_amount,
            p.currency,
            p.payment_date,

            p.status,
            p.workflow_stage,

            p.card_paid_by,
            p.card_paid_by_name,
            p.card_paid_at,
            p.card_payment_reference,

            p.supplier_name,

            mr.project_name

        FROM construction_purchase_procurement p

        INNER JOIN construction_purchase_material_requests mr
            ON mr.id = p.material_request_id

        WHERE p.procurement_method = 'Card'

          AND p.status = 'Completed'

          AND (
                p.card_paid_at IS NOT NULL
                OR p.payment_date IS NOT NULL
                OR p.card_paid_by IS NOT NULL
                OR p.card_paid_by_name IS NOT NULL
          )

          AND p.procurement_amount IS NOT NULL

          AND p.procurement_amount > 0
    """)

    card_rows = (
        cursor.fetchall()
        or []
    )

    for row in card_rows:

        transaction_date = (
            row.get("card_paid_at")
            or row.get("payment_date")
            or datetime.now()
        )

        transaction_by_id = (
            row.get("card_paid_by")
            or None
        )

        transaction_by_name = (
            row.get("card_paid_by_name")
            or "Unknown"
        )

        reference = (
            row.get("card_payment_reference")
            or f"CARD-{row['id']:06d}"
        )

        transaction_number = (
            f"CARD-{row['id']:06d}"
        )

        cursor.execute("""
            INSERT INTO construction_account_financial_transactions
            (
                transaction_number,
                transaction_date,
                source_type,
                source_id,
                payment_method,
                project_name,
                supplier_or_recipient,
                amount,
                currency,
                payment_reference,
                transaction_by,
                transaction_by_name,
                payment_recorded_by,
                payment_recorded_by_name,
                payment_recorded_at,
                status
            )
            VALUES
            (
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                NOW(),
                'Recorded'
            )

            ON DUPLICATE KEY UPDATE

                transaction_date =
                    VALUES(transaction_date),

                project_name =
                    VALUES(project_name),

                supplier_or_recipient =
                    VALUES(supplier_or_recipient),

                amount =
                    VALUES(amount),

                currency =
                    VALUES(currency),

                payment_reference =
                    VALUES(payment_reference),

                transaction_by =
                    VALUES(transaction_by),

                transaction_by_name =
                    VALUES(transaction_by_name),

                status =
                    'Recorded'
        """, (
            transaction_number,
            transaction_date,
            "Procurement",
            row["id"],
            "Card",
            row.get("project_name"),
            row.get("supplier_name"),
            row.get("procurement_amount")
            or Decimal("0.00"),
            row.get("currency") or "QAR",
            reference,
            transaction_by_id,
            transaction_by_name,
            current_user_id,
            current_user_name
        ))

    # =====================================================
    # 3. COMPLETED CHEQUE PAYMENTS
    # =====================================================

    cursor.execute("""
        SELECT
            p.id,

            p.procurement_amount,
            p.currency,
            p.payment_date,

            p.status,
            p.workflow_stage,

            p.final_payment_method,

            p.cheque_number,
            p.cheque_date,
            p.cheque_bank,

            p.account_cheque_issued_by,
            p.account_cheque_issued_by_name,
            p.account_cheque_issued_at,

            p.account_cheque_number,
            p.account_cheque_date,
            p.account_cheque_bank,

            p.supplier_name,

            mr.project_name

        FROM construction_purchase_procurement p

        INNER JOIN construction_purchase_material_requests mr
            ON mr.id = p.material_request_id

        WHERE p.final_payment_method = 'Cheque'

          AND p.status = 'Completed'

          AND p.procurement_amount IS NOT NULL

          AND p.procurement_amount > 0
    """)

    cheque_rows = (
        cursor.fetchall()
        or []
    )

    for row in cheque_rows:

        # -------------------------------------------------
        # TRANSACTION DATE
        # -------------------------------------------------

        transaction_date = (
            row.get("account_cheque_issued_at")
            or row.get("account_cheque_date")
            or row.get("cheque_date")
            or row.get("payment_date")
            or datetime.now()
        )

        # -------------------------------------------------
        # ACTUAL PERSON WHO ISSUED THE CHEQUE
        # -------------------------------------------------

        transaction_by_id = (
            row.get("account_cheque_issued_by")
            or None
        )

        transaction_by_name = (
            row.get("account_cheque_issued_by_name")
            or "Unknown"
        )

        # -------------------------------------------------
        # CHEQUE REFERENCE
        # -------------------------------------------------

        cheque_reference = (
            row.get("account_cheque_number")
            or row.get("cheque_number")
            or f"CHEQUE-{row['id']:06d}"
        )

        transaction_number = (
            f"CHEQUE-{row['id']:06d}"
        )

        cursor.execute("""
            INSERT INTO construction_account_financial_transactions
            (
                transaction_number,
                transaction_date,
                source_type,
                source_id,
                payment_method,
                project_name,
                supplier_or_recipient,
                amount,
                currency,
                payment_reference,
                transaction_by,
                transaction_by_name,
                payment_recorded_by,
                payment_recorded_by_name,
                payment_recorded_at,
                status
            )
            VALUES
            (
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                %s,
                NOW(),
                'Recorded'
            )

            ON DUPLICATE KEY UPDATE

                transaction_date =
                    VALUES(transaction_date),

                project_name =
                    VALUES(project_name),

                supplier_or_recipient =
                    VALUES(supplier_or_recipient),

                amount =
                    VALUES(amount),

                currency =
                    VALUES(currency),

                payment_reference =
                    VALUES(payment_reference),

                transaction_by =
                    VALUES(transaction_by),

                transaction_by_name =
                    VALUES(transaction_by_name),

                payment_recorded_by =
                    COALESCE(
                        payment_recorded_by,
                        VALUES(payment_recorded_by)
                    ),

                payment_recorded_by_name =
                    COALESCE(
                        payment_recorded_by_name,
                        VALUES(payment_recorded_by_name)
                    ),

                status =
                    'Recorded'
        """, (
            transaction_number,
            transaction_date,
            "Procurement",
            row["id"],
            "Cheque",
            row.get("project_name"),
            row.get("supplier_name"),
            row.get("procurement_amount")
            or Decimal("0.00"),
            row.get("currency") or "QAR",
            cheque_reference,
            transaction_by_id,
            transaction_by_name,
            current_user_id,
            current_user_name
        ))








# =========================================================
# CONSTRUCTION ACCOUNT FINANCIAL SUMMARY
# =========================================================
@app.route("/construction/account/financial-summary")
def construction_account_financial_summary():

    # -----------------------------------------------------
    # LOGIN PROTECTION
    # -----------------------------------------------------
    if "construction_admin_id" not in session:
        return redirect(url_for("admin_login"))

    # -----------------------------------------------------
    # ROLE PROTECTION
    # -----------------------------------------------------
    role = (
        session.get("construction_admin_role") or ""
    ).strip().lower()

    if role != "account":
        flash(
            "You are not authorized to access the Financial Summary.",
            "danger"
        )
        return redirect(
            url_for("construction_account_dashboard")
        )

    conn = None
    cursor = None

    try:

        conn = get_db_connection()
        cursor = conn.cursor(dictionary=True)

        # =================================================
        # CURRENT ACCOUNT USER
        # =================================================
        current_user_id = session.get(
            "construction_admin_id"
        )

        current_user_name = (
            session.get("construction_admin_name")
            or "Account Officer"
        )

        # =================================================
        # SAFE DECIMAL
        # =================================================
        from decimal import Decimal

        def decimal_value(value):

            if value is None:
                return Decimal("0.00")

            try:
                return Decimal(str(value))
            except Exception:
                return Decimal("0.00")

        # =================================================
        # SYNC FINANCIAL TRANSACTION
        #
        # IMPORTANT:
        #
        # source_type + source_id is the permanent identity
        # of a synchronized financial transaction.
        #
        # The database also has:
        #
        # UNIQUE(source_type, source_id)
        #
        # Therefore the same source cannot be inserted twice.
        # =================================================
        def sync_transaction(
            source_type,
            source_id,
            transaction_number,
            transaction_date,
            payment_method,
            project_name,
            supplier_or_recipient,
            amount,
            currency,
            payment_reference,
            transaction_by,
            transaction_by_name,
            notes=None
        ):

            if source_id is None:
                return

            if not payment_method:
                return

            source_type = (
                str(source_type).strip()
            )

            payment_method = (
                str(payment_method).strip()
            )

            allowed_methods = {
                "Petty Cash",
                "Card",
                "Cheque"
            }

            if payment_method not in allowed_methods:
                return

            amount = decimal_value(amount)

            # -------------------------------------------------
            # FIND EXISTING RECORD
            #
            # DO NOT use source_id alone.
            #
            # source_id belongs to different tables and can
            # legitimately have the same number.
            # -------------------------------------------------
            cursor.execute(
                """
                SELECT
                    id,
                    payment_recorded_by,
                    payment_recorded_by_name,
                    payment_recorded_at
                FROM
                    construction_account_financial_transactions
                WHERE
                    source_type = %s
                    AND source_id = %s
                LIMIT 1
                """,
                (
                    source_type,
                    source_id
                )
            )

            existing = cursor.fetchone()

            # =================================================
            # UPDATE EXISTING RECORD
            #
            # IMPORTANT:
            #
            # payment_recorded_by
            # payment_recorded_by_name
            # payment_recorded_at
            #
            # are NOT changed here.
            #
            # This preserves the original audit trail.
            # =================================================
            if existing:

                cursor.execute(
                    """
                    UPDATE
                        construction_account_financial_transactions
                    SET
                        transaction_number = %s,
                        transaction_date = %s,
                        payment_method = %s,
                        project_name = %s,
                        supplier_or_recipient = %s,
                        amount = %s,
                        currency = %s,
                        payment_reference = %s,
                        transaction_by = %s,
                        transaction_by_name = %s,
                        status = 'Recorded',
                        notes = %s
                    WHERE
                        id = %s
                    """,
                    (
                        transaction_number,
                        transaction_date,
                        payment_method,
                        project_name,
                        supplier_or_recipient,
                        amount,
                        currency,
                        payment_reference,
                        transaction_by,
                        transaction_by_name,
                        notes,
                        existing["id"]
                    )
                )

            # =================================================
            # INSERT NEW RECORD
            # =================================================
            else:

                cursor.execute(
                    """
                    INSERT INTO
                        construction_account_financial_transactions
                    (
                        transaction_number,
                        transaction_date,
                        source_type,
                        source_id,
                        payment_method,
                        project_name,
                        supplier_or_recipient,
                        amount,
                        currency,
                        payment_reference,
                        transaction_by,
                        transaction_by_name,
                        payment_recorded_by,
                        payment_recorded_by_name,
                        payment_recorded_at,
                        status,
                        notes
                    )
                    VALUES
                    (
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        %s,
                        NOW(),
                        'Recorded',
                        %s
                    )
                    """,
                    (
                        transaction_number,
                        transaction_date,
                        source_type,
                        source_id,
                        payment_method,
                        project_name,
                        supplier_or_recipient,
                        amount,
                        currency,
                        payment_reference,
                        transaction_by,
                        transaction_by_name,
                        current_user_id,
                        current_user_name,
                        notes
                    )
                )

        # =================================================
        # 1. DIRECT CARD PROCUREMENT
        #
        # procurement_method = Card
        #
        # These are real Card transactions.
        # =================================================
        cursor.execute(
            """
            SELECT
                p.id,
                p.material_request_id,
                p.procurement_method,
                p.workflow_stage,
                p.status,

                p.supplier_name,
                p.supplier_contact,

                p.procurement_amount,
                p.currency,

                p.payment_date,

                p.card_paid_by,
                p.card_paid_by_name,
                p.card_paid_at,
                p.card_payment_reference,

                p.created_at,
                p.updated_at,

                mr.request_number,
                mr.project_name,
                mr.requested_by_name

            FROM
                construction_purchase_procurement p

            INNER JOIN
                construction_purchase_material_requests mr
                ON mr.id = p.material_request_id

            WHERE
                p.procurement_method = 'Card'
                AND
                (
                    p.status = 'Documents Submitted to Account'
                    OR
                    (
                        p.status = 'Completed'
                        AND p.workflow_stage = 'Completed'
                    )
                )

            ORDER BY
                p.updated_at DESC,
                p.id DESC
            """
        )

        direct_card_rows = (
            cursor.fetchall() or []
        )

        direct_card_synced = 0

        for row in direct_card_rows:

            transaction_date = (
                row.get("card_paid_at")
                or row.get("payment_date")
                or row.get("updated_at")
                or row.get("created_at")
            )

            # -------------------------------------------------
            # Stable unique transaction number
            # -------------------------------------------------
            transaction_number = (
                f"DC-{row.get('id')}"
            )

            transaction_by = row.get(
                "card_paid_by"
            )

            transaction_by_name = (
                row.get("card_paid_by_name")
                or "Unknown"
            )

            sync_transaction(
                source_type="Direct Card",
                source_id=row.get("id"),
                transaction_number=transaction_number,
                transaction_date=transaction_date,
                payment_method="Card",
                project_name=row.get("project_name"),
                supplier_or_recipient=row.get(
                    "supplier_name"
                ),
                amount=row.get(
                    "procurement_amount"
                ),
                currency=row.get("currency"),
                payment_reference=row.get(
                    "card_payment_reference"
                ),
                transaction_by=transaction_by,
                transaction_by_name=transaction_by_name,
                notes="Direct Card procurement"
            )

            direct_card_synced += 1

        # =================================================
        # 2. QUOTATION / LPO FINAL PAYMENTS
        #
        # Card and Cheque are separate source types.
        # =================================================
        cursor.execute(
            """
            SELECT
                p.id,
                p.material_request_id,
                p.procurement_method,
                p.status,
                p.workflow_stage,

                p.supplier_name,
                p.supplier_contact,

                p.procurement_amount,
                p.currency,

                p.final_payment_method,

                p.final_card_paid_by,
                p.final_card_paid_by_name,
                p.final_card_paid_at,
                p.final_card_payment_reference,

                p.cheque_required,
                p.cheque_number,
                p.cheque_date,
                p.cheque_bank,

                p.final_documents_submitted_by,
                p.final_documents_submitted_by_name,
                p.final_documents_submitted_at,

                p.completed_by,
                p.completed_by_name,
                p.completed_at,

                p.created_at,
                p.updated_at,

                mr.request_number,
                mr.project_name,
                mr.requested_by_name

            FROM
                construction_purchase_procurement p

            INNER JOIN
                construction_purchase_material_requests mr
                ON mr.id = p.material_request_id

            WHERE
                p.procurement_method = 'Quotation'
                AND p.status = 'Completed'
                AND LOWER(
                    TRIM(
                        COALESCE(
                            p.final_payment_method,
                            ''
                        )
                    )
                ) IN ('card', 'cheque')

            ORDER BY
                p.completed_at DESC,
                p.updated_at DESC,
                p.id DESC
            """
        )

        quotation_final_rows = (
            cursor.fetchall() or []
        )

        quotation_card_synced = 0
        quotation_cheque_synced = 0

        for row in quotation_final_rows:

            final_method = (
                row.get("final_payment_method")
                or ""
            ).strip().lower()

            # =================================================
            # FINAL CARD
            # =================================================
            if final_method == "card":

                transaction_date = (
                    row.get("final_card_paid_at")
                    or row.get(
                        "final_documents_submitted_at"
                    )
                    or row.get("completed_at")
                    or row.get("updated_at")
                )

                transaction_number = (
                    f"QP-CARD-{row.get('id')}"
                )

                transaction_by = row.get(
                    "final_card_paid_by"
                )

                transaction_by_name = (
                    row.get(
                        "final_card_paid_by_name"
                    )
                    or "Unknown"
                )

                payment_reference = row.get(
                    "final_card_payment_reference"
                )

                sync_transaction(
                    source_type="Quotation/LPO - Card",
                    source_id=row.get("id"),
                    transaction_number=transaction_number,
                    transaction_date=transaction_date,
                    payment_method="Card",
                    project_name=row.get(
                        "project_name"
                    ),
                    supplier_or_recipient=row.get(
                        "supplier_name"
                    ),
                    amount=row.get(
                        "procurement_amount"
                    ),
                    currency=row.get("currency"),
                    payment_reference=payment_reference,
                    transaction_by=transaction_by,
                    transaction_by_name=transaction_by_name,
                    notes=(
                        "Quotation/LPO final payment - Card"
                    )
                )

                quotation_card_synced += 1

            # =================================================
            # FINAL CHEQUE
            # =================================================
            elif final_method == "cheque":

                transaction_date = (
                    row.get("cheque_date")
                    or row.get(
                        "final_documents_submitted_at"
                    )
                    or row.get("completed_at")
                    or row.get("updated_at")
                )

                transaction_number = (
                    f"QP-CHEQUE-{row.get('id')}"
                )

                transaction_by = row.get(
                    "final_documents_submitted_by"
                )

                transaction_by_name = row.get(
                    "final_documents_submitted_by_name"
                )

                if not transaction_by_name:

                    transaction_by = row.get(
                        "completed_by"
                    )

                    transaction_by_name = row.get(
                        "completed_by_name"
                    )

                if not transaction_by_name:
                    transaction_by_name = "Unknown"

                payment_reference = row.get(
                    "cheque_number"
                )

                cheque_bank = row.get(
                    "cheque_bank"
                )

                cheque_notes = (
                    "Quotation/LPO final payment - Cheque"
                )

                if cheque_bank:

                    cheque_notes += (
                        f" | Bank: {cheque_bank}"
                    )

                sync_transaction(
                    source_type="Quotation/LPO - Cheque",
                    source_id=row.get("id"),
                    transaction_number=transaction_number,
                    transaction_date=transaction_date,
                    payment_method="Cheque",
                    project_name=row.get(
                        "project_name"
                    ),
                    supplier_or_recipient=row.get(
                        "supplier_name"
                    ),
                    amount=row.get(
                        "procurement_amount"
                    ),
                    currency=row.get("currency"),
                    payment_reference=payment_reference,
                    transaction_by=transaction_by,
                    transaction_by_name=transaction_by_name,
                    notes=cheque_notes
                )

                quotation_cheque_synced += 1

        # =================================================
        # 3. PETTY CASH
        # =================================================
        cursor.execute(
            """
            SELECT
                id,
                transaction_number,
                transaction_datetime,
                recipient_name,
                project_name,
                amount,
                currency,
                reference_number,
                status,
                recorded_by,
                recorded_by_name,
                recorded_at

            FROM
                construction_account_petty_cash_transactions

            WHERE
                status IS NULL
                OR TRIM(status) <> 'Rejected'

            ORDER BY
                transaction_datetime DESC,
                id DESC
            """
        )

        petty_cash_rows = (
            cursor.fetchall() or []
        )

        petty_cash_synced = 0

        for row in petty_cash_rows:

            transaction_date = (
                row.get("transaction_datetime")
                or row.get("recorded_at")
            )

            transaction_number = (
                row.get("transaction_number")
                or f"PC-{row.get('id')}"
            )

            transaction_by_name = (
                row.get("recorded_by_name")
                or "Unknown"
            )

            sync_transaction(
                source_type="Petty Cash",
                source_id=row.get("id"),
                transaction_number=transaction_number,
                transaction_date=transaction_date,
                payment_method="Petty Cash",
                project_name=row.get(
                    "project_name"
                ),
                supplier_or_recipient=row.get(
                    "recipient_name"
                ),
                amount=row.get("amount"),
                currency=row.get("currency"),
                payment_reference=row.get(
                    "reference_number"
                ),
                transaction_by=row.get(
                    "recorded_by"
                ),
                transaction_by_name=transaction_by_name,
                notes="Account Petty Cash transaction"
            )

            petty_cash_synced += 1

        # =================================================
        # COMMIT SYNCHRONIZATION
        # =================================================
        conn.commit()

        # =================================================
        # DEBUG
        # =================================================
        print("=================================================")
        print("FINANCIAL SUMMARY SYNCHRONIZATION")
        print(
            "Direct Card records:",
            direct_card_synced
        )
        print(
            "Quotation/LPO Card records:",
            quotation_card_synced
        )
        print(
            "Quotation/LPO Cheque records:",
            quotation_cheque_synced
        )
        print(
            "Petty Cash records:",
            petty_cash_synced
        )
        print("=================================================")

        # =================================================
        # FILTERS
        # =================================================
        payment_method = (
            request.args.get("payment_method")
            or "All"
        ).strip()

        start_date = (
            request.args.get("start_date")
            or ""
        ).strip()

        end_date = (
            request.args.get("end_date")
            or ""
        ).strip()

        project = (
            request.args.get("project")
            or ""
        ).strip()

        search = (
            request.args.get("search")
            or ""
        ).strip()

        # =================================================
        # PAGE
        # =================================================
        try:

            page = int(
                request.args.get("page", 1)
            )

        except (ValueError, TypeError):

            page = 1

        if page < 1:
            page = 1

        # =================================================
        # PAGE SIZE
        # =================================================
        try:

            page_size = int(
                request.args.get(
                    "page_size",
                    50
                )
            )

        except (ValueError, TypeError):

            page_size = 50

        allowed_page_sizes = {
            25,
            50,
            100,
            250
        }

        if page_size not in allowed_page_sizes:
            page_size = 50

        # =================================================
        # ALLOWED PAYMENT METHODS
        #
        # NO CASH
        # =================================================
        allowed_methods = {
            "All",
            "Petty Cash",
            "Cheque",
            "Card"
        }

        if payment_method not in allowed_methods:
            payment_method = "All"

        # =================================================
        # BUILD FILTER
        # =================================================
        where = [
            """
            (
                status IS NULL
                OR TRIM(status) <> 'Cancelled'
            )
            """
        ]

        params = []

        # -------------------------------------------------
        # PAYMENT METHOD
        # -------------------------------------------------
        if payment_method != "All":

            where.append(
                "payment_method = %s"
            )

            params.append(
                payment_method
            )

        # -------------------------------------------------
        # START DATE
        # -------------------------------------------------
        if start_date:

            where.append(
                "DATE(transaction_date) >= %s"
            )

            params.append(
                start_date
            )

        # -------------------------------------------------
        # END DATE
        # -------------------------------------------------
        if end_date:

            where.append(
                "DATE(transaction_date) <= %s"
            )

            params.append(
                end_date
            )

        # -------------------------------------------------
        # PROJECT
        # -------------------------------------------------
        if project:

            where.append(
                "project_name LIKE %s"
            )

            params.append(
                f"%{project}%"
            )

        # -------------------------------------------------
        # SEARCH
        # -------------------------------------------------
        if search:

            where.append(
                """
                (
                    transaction_number LIKE %s
                    OR payment_reference LIKE %s
                    OR supplier_or_recipient LIKE %s
                    OR transaction_by_name LIKE %s
                    OR payment_recorded_by_name LIKE %s
                    OR project_name LIKE %s
                    OR source_type LIKE %s
                    OR notes LIKE %s
                )
                """
            )

            search_value = (
                f"%{search}%"
            )

            params.extend(
                [
                    search_value,
                    search_value,
                    search_value,
                    search_value,
                    search_value,
                    search_value,
                    search_value,
                    search_value
                ]
            )

        where_sql = " AND ".join(
            where
        )

        # =================================================
        # SUMMARY
        # =================================================
        cursor.execute(
            f"""
            SELECT
                COUNT(*) AS transaction_count,

                COALESCE(
                    SUM(amount),
                    0
                ) AS total_paid,

                COALESCE(
                    SUM(
                        CASE
                            WHEN payment_method = 'Petty Cash'
                            THEN amount
                            ELSE 0
                        END
                    ),
                    0
                ) AS petty_cash_total,

                COALESCE(
                    SUM(
                        CASE
                            WHEN payment_method = 'Cheque'
                            THEN amount
                            ELSE 0
                        END
                    ),
                    0
                ) AS cheque_total,

                COALESCE(
                    SUM(
                        CASE
                            WHEN payment_method = 'Card'
                            THEN amount
                            ELSE 0
                        END
                    ),
                    0
                ) AS card_total

            FROM
                construction_account_financial_transactions

            WHERE
                {where_sql}
            """,
            tuple(params)
        )

        summary = (
            cursor.fetchone()
            or {}
        )

        summary.setdefault(
            "transaction_count",
            0
        )

        summary.setdefault(
            "total_paid",
            Decimal("0.00")
        )

        summary.setdefault(
            "petty_cash_total",
            Decimal("0.00")
        )

        summary.setdefault(
            "cheque_total",
            Decimal("0.00")
        )

        summary.setdefault(
            "card_total",
            Decimal("0.00")
        )

        total_transactions = int(
            summary.get(
                "transaction_count"
            )
            or 0
        )

        # =================================================
        # TOTAL PAGES
        # =================================================
        if total_transactions == 0:

            total_pages = 1

        else:

            total_pages = (
                total_transactions
                + page_size
                - 1
            ) // page_size

        if page > total_pages:
            page = total_pages

        if page < 1:
            page = 1

        # =================================================
        # OFFSET
        # =================================================
        offset = (
            page - 1
        ) * page_size

        transaction_params = list(
            params
        )

        transaction_params.extend(
            [
                offset,
                page_size
            ]
        )

        # =================================================
        # LOAD TRANSACTIONS
        # =================================================
        cursor.execute(
            f"""
            SELECT
                id,
                transaction_number,
                transaction_date,
                source_type,
                source_id,
                payment_method,
                project_name,
                supplier_or_recipient,
                amount,
                currency,
                payment_reference,
                transaction_by,
                transaction_by_name,
                payment_recorded_by,
                payment_recorded_by_name,
                payment_recorded_at,
                status,
                notes

            FROM
                construction_account_financial_transactions

            WHERE
                {where_sql}

            ORDER BY
                transaction_date DESC,
                id DESC

            LIMIT %s, %s
            """,
            tuple(transaction_params)
        )

        transactions = (
            cursor.fetchall()
            or []
        )

        # =================================================
        # PROJECT LIST
        # =================================================
        cursor.execute(
            """
            SELECT DISTINCT
                project_name

            FROM
                construction_account_financial_transactions

            WHERE
                project_name IS NOT NULL
                AND TRIM(project_name) <> ''

            ORDER BY
                project_name ASC
            """
        )

        projects = [
            row["project_name"]
            for row in (
                cursor.fetchall()
                or []
            )
            if row.get("project_name")
        ]

        # =================================================
        # SHOWING RANGE
        # =================================================
        if total_transactions > 0:

            showing_from = (
                offset + 1
            )

            showing_to = (
                offset
                + len(transactions)
            )

            if showing_to > total_transactions:

                showing_to = (
                    total_transactions
                )

        else:

            showing_from = 0
            showing_to = 0

        # =================================================
        # PAGINATION
        # =================================================
        pagination_pages = []

        if total_pages <= 7:

            pagination_pages = list(
                range(
                    1,
                    total_pages + 1
                )
            )

        else:

            pagination_pages.append(1)

            if page > 4:

                pagination_pages.append(
                    "..."
                )

            start_page = max(
                2,
                page - 1
            )

            end_page = min(
                total_pages - 1,
                page + 1
            )

            for page_number in range(
                start_page,
                end_page + 1
            ):

                pagination_pages.append(
                    page_number
                )

            if page < (
                total_pages - 3
            ):

                pagination_pages.append(
                    "..."
                )

            pagination_pages.append(
                total_pages
            )

        # =================================================
        # FINAL DEBUG
        # =================================================
        print("=================================================")
        print("ACCOUNT FINANCIAL SUMMARY")
        print(
            "Filter:",
            payment_method
        )
        print(
            "Total transactions:",
            total_transactions
        )
        print(
            "Total paid:",
            summary.get(
                "total_paid"
            )
        )
        print(
            "Card total:",
            summary.get(
                "card_total"
            )
        )
        print(
            "Cheque total:",
            summary.get(
                "cheque_total"
            )
        )
        print(
            "Petty Cash total:",
            summary.get(
                "petty_cash_total"
            )
        )
        print("=================================================")

        # =================================================
        # RENDER
        # =================================================
        return render_template(
            "construction_admin/"
            "construction_account_financial_summary.html",

            summary=summary,

            transactions=transactions,

            projects=projects,

            payment_method=payment_method,

            start_date=start_date,

            end_date=end_date,

            project=project,

            search=search,

            page=page,

            page_size=page_size,

            total_pages=total_pages,

            total_transactions=(
                total_transactions
            ),

            showing_from=showing_from,

            showing_to=showing_to,

            pagination_pages=(
                pagination_pages
            ),

            account_user_name=(
                current_user_name
            )
        )

    except Exception as e:

        # =================================================
        # ROLLBACK
        # =================================================
        if conn:

            try:
                conn.rollback()
            except Exception:
                pass

        # =================================================
        # ERROR LOG
        # =================================================
        print("=================================================")
        print(
            "ACCOUNT FINANCIAL SUMMARY ERROR"
        )
        print(
            type(e).__name__
        )
        print(
            repr(e)
        )
        print("=================================================")

        import traceback
        traceback.print_exc()

        flash(
            "Unable to load the Financial Summary.",
            "danger"
        )

        return redirect(
            url_for(
                "construction_account_dashboard"
            )
        )

    finally:

        if cursor:

            try:
                cursor.close()
            except Exception:
                pass

        if conn:

            try:
                conn.close()
            except Exception:
                pass


















# =====================================================
# END ROUTE
# =====================================================
if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=5003,
        debug=True
    )