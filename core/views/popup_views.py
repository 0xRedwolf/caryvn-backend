from rest_framework import views, status
from rest_framework.response import Response
from rest_framework.permissions import IsAuthenticated, AllowAny
from django.db.models import F

from ..models import PopupCard
from ..serializers import PopupCardSerializer


class ActivePopupCardsView(views.APIView):
    """
    API endpoint that allows fetching all active popup cards or dashboard banners.
    Optional query parameter:
      ?placement=POPUP (default for modals)
      ?placement=BANNER (for in-feed dashboard banners)
    """
    permission_classes = [IsAuthenticated]

    def get(self, request):
        queryset = PopupCard.objects.filter(is_active=True)

        placement = request.query_params.get('placement')
        if placement:
            queryset = queryset.filter(placement_type=placement.upper())

        popups = queryset.order_by('order', '-created_at')
        serializer = PopupCardSerializer(popups, many=True, context={'request': request})
        return Response(serializer.data, status=status.HTTP_200_OK)


class TrackPopupImpressionView(views.APIView):
    """
    Increments the impressions count atomically when an ad/popup is displayed to a user.
    """
    permission_classes = [AllowAny]

    def post(self, request, popup_id):
        updated = PopupCard.objects.filter(id=popup_id).update(impressions_count=F('impressions_count') + 1)
        if updated:
            return Response({'status': 'ok'}, status=status.HTTP_200_OK)
        return Response({'error': 'Ad not found'}, status=status.HTTP_404_NOT_FOUND)


class TrackPopupClickView(views.APIView):
    """
    Increments the clicks count atomically when a user clicks the action CTA button.
    """
    permission_classes = [AllowAny]

    def post(self, request, popup_id):
        updated = PopupCard.objects.filter(id=popup_id).update(clicks_count=F('clicks_count') + 1)
        if updated:
            return Response({'status': 'ok'}, status=status.HTTP_200_OK)
        return Response({'error': 'Ad not found'}, status=status.HTTP_404_NOT_FOUND)
