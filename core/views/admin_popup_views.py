from rest_framework import views, status
from rest_framework.response import Response
from rest_framework.permissions import IsAdminUser

from ..models import PopupCard
from ..serializers import PopupCardSerializer

class AdminPopupCardsView(views.APIView):
    """
    Admin API endpoint to list and create popup cards.
    """
    permission_classes = [IsAdminUser]

    def get(self, request):
        popups = PopupCard.objects.all()
        serializer = PopupCardSerializer(popups, many=True, context={'request': request})
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request):
        # We might receive multipart/form-data if uploading an image
        serializer = PopupCardSerializer(data=request.data, context={'request': request})
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

class AdminPopupCardDetailView(views.APIView):
    """
    Admin API endpoint to retrieve, update or delete a popup card.
    """
    permission_classes = [IsAdminUser]

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
        serializer = PopupCardSerializer(popup, data=request.data, partial=True, context={'request': request})
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
