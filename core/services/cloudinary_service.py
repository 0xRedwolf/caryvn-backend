"""
Cloudinary Image Hosting & Upload Service for Caryvn.
Handles uploading blog featured images, author avatars, and inline content assets.
Gracefully falls back to local media storage if Cloudinary credentials are not configured.
"""
import os
import uuid
import logging
from django.conf import settings
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile

logger = logging.getLogger(__name__)

try:
    import cloudinary
    import cloudinary.uploader
    CLOUDINARY_AVAILABLE = True
except ImportError:
    CLOUDINARY_AVAILABLE = False


class CloudinaryService:
    """Service to upload and manage media on Cloudinary CDN."""

    def __init__(self):
        self.cloud_name = getattr(settings, 'CLOUDINARY_CLOUD_NAME', '')
        self.api_key = getattr(settings, 'CLOUDINARY_API_KEY', '')
        self.api_secret = getattr(settings, 'CLOUDINARY_API_SECRET', '')

        if self.is_configured and CLOUDINARY_AVAILABLE:
            cloudinary.config(
                cloud_name=self.cloud_name,
                api_key=self.api_key,
                api_secret=self.api_secret,
                secure=True,
            )

    @property
    def is_configured(self) -> bool:
        """Returns True if Cloudinary credentials are provided in settings."""
        return bool(self.cloud_name and self.api_key and self.api_secret)

    def upload_image(self, file_obj, folder: str = 'caryvn_blog') -> dict:
        """
        Upload an image file object to Cloudinary.
        If Cloudinary is not configured or unavailable, stores locally in media/ folder.

        Returns:
            dict containing:
                - url: public secure URL
                - public_id: Cloudinary public_id or local filename
                - is_cloud: bool indicating whether stored on Cloudinary CDN
        """
        # If Cloudinary is configured and installed, upload to CDN
        if self.is_configured and CLOUDINARY_AVAILABLE:
            try:
                upload_res = cloudinary.uploader.upload(
                    file_obj,
                    folder=folder,
                    use_filename=True,
                    unique_filename=True,
                    resource_type='image',
                    format='webp',  # Automatic WebP conversion for maximum SEO speed
                )
                secure_url = upload_res.get('secure_url') or upload_res.get('url')
                return {
                    'url': secure_url,
                    'public_id': upload_res.get('public_id', ''),
                    'format': upload_res.get('format', 'webp'),
                    'bytes': upload_res.get('bytes', 0),
                    'is_cloud': True,
                }
            except Exception as e:
                logger.error(f'Cloudinary upload failed, falling back to local storage: {e}')

        # Fallback to local media storage
        ext = os.path.splitext(getattr(file_obj, 'name', 'image.jpg'))[1].lower() or '.jpg'
        filename = f'blog/{uuid.uuid4().hex}{ext}'
        saved_path = default_storage.save(filename, ContentFile(file_obj.read()))
        local_url = f'{settings.MEDIA_URL.rstrip("/")}/{saved_path.lstrip("/")}'

        return {
            'url': local_url,
            'public_id': saved_path,
            'is_cloud': False,
        }


cloudinary_service = CloudinaryService()
