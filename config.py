import os


BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DATA_DIR", BASE_DIR)
DEFAULT_DB_PATH = os.path.join(DATA_DIR, "village.db")
LEGACY_VOTE_DB_PATH = os.path.join(DATA_DIR, "votes.db")
DB_PATH = os.environ.get(
    "DB_PATH",
    LEGACY_VOTE_DB_PATH
    if os.path.isfile(LEGACY_VOTE_DB_PATH) and not os.path.exists(DEFAULT_DB_PATH)
    else DEFAULT_DB_PATH,
)
UPLOADS_DIR = os.environ.get("UPLOAD_DIR", os.path.join(DATA_DIR, "uploads"))
THUMBNAILS_DIR = os.path.join(UPLOADS_DIR, "thumbnails")
ORG_NAME = os.environ.get("ORG_NAME", "శేషంపల్లి")
ADMIN_USER = os.environ.get("ADMIN_USER", "").strip().lower()
ADMIN_PASSWORD = os.environ.get("ADMIN_PASSWORD", "")
SECRET_KEY = os.environ.get("SECRET_KEY", "")
APP_ENV = os.environ.get("APP_ENV", os.environ.get("FLASK_ENV", "development")).strip().lower()
IS_PRODUCTION = APP_ENV == "production" or os.environ.get("RENDER", "").lower() == "true"
if IS_PRODUCTION:
    missing = [
        name for name, value in (
            ("ADMIN_USER", ADMIN_USER),
            ("ADMIN_PASSWORD", ADMIN_PASSWORD),
            ("SECRET_KEY", SECRET_KEY),
        ) if not value
    ]
    if missing:
        raise RuntimeError(
            "Production configuration is missing required environment variables: "
            + ", ".join(missing)
        )
    if len(SECRET_KEY) < 32:
        raise RuntimeError("Production SECRET_KEY must contain at least 32 characters.")
if bool(ADMIN_USER) != bool(ADMIN_PASSWORD):
    raise RuntimeError("Set both ADMIN_USER and ADMIN_PASSWORD, or neither.")
if ADMIN_PASSWORD and len(ADMIN_PASSWORD) < 12:
    raise RuntimeError("ADMIN_PASSWORD must contain at least 12 characters.")
CONTACT_PHONE = os.environ.get("CONTACT_PHONE", "")
WHATSAPP_NUMBER = os.environ.get("WHATSAPP_NUMBER", "")
WHATSAPP_GROUP_LINK = os.environ.get("WHATSAPP_GROUP_LINK", "")
UPI_ID = os.environ.get("UPI_ID", "")
VILLAGE_ADDRESS = os.environ.get("VILLAGE_ADDRESS", "")
HTTPS = IS_PRODUCTION or os.environ.get("BASE_URL", "").startswith("https://")
TZ_HOURS = float(os.environ.get("TZ_HOURS", "5.5"))
REQUIRE_PHONE_FOR_VOTE = os.environ.get("REQUIRE_PHONE_FOR_VOTE", "").strip().lower() in ("1", "true", "yes", "on")
