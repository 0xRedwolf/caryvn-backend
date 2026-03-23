from rest_framework import views, status
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated

from ..models import PopupCard
from ..serializers import PopupCardSerializer

class ActivePopupCardsView(views.APIView):
    """
    API endpoint that allows fetching all active popup cards.
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        # Fetch active cards ordered by their 'order' field and creation date
        popups = PopupCard.objects.filter(is_active=True).order_by('order', '-created_at')
        serializer = PopupCardSerializer(popups, many=True, context={'request': request})
        return Response(serializer.data, status=status.HTTP_200_OK)
