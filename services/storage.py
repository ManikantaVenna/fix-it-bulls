from io import BytesIO
from pathlib import Path
from uuid import uuid4

from flask import current_app
from PIL import Image, ImageOps, UnidentifiedImageError

from db import get_db

MAX_INPUT_BYTES = 5 * 1024 * 1024
MAX_OUTPUT_BYTES = 2 * 1024 * 1024
MAX_PIXELS = 20_000_000


def save_photo(user_id, upload):
    db = get_db()
    if db.execute("SELECT id FROM users WHERE id=?", (user_id,)).fetchone() is None:
        raise LookupError("Photo owner not found")
    raw = upload.read(MAX_INPUT_BYTES + 1)
    if len(raw) > MAX_INPUT_BYTES:
        raise ValueError("Image size exceeds 5 MB")
    try:
        with Image.open(BytesIO(raw)) as original:
            if original.format not in {"JPEG", "PNG", "WEBP"} or original.width * original.height > MAX_PIXELS:
                raise ValueError("Unsupported image type or dimensions")
            original.load()
            image = ImageOps.exif_transpose(original).convert("RGBA")
            background = Image.new("RGB", image.size, "white")
            background.paste(image, mask=image.getchannel("A"))
            background.thumbnail((1800, 1800))
            encoded = BytesIO()
            background.save(encoded, format="JPEG", quality=82, optimize=True)
            data = encoded.getvalue()
    except (UnidentifiedImageError, OSError, Image.DecompressionBombError) as error:
        raise ValueError("Invalid image; use a valid JPEG, PNG or WebP") from error
    if len(data) > MAX_OUTPUT_BYTES:
        raise ValueError("Processed image size exceeds 2 MB")
    photo_id = uuid4().hex
    storage_name = photo_id + ".jpg"
    path = Path(current_app.config["UPLOAD_FOLDER"]) / storage_name
    path.write_bytes(data)
    try:
        db.execute(
            "INSERT INTO photos(id,user_id,storage_name,mime_type,size_bytes) VALUES(?,?,?,?,?)",
            (photo_id, user_id, storage_name, "image/jpeg", len(data)),
        )
        db.commit()
    except Exception:
        db.rollback()
        path.unlink(missing_ok=True)
        raise
    return photo_id


def read_photo(user_id, photo_id):
    row = get_db().execute("SELECT storage_name FROM photos WHERE id=? AND user_id=?", (photo_id, user_id)).fetchone()
    if row is None:
        raise LookupError("Photo not found")
    return (Path(current_app.config["UPLOAD_FOLDER"]) / row["storage_name"]).read_bytes()
