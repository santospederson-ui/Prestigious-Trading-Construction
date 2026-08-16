import os
import uuid
from flask import Flask, render_template, redirect, url_for, flash, session, request, jsonify
from werkzeug.security import check_password_hash

from werkzeug.utils import secure_filename
import mysql.connector

from datetime import datetime, timedelta
import requests

from io import BytesIO


import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

from flask import Response, send_from_directory
from dotenv import load_dotenv

import cloudinary
import cloudinary.uploader


load_dotenv()




app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY", "super-secret-key")

app.config["PERMANENT_SESSION_LIFETIME"] = timedelta(minutes=30)





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

    pages.append(url_for("home", _external=True))
    pages.append(url_for("about", _external=True))
    pages.append(url_for("properties", _external=True))
    pages.append(url_for("services", _external=True))
    pages.append(url_for("locations", _external=True))
    pages.append(url_for("contact", _external=True))
    pages.append(url_for("find_property", _external=True))



    # =========================
    # PROPERTY DETAILS PAGES
    # =========================

    conn = get_db_connection()

    cursor = conn.cursor(dictionary=True)


    cursor.execute("""
        SELECT id
        FROM properties
        ORDER BY created_at DESC
    """)


    properties = cursor.fetchall()



    for property in properties:

        pages.append(
            url_for(
                "property_details",
                id=property["id"],
                _external=True
            )
        )



    cursor.close()
    conn.close()



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

        "Prestigious Trading & Construction Email Test",

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
                "Prestigious Trading & Construction"
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

                        Prestigious Trading & Construction

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
                        Construction QID Expiry Monitoring System.

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

import threading
import os
from flask import jsonify, request

@app.route("/run-qid-monitoring", methods=["GET"])
def run_qid_monitoring():

    # =====================================================
    # 1. GET & VALIDATE SECRETS
    # =====================================================
    scheduler_key = os.getenv("QID_MONITORING_SECRET", "").strip()
    request_key = request.args.get("key", "").strip()

    if not scheduler_key:
        print("QID MONITORING ERROR: QID_MONITORING_SECRET is not configured.")
        return jsonify({"success": False, "error": "Secret not configured"}), 500

    if not request_key or request_key != scheduler_key:
        print("QID MONITORING BLOCKED: Invalid or missing scheduler key.")
        return jsonify({"success": False, "error": "Unauthorized"}), 401


    # =====================================================
    # 2. RUN MONITORING IN BACKGROUND THREAD
    # (Prevents Render 50-Second Timeout & Large Response Errors)
    # =====================================================
    def execute_monitoring():
        print("\n=========================================================")
        print("AUTOMATIC QID MONITORING STARTED")
        print("=========================================================")
        try:
            results = run_qid_expiry_monitoring()
            print("AUTOMATIC QID MONITORING FINISHED")
            print("Results:", results)
        except Exception as e:
            print("AUTOMATIC QID MONITORING ERROR")
            print("Error Type:", type(e).__name__)
            print("Error:", str(e))
        print("=========================================================\n")

    # Start task asynchronously
    thread = threading.Thread(target=execute_monitoring)
    thread.daemon = True
    thread.start()


    # =====================================================
    # 3. IMMEDIATE MINIMAL RESPONSE FOR CRON SERVICE
    # =====================================================
    return jsonify({
        "success": True,
        "message": "Monitoring task dispatched successfully"
    }), 200






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
        FROM properties
        ORDER BY id DESC
        LIMIT 12
    """)

    properties = cursor.fetchall()

    cursor.close()
    conn.close()

    return render_template(
        "home.html",
        properties=properties
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

                "New Website Enquiry - Prestigious Trading & Construction",

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
                    Sent from Prestigious Trading & Construction Website
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

                "Thank You for Contacting Prestigious Trading & Construction",

                f"""
                <h2>Hello {fullname},</h2>

                <p>
                    Thank you for contacting
                    <b>Prestigious Trading & Construction</b>.
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
                    <b>Prestigious Trading & Construction Team</b>
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





# =====================================================
# CONSTRUCTION ADMIN LOGIN ROUTE
# =====================================================
@app.route("/admin/login", methods=["GET", "POST"])
def admin_login():

    if "construction_admin_id" in session:
        return redirect(url_for("admin_dashboard"))

    if request.method == "POST":

        username = request.form.get("username", "").strip()
        password = request.form.get("password", "")

        if not username or not password:

            flash(
                "Please enter your username and password.",
                "danger"
            )

            return render_template("construction_admin/login.html")

        conn = None
        cursor = None

        try:

            conn = get_db_connection()

            cursor = conn.cursor(dictionary=True)

            cursor.execute(
                """
                SELECT
                    id,
                    fullname,
                    username,
                    email,
                    password,
                    role
                FROM construction_admins
                WHERE username=%s
                LIMIT 1
                """,
                (username,)
            )

            admin = cursor.fetchone()

            if admin and check_password_hash(
                admin["password"],
                password
            ):

                session.permanent = True

                session["construction_admin_id"] = admin["id"]
                session["construction_admin_name"] = admin["fullname"]
                session["construction_admin_username"] = admin["username"]
                session["construction_admin_role"] = admin["role"]

                session["construction_last_activity"] = (
                    datetime.utcnow().isoformat()
                )

                return redirect(
                    url_for("admin_dashboard")
                )

            flash(
                "Invalid username or password.",
                "danger"
            )

        except Exception as e:

            print(
                "CONSTRUCTION ADMIN LOGIN ERROR:",
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



# =====================================================
# CONSTRUCTION ADMIN LOGOUT
# =====================================================
@app.route("/admin/logout")
def admin_logout():

    session.clear()

    flash(
        "You have been logged out successfully.",
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
        # Your actual table uses:
        # is_read = 0  -> UNREAD
        # is_read = 1  -> READ
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
        #
        # IMPORTANT:
        # Your actual table columns are:
        #
        # fullname
        # email
        # phone
        # subject
        # message
        # created_at
        # is_read
        #
        # So we use those exact columns.
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
                ""
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

                result = (
                    cloudinary.uploader.upload(
                        image,
                        folder=
                        "prestigious_construction/projects"
                    )
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
                the Prestigious Trading & Construction website.
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
                <b>Prestigious Trading & Construction</b>.
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
                Prestigious Trading & Construction
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












# =====================================================
# END ROUTE
# =====================================================
if __name__ == "__main__":

    app.run(
        host="0.0.0.0",
        port=5003,
        debug=True
    )
