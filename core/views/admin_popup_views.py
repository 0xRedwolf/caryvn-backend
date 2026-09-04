import logging
from rest_framework import views, status
from rest_framework.response import Response
from rest_framework.permissions import IsAdminUser
from rest_framework.parsers import MultiPartParser, FormParser, JSONParser

from ..models import PopupCard
from ..serializers import PopupCardSerializer
from ..services.cloudinary_service import cloudinary_service

logger = logging.getLogger(__name__)


class AdminPopupCardsView(views.APIView):
    """
    Admin API endpoint to list and create popup / banner ads.
    Supports JSON and multipart/form-data with automatic Cloudinary CDN uploads.
    """
    permission_classes = [IsAdminUser]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get(self, request):
        popups = PopupCard.objects.all().order_by('order', '-created_at')
        serializer = PopupCardSerializer(popups, many=True, context={'request': request})
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request):
        data = request.data.copy() if hasattr(request.data, 'copy') else dict(request.data)

        # Handle direct file upload if passed in multipart form
        if 'image' in request.FILES:
            image_file = request.FILES['image']
            upload_res = cloudinary_service.upload_image(image_file, folder='caryvn_ads')
            data['image'] = upload_res.get('url', '')

        serializer = PopupCardSerializer(data=data, context={'request': request})
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class AdminPopupCardDetailView(views.APIView):
    """
    Admin API endpoint to retrieve, update or delete a popup / banner ad.
    """
    permission_classes = [IsAdminUser]
    parser_classes = [JSONParser, MultiPartParser, FormParser]

    def get_object(self, popup_id):
        try:
            return PopupCard.objects.get(id=popup_id)
        except PopupCard.DoesNotExist:
            return None

    def get(self, request, popup_id):
        popup = self.get_object(popup_id)
        if not popup:
            return Response({'error': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
        serializer = PopupCardSerializer(popup, context={'request': request})
        return Response(serializer.data, status=status.HTTP_200_OK)

    def patch(self, request, popup_id):
        popup = self.get_object(popup_id)
        if not popup:
            return Response({'error': 'Not found'}, status=status.HTTP_404_NOT_FOUND)

        data = request.data.copy() if hasattr(request.data, 'copy') else dict(request.data)

        # Handle direct file upload if passed in multipart form
        if 'image' in request.FILES:
            image_file = request.FILES['image']
            upload_res = cloudinary_service.upload_image(image_file, folder='caryvn_ads')
            data['image'] = upload_res.get('url', '')

        serializer = PopupCardSerializer(popup, data=data, partial=True, context={'request': request})
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, popup_id):
        popup = self.get_object(popup_id)
        if not popup:
            return Response({'error': 'Not found'}, status=status.HTTP_404_NOT_FOUND)
        popup.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class AdminPopupImageUploadView(views.APIView):
    """
    Dedicated endpoint for instant image upload to Cloudinary CDN (folder: caryvn_ads).
    Returns WebP optimized CDN URL immediately for form preview.
    """
    permission_classes = [IsAdminUser]
    parser_classes = [MultiPartParser, FormParser]

    def post(self, request):
        file_obj = request.FILES.get('image') or request.FILES.get('file')
        if not file_obj:
            return Response({'error': 'No image file provided.'}, status=status.HTTP_400_BAD_REQUEST)

        # Validate file format
        allowed_types = ['image/jpeg', 'image/png', 'image/webp', 'image/gif', 'image/svg+xml']
        if hasattr(file_obj, 'content_type') and file_obj.content_type not in allowed_types:
            return Response({'error': 'Unsupported file type. Please upload a JPG, PNG, WebP, or SVG.'}, status=status.HTTP_400_BAD_REQUEST)

        # Max 10MB
        if file_obj.size > 10 * 1024 * 1024:
            return Response({'error': 'Image file size cannot exceed 10MB.'}, status=status.HTTP_400_BAD_REQUEST)

        try:
            res = cloudinary_service.upload_image(file_obj, folder='caryvn_ads')
            return Response(res, status=status.HTTP_200_OK)
        except Exception as e:
            logger.error(f"Cloudinary ad image upload error: {e}")
            return Response({'error': f'Upload failed: {str(e)}'}, status=status.HTTP_500_INTERNAL_SERVER_ERROR)


class AdminPopupToggleActiveView(views.APIView):
    """
    Quick 1-click toggle to activate or deactivate an ad directly from the list table.
    """
    permission_classes = [IsAdminUser]

    def post(self, request, popup_id):
        try:
            popup = PopupCard.objects.get(id=popup_id)
            popup.is_active = not popup.is_active
            popup.save(update_fields=['is_active'])
            return Response({
                'id': popup.id,
                'is_active': popup.is_active,
                'message': f"Ad '{popup.title or 'Card'}' is now {'Active' if popup.is_active else 'Inactive'}."
            }, status=status.HTTP_200_OK)
        except PopupCard.DoesNotExist:
            return Response({'error': 'Ad not found.'}, status=status.HTTP_404_NOT_FOUND)
