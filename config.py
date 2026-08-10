import os


class Config:

    # Security
    SECRET_KEY = os.environ.get("SECRET_KEY")


    # File Uploads
    UPLOAD_FOLDER = os.path.join(
        "static",
        "uploads"
    )

    MAX_CONTENT_LENGTH = 16 * 1024 * 1024



    # Database

    DB_USER = os.environ.get("DB_USER")

    DB_PASSWORD = os.environ.get("DB_PASSWORD")

    DB_HOST = os.environ.get(
        "DB_HOST",
        "railway"
    )

    DB_PORT = os.environ.get(
        "DB_PORT",
        "3306"
    )

    DB_NAME = os.environ.get(
        "DB_NAME"
    )



    SQLALCHEMY_DATABASE_URI = (
        f"mysql+pymysql://{DB_USER}:{DB_PASSWORD}"
        f"@{DB_HOST}:{DB_PORT}/{DB_NAME}"
    )


    SQLALCHEMY_TRACK_MODIFICATIONS = False



    # Company Information

    DEFAULT_CURRENCY = "QAR"

    COMPANY_NAME = "Prestigious Real Estate"

    CONTACT_EMAIL = "santospederson@gmail.com"

    CONTACT_PHONE = "+974 4400 1234"

    HEADQUARTERS = "Doha, Qatar"