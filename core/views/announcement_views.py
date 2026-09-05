from rest_framework import views, status
from rest_framework.response import Response
from rest_framework.permissions import AllowAny, IsAdminUser
import logging

from ..models import Announcement
from ..serializers import AnnouncementSerializer

logger = logging.getLogger(__name__)


class AnnouncementListView(views.APIView):
    """
    Public / user endpoint to fetch all active announcements for the ticker bar.
    Ordered by sort_order ascending, then newest first.
    """
    permission_classes = [AllowAny]

    def get(self, request):
        announcements = Announcement.objects.filter(is_active=True).order_by('sort_order', '-created_at')
        serializer = AnnouncementSerializer(announcements, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)


class AdminAnnouncementListCreateView(views.APIView):
    """
    Admin endpoint to list all announcements (active and inactive) or create a new one.
    """
    permission_classes = [IsAdminUser]

    def get(self, request):
        announcements = Announcement.objects.all().order_by('sort_order', '-created_at')
        serializer = AnnouncementSerializer(announcements, many=True)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def post(self, request):
        serializer = AnnouncementSerializer(data=request.data)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_201_CREATED)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)


class AdminAnnouncementDetailView(views.APIView):
    """
    Admin endpoint to retrieve, update, or delete an announcement.
    """
    permission_classes = [IsAdminUser]

    def get_object(self, pk):
        try:
            return Announcement.objects.get(pk=pk)
        except Announcement.DoesNotExist:
            return None

    def get(self, request, pk):
        announcement = self.get_object(pk)
        if not announcement:
            return Response({'error': 'Announcement not found'}, status=status.HTTP_404_NOT_FOUND)
        serializer = AnnouncementSerializer(announcement)
        return Response(serializer.data, status=status.HTTP_200_OK)

    def patch(self, request, pk):
        announcement = self.get_object(pk)
        if not announcement:
            return Response({'error': 'Announcement not found'}, status=status.HTTP_404_NOT_FOUND)
        serializer = AnnouncementSerializer(announcement, data=request.data, partial=True)
        if serializer.is_valid():
            serializer.save()
            return Response(serializer.data, status=status.HTTP_200_OK)
        return Response(serializer.errors, status=status.HTTP_400_BAD_REQUEST)

    def delete(self, request, pk):
        announcement = self.get_object(pk)
        if not announcement:
            return Response({'error': 'Announcement not found'}, status=status.HTTP_404_NOT_FOUND)
        announcement.delete()
        return Response(status=status.HTTP_204_NO_CONTENT)


class AdminAnnouncementToggleView(views.APIView):
    """
    Admin endpoint to quickly toggle an announcement's active state.
    """
    permission_classes = [IsAdminUser]

    def post(self, request, pk):
        try:
            announcement = Announcement.objects.get(pk=pk)
            announcement.is_active = not announcement.is_active
            announcement.save(update_fields=['is_active', 'updated_at'])
            return Response({
                'id': announcement.id,
                'is_active': announcement.is_active,
                'message': f"Announcement is now {'Active' if announcement.is_active else 'Inactive'}."
            }, status=status.HTTP_200_OK)
        except Announcement.DoesNotExist:
            return Response({'error': 'Announcement not found.'}, status=status.HTTP_404_NOT_FOUND)
