import logging
from functools import lru_cache
from io import BytesIO
from os import getenv
from uuid import uuid4

from minio import Minio
from minio.error import MinioException, S3Error
from PIL import Image, ImageOps, UnidentifiedImageError
from urllib3 import PoolManager, Timeout
from urllib3.exceptions import HTTPError

from backend.pipeline.results import StorageUnavailable


BUCKET = "pult-avatars"
MAX_UPLOAD_BYTES = 4 * 1024 * 1024
FORMATS = ("JPEG", "PNG", "WEBP")
logger = logging.getLogger(__name__)


@lru_cache(maxsize=1)
def storage() -> Minio:
    endpoint = getenv("PULT_MINIO_ENDPOINT")
    if not endpoint:
        raise StorageUnavailable("Для загрузки аватаров подключите MinIO.")
    return Minio(
        endpoint, access_key=getenv("MINIO_ROOT_USER"), secret_key=getenv("MINIO_ROOT_PASSWORD"),
        secure=getenv("PULT_MINIO_SECURE") == "1", region="us-east-1",
        http_client=PoolManager(timeout=Timeout(connect=3, read=5), retries=False),
    )


def normalize_avatar(data: bytes) -> bytes:
    try:
        with Image.open(BytesIO(data), formats=FORMATS) as image:
            if image.width * image.height > 16_000_000 or getattr(image, "is_animated", False):
                raise ValueError
            rgb = ImageOps.exif_transpose(image).convert("RGB")
            square = ImageOps.fit(rgb, (256, 256), method=Image.Resampling.LANCZOS)
            result = BytesIO()
            square.save(result, format="WEBP", quality=88)
            return result.getvalue()
    except (UnidentifiedImageError, Image.DecompressionBombError, OSError, ValueError) as error:
        raise ValueError("Выберите обычный JPEG, PNG или WebP размером до 16 мегапикселей.") from error


def upload_avatar(data: bytes) -> str:
    client = storage()
    avatar_id = uuid4().hex
    try:
        if not client.bucket_exists(BUCKET):
            try:
                client.make_bucket(BUCKET)
            except S3Error as error:
                if error.code != "BucketAlreadyOwnedByYou":
                    raise
        client.put_object(BUCKET, avatar_id, BytesIO(data), len(data), content_type="image/webp")
    except (MinioException, HTTPError, OSError) as error:
        raise StorageUnavailable("Хранилище аватаров недоступно. Попробуйте позже.") from error
    return avatar_id


def read_avatar(avatar_id: str) -> bytes | None:
    try:
        result = storage().get_object(BUCKET, avatar_id)
        try:
            return result.read(MAX_UPLOAD_BYTES)
        finally:
            result.close()
            result.release_conn()
    except S3Error as error:
        if error.code in {"NoSuchKey", "NoSuchBucket"}:
            return None
        raise StorageUnavailable("Хранилище аватаров недоступно.") from error
    except (MinioException, HTTPError, OSError) as error:
        raise StorageUnavailable("Хранилище аватаров недоступно.") from error


def remove_avatar(avatar_id: str | None) -> None:
    if not avatar_id:
        return
    try:
        storage().remove_object(BUCKET, avatar_id)
    except (StorageUnavailable, MinioException, HTTPError, OSError):
        logger.warning("Не удалось удалить предыдущий объект аватара", exc_info=True)
